/*
 * Copyright 2010-2016 OpenXcom Developers.
 * Copyright 2023-2026 XComCoopTeam (https://www.moddb.com/mods/openxcom-coop-mod)
 *
 * This file is part of OpenXcom.
 *
 * OpenXcom is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * OpenXcom is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with OpenXcom.  If not, see <http://www.gnu.org/licenses/>.
 */
#include "TestServer.h"

#include <cstdlib>
#include <typeinfo>

#include <json/json.h>
#include <SDL_net.h>

#include "../Engine/Game.h"
#include "../Engine/Logger.h"
#include "../Engine/Options.h"
#include "../Engine/State.h"
#include "../Geoscape/GeoscapeState.h"
#include "../Battlescape/BattlescapeState.h"
#include "../Savegame/Base.h"
#include "../Savegame/Country.h"
#include "../Savegame/Craft.h"
#include "../Savegame/SavedBattleGame.h"
#include "../Savegame/SavedGame.h"
#include "../Savegame/Soldier.h"
#include "../Menu/NewGameState.h"
#include "../Menu/StartState.h"
#include "../Geoscape/BuildNewBaseState.h"
#include "../Geoscape/BaseNameState.h"
#include "../Basescape/BasescapeState.h"
#include "../Basescape/SoldiersState.h"
#include "CoopState.h"
#include "LobbyMenu.h"
#include "Profile.h"
#include "TransferNoticeState.h"
#include "TransferSoldierMenu.h"
#include "connectionTCP.h"

namespace OpenXcom
{

TestServer& TestServer::instance()
{
	static TestServer s;
	return s;
}

void TestServer::startFromEnvironment(Game* game)
{
	if (_running.load())
	{
		return;
	}
	const char* portStr = std::getenv("OXC_TEST_PORT");
	if (!portStr)
	{
		return;
	}
	int port = std::atoi(portStr);
	if (port <= 0 || port > 65535)
	{
		return;
	}
	_game = game;
	_running.store(true);
	_thread = std::thread(&TestServer::ioThread, this, port);
	Log(LOG_INFO) << "[testserver] listening on 127.0.0.1:" << port;
}

void TestServer::stop()
{
	_running.store(false);
	if (_thread.joinable())
	{
		_thread.join();
	}
}

void TestServer::ioThread(int port)
{
	if (SDLNet_Init() != 0)
	{
		Log(LOG_ERROR) << "[testserver] SDLNet_Init failed: " << SDLNet_GetError();
		return;
	}
	IPaddress ip;
	// NULL host = listen (SDL_net semantics; a concrete address would mean
	// an outbound connect). Test-only server, gated by OXC_TEST_PORT.
	if (SDLNet_ResolveHost(&ip, nullptr, (Uint16)port) != 0)
	{
		Log(LOG_ERROR) << "[testserver] resolve failed: " << SDLNet_GetError();
		return;
	}
	TCPsocket listening = SDLNet_TCP_Open(&ip);
	if (!listening)
	{
		Log(LOG_ERROR) << "[testserver] open failed: " << SDLNet_GetError();
		return;
	}
	SDLNet_SocketSet set = SDLNet_AllocSocketSet(2);
	SDLNet_TCP_AddSocket(set, listening);

	TCPsocket client = nullptr;
	std::string recvBuf;

	while (_running.load())
	{
		SDLNet_CheckSockets(set, 50);

		if (TCPsocket fresh = SDLNet_TCP_Accept(listening))
		{
			if (!client)
			{
				client = fresh;
				SDLNet_TCP_AddSocket(set, client);
			}
			else
			{
				SDLNet_TCP_Close(fresh);
			}
		}

		if (client && SDLNet_SocketReady(client))
		{
			char buf[4096];
			int n = SDLNet_TCP_Recv(client, buf, sizeof(buf));
			if (n <= 0)
			{
				SDLNet_TCP_DelSocket(set, client);
				SDLNet_TCP_Close(client);
				client = nullptr;
				recvBuf.clear();
			}
			else
			{
				recvBuf.append(buf, n);
				size_t pos;
				while ((pos = recvBuf.find('\n')) != std::string::npos)
				{
					std::string line = recvBuf.substr(0, pos);
					recvBuf.erase(0, pos + 1);
					if (!line.empty() && line.back() == '\r')
					{
						line.pop_back();
					}
					if (!line.empty())
					{
						std::lock_guard<std::mutex> lock(_mutex);
						_inbox.push_back(line);
					}
				}
			}
		}

		// Flush responses.
		if (client)
		{
			std::deque<std::string> out;
			{
				std::lock_guard<std::mutex> lock(_mutex);
				out.swap(_outbox);
			}
			for (auto& resp : out)
			{
				resp += '\n';
				int sent = 0;
				int len = (int)resp.size();
				while (sent < len)
				{
					int n = SDLNet_TCP_Send(client, resp.data() + sent, len - sent);
					if (n <= 0)
					{
						break;
					}
					sent += n;
				}
			}
		}
	}

	if (client)
	{
		SDLNet_TCP_Close(client);
	}
	SDLNet_TCP_Close(listening);
	SDLNet_FreeSocketSet(set);
}

void TestServer::pump()
{
	if (!_running.load())
	{
		return;
	}
	// While StartState is on the stack the mod is still being loaded on its
	// worker thread; executing commands now races it (e.g. GeoscapeState
	// needs surfaces that modResources() synthesizes at the very end of the
	// load). Leave commands queued until loading finishes.
	for (auto* s : _game->getStates())
	{
		if (dynamic_cast<StartState*>(s))
		{
			return;
		}
	}
	for (;;)
	{
		std::string line;
		{
			std::lock_guard<std::mutex> lock(_mutex);
			if (_inbox.empty())
			{
				break;
			}
			line = std::move(_inbox.front());
			_inbox.pop_front();
		}
		std::string resp;
		try
		{
			resp = execute(line);
		}
		catch (const std::exception& e)
		{
			Json::Value err;
			err["ok"] = false;
			err["error"] = std::string("exception: ") + e.what();
			Json::FastWriter w;
			resp = w.write(err);
			if (!resp.empty() && resp.back() == '\n') resp.pop_back();
		}
		{
			std::lock_guard<std::mutex> lock(_mutex);
			_outbox.push_back(resp);
		}
	}
}

static Json::Value soldierToJson(Soldier* s)
{
	Json::Value j;
	j["id"] = s->getId();
	j["name"] = s->getName();
	j["owner"] = s->getOwnerPlayerId();
	j["coop"] = s->getCoop();
	j["coopBase"] = s->getCoopBase();
	j["craft"] = s->getCraft() ? s->getCraft()->getType() : "";
	j["dead"] = s->getDeath() != nullptr;
	return j;
}

std::string TestServer::execute(const std::string& line)
{
	Json::Value req;
	Json::Value resp;
	resp["ok"] = false;

	Json::CharReaderBuilder rb;
	std::unique_ptr<Json::CharReader> reader(rb.newCharReader());
	std::string errs;
	if (!reader->parse(line.data(), line.data() + line.size(), &req, &errs))
	{
		resp["error"] = "bad json: " + errs;
	}
	else
	{
		std::string cmd = req.get("cmd", "").asString();
		connectionTCP* coop = _game->getCoopMod();

		if (cmd == "ping")
		{
			resp["ok"] = true;
			resp["pong"] = true;
		}
		else if (cmd == "quit")
		{
			_game->quit();
			resp["ok"] = true;
		}
		else if (cmd == "get_state")
		{
			Json::Value states(Json::arrayValue);
			for (auto* s : _game->getStates())
			{
				states.append(typeid(*s).name());
			}
			resp["states"] = states;
			resp["ok"] = true;
		}
		else if (cmd == "get_coop")
		{
			resp["coopStatic"] = coop->getCoopStatic();
			resp["coopCampaign"] = coop->getCoopCampaign();
			resp["host"] = connectionTCP::getHost();
			resp["serverOwner"] = connectionTCP::getServerOwner();
			resp["onConnect"] = coop->isConnected();
			resp["sessionLocked"] = connectionTCP::isCoopSessionLocked;
			resp["playerReady"] = connectionTCP::isPlayerReady;
			resp["playersReady"] = connectionTCP::isPlayersReady;
			resp["lobbyClosed"] = connectionTCP::isLobbyMenuClosed;
			resp["lobbyFileStatus"] = connectionTCP::LobbyFileStatus;
			resp["coopSession"] = coop->isCoopSession();
			resp["hasSave"] = _game->getSavedGame() != nullptr;
			resp["inBattle"] = _game->getSavedGame() && _game->getSavedGame()->getSavedBattle();
			resp["hostName"] = coop->getHostName();
			resp["clientName"] = coop->getCurrentClientName();
			resp["insideCoopBase"] = coop->playerInsideCoopBase;
			resp["saveID"] = Json::Value::Int64(connectionTCP::saveID);
			resp["ok"] = true;
		}
		else if (cmd == "load_save")
		{
			std::string file = req.get("file", "").asString();
			SavedGame* s = new SavedGame();
			s->load(file, _game->getMod(), _game->getLanguage());
			coop->resetTransferSessionState();
			_game->setSavedGame(s);
			_game->setState(new GeoscapeState);
			resp["ok"] = true;
		}
		else if (cmd == "host_tcp")
		{
			std::string server = req.get("server", "TestServer").asString();
			std::string port = req.get("port", "3000").asString();
			std::string player = req.get("player", "HostPlayer").asString();

			connectionTCP::password = "";
			connectionTCP::isPasswordRequired = false;
			connectionTCP::_coopGamemode = 1; // PVE
			coop->setCoopSession(false);
			coop->setPlayerTurn(3);
			coop->setHostName(player);
			// campaign when a real campaign save is loaded (same check as HostMenu)
			bool campaign = _game->getSavedGame() && !_game->getSavedGame()->getCountries()->empty();
			coop->setCoopCampaign(campaign);
			coop->hostTCPServer(server, port);
			coop->setServerOwner(true);
			if (Options::HostSaveProgress && campaign)
			{
				_game->pushState(new LobbyMenu());
			}
			resp["campaign"] = campaign;
			resp["ok"] = true;
		}
		else if (cmd == "join_tcp")
		{
			std::string ipaddr = req.get("ip", "127.0.0.1").asString();
			std::string port = req.get("port", "3000").asString();
			std::string player = req.get("player", "ClientPlayer").asString();

			coop->setCoopSession(false);
			coop->setPlayerTurn(3);
			coop->setHostName(player);
			bool campaign = _game->getSavedGame() && !_game->getSavedGame()->getCountries()->empty();
			coop->setCoopCampaign(campaign);
			coop->connectTCPServer(ipaddr, port);
			resp["ok"] = true;
		}
		else if (cmd == "profile_ok")
		{
			Profile* profile = nullptr;
			for (auto* s : _game->getStates())
			{
				if (auto* p = dynamic_cast<Profile*>(s))
				{
					profile = p;
				}
			}
			if (profile)
			{
				profile->buttonOK(nullptr);
				resp["ok"] = true;
			}
			else
			{
				resp["error"] = "no Profile in state stack";
			}
		}
		else if (cmd == "open_new_game")
		{
			_game->pushState(new NewGameState);
			resp["ok"] = true;
		}
		else if (cmd == "newgame_ok")
		{
			NewGameState* ng = nullptr;
			for (auto* s : _game->getStates())
			{
				if (auto* n = dynamic_cast<NewGameState*>(s))
				{
					ng = n;
				}
			}
			if (ng)
			{
				ng->btnOkClick(nullptr);
				resp["ok"] = true;
			}
			else
			{
				resp["error"] = "no NewGameState in state stack";
			}
		}
		else if (cmd == "place_first_base")
		{
			double lon = req.get("lon", 0.0).asDouble();
			double lat = req.get("lat", 0.0).asDouble();
			std::string name = req.get("name", "TestBase").asString();

			BuildNewBaseState* build = nullptr;
			for (auto* s : _game->getStates())
			{
				if (auto* b = dynamic_cast<BuildNewBaseState*>(s))
				{
					build = b;
				}
			}
			if (!build)
			{
				resp["error"] = "no BuildNewBaseState in state stack";
			}
			else if (!build->placeAt(lon, lat))
			{
				resp["error"] = "coordinates not on land";
			}
			else
			{
				// placeAt pushed BaseNameState (first base); confirm the name.
				BaseNameState* nameState = nullptr;
				for (auto* s : _game->getStates())
				{
					if (auto* n = dynamic_cast<BaseNameState*>(s))
					{
						nameState = n;
					}
				}
				if (nameState)
				{
					nameState->setNameAndConfirm(name);
					resp["ok"] = true;
				}
				else
				{
					resp["error"] = "BaseNameState not pushed after placement";
				}
			}
		}
		else if (cmd == "lobby_ready")
		{
			LobbyMenu* lobby = nullptr;
			for (auto* s : _game->getStates())
			{
				if (auto* l = dynamic_cast<LobbyMenu*>(s))
				{
					lobby = l;
				}
			}
			if (lobby)
			{
				lobby->btnCancelClick(nullptr);
				resp["ok"] = true;
			}
			else
			{
				resp["error"] = "no LobbyMenu in state stack";
			}
		}
		else if (cmd == "get_soldiers")
		{
			if (!_game->getSavedGame())
			{
				resp["error"] = "no save loaded";
			}
			else
			{
				Json::Value bases(Json::arrayValue);
				for (auto* base : *_game->getSavedGame()->getBases())
				{
					Json::Value b;
					b["name"] = base->getName();
					b["coopBaseFlag"] = base->_coopBase;
					b["coopIcon"] = base->_coopIcon;
					b["coopBaseId"] = base->_coop_base_id;
					Json::Value soldiers(Json::arrayValue);
					for (auto* s : *base->getSoldiers())
					{
						soldiers.append(soldierToJson(s));
					}
					b["soldiers"] = soldiers;
					bases.append(b);
				}
				resp["bases"] = bases;
				resp["ok"] = true;
			}
		}
		else if (cmd == "get_mirror_soldiers")
		{
			// What the mirror-base visit view would list: soldiers in THIS
			// machine's save stationed at the given coop base id (the exact
			// source set CoopState(55) deep-copies into the visited base).
			int coopBaseId = req.get("coopBaseId", -1).asInt();
			if (!_game->getSavedGame())
			{
				resp["error"] = "no save loaded";
			}
			else
			{
				Json::Value soldiers(Json::arrayValue);
				for (auto* base : *_game->getSavedGame()->getBases())
				{
					for (auto* s : *base->getSoldiers())
					{
						if (s->getCoopBase() == coopBaseId)
						{
							soldiers.append(soldierToJson(s));
						}
					}
				}
				resp["soldiers"] = soldiers;
				resp["ok"] = true;
			}
		}
		else if (cmd == "open_soldiers")
		{
			std::string baseName = req.get("base", "").asString();
			Base* target = nullptr;
			if (_game->getSavedGame())
			{
				for (auto* base : *_game->getSavedGame()->getBases())
				{
					if (baseName.empty() ? (base->_coopBase == false && base->_coopIcon == false) : base->getName() == baseName)
					{
						target = base;
						break;
					}
				}
			}
			if (target)
			{
				_game->pushState(new SoldiersState(target));
				resp["ok"] = true;
			}
			else
			{
				resp["error"] = "base not found: " + baseName;
			}
		}
		else if (cmd == "soldiers_ok")
		{
			SoldiersState* st = nullptr;
			for (auto* s : _game->getStates())
			{
				if (auto* x = dynamic_cast<SoldiersState*>(s))
				{
					st = x;
				}
			}
			if (st)
			{
				st->btnOkClick(nullptr);
				resp["ok"] = true;
			}
			else
			{
				resp["error"] = "no SoldiersState in state stack";
			}
		}
		else if (cmd == "visit_coop_base")
		{
			std::string baseName = req.get("base", "").asString();
			Base* target = nullptr;
			if (_game->getSavedGame())
			{
				for (auto* base : *_game->getSavedGame()->getBases())
				{
					if (base->_coopBase == true || base->_coopIcon == true)
					{
						if (baseName.empty() || base->getName() == baseName)
						{
							target = base;
							break;
						}
					}
				}
			}
			GeoscapeState* geo = _game->getGeoscapeState();
			if (!target)
			{
				resp["error"] = "coop base not found: " + baseName;
			}
			else if (!geo)
			{
				resp["error"] = "no GeoscapeState";
			}
			else
			{
				// same as clicking the peer base marker (MultipleTargetsState)
				coop->current_base_name = target->getName();
				CoopState* w = new CoopState(50);
				w->setGlobe(geo->getGlobe());
				_game->pushState(w);
				resp["ok"] = true;
			}
		}
		else if (cmd == "leave_base")
		{
			BasescapeState* st = nullptr;
			for (auto* s : _game->getStates())
			{
				if (auto* x = dynamic_cast<BasescapeState*>(s))
				{
					st = x;
				}
			}
			if (st)
			{
				st->btnGeoscapeClick(nullptr);
				resp["ok"] = true;
			}
			else
			{
				resp["error"] = "no BasescapeState in state stack";
			}
		}
		else if (cmd == "transfer_targets")
		{
			// What the transfer dialog would offer for this soldier - lets
			// tests validate owner resolution + button names without UI.
			std::string name = req.get("name", "").asString();
			Soldier* found = nullptr;
			if (_game->getSavedGame())
			{
				for (auto* base : *_game->getSavedGame()->getBases())
				{
					for (auto* s : *base->getSoldiers())
					{
						if (s->getName().find(name) != std::string::npos)
						{
							found = s;
							break;
						}
					}
					if (found) break;
				}
			}
			if (!found)
			{
				resp["error"] = "soldier not found: " + name;
			}
			else
			{
				int currentOwner = TransferSoldierMenu::resolveOwnerId(found);
				int localPlayerId = connectionTCP::getHost() ? 0 : 1;
				Json::Value targets(Json::arrayValue);
				for (int playerId = 0; playerId <= 1; ++playerId)
				{
					if (playerId != currentOwner)
					{
						Json::Value t;
						t["id"] = playerId;
						t["name"] = (playerId == localPlayerId) ? coop->getHostName() : coop->getCurrentClientName();
						targets.append(t);
					}
				}
				resp["currentOwner"] = currentOwner;
				resp["localPlayer"] = localPlayerId;
				resp["targets"] = targets;
				resp["ok"] = true;
			}
		}
		else if (cmd == "open_transfer_dialog")
		{
			std::string name = req.get("name", "").asString();
			Soldier* found = nullptr;
			if (_game->getSavedGame())
			{
				for (auto* base : *_game->getSavedGame()->getBases())
				{
					for (auto* s : *base->getSoldiers())
					{
						if (s->getName().find(name) != std::string::npos)
						{
							found = s;
							break;
						}
					}
					if (found) break;
				}
			}
			if (found)
			{
				_game->pushState(new TransferSoldierMenu(found, TransferSoldierMenu::resolveOwnerId(found)));
				resp["ok"] = true;
			}
			else
			{
				resp["error"] = "soldier not found: " + name;
			}
		}
		else if (cmd == "rename_soldier")
		{
			std::string name = req.get("name", "").asString();
			std::string newName = req.get("newName", "").asString();
			Soldier* found = nullptr;
			if (_game->getSavedGame() && !newName.empty())
			{
				for (auto* base : *_game->getSavedGame()->getBases())
				{
					for (auto* s : *base->getSoldiers())
					{
						if (s->getName().find(name) != std::string::npos)
						{
							found = s;
							break;
						}
					}
					if (found) break;
				}
			}
			if (found)
			{
				found->setName(newName);
				resp["ok"] = true;
			}
			else
			{
				resp["error"] = "soldier not found: " + name;
			}
		}
		else if (cmd == "show_notice")
		{
			_game->pushState(new TransferNoticeState(req.get("message", "test notice").asString()));
			resp["ok"] = true;
		}
		else if (cmd == "get_notices")
		{
			Json::Value notices(Json::arrayValue);
			for (auto* s : _game->getStates())
			{
				if (auto* n = dynamic_cast<TransferNoticeState*>(s))
				{
					notices.append(n->getCategory());
				}
			}
			resp["categories"] = notices;
			resp["ok"] = true;
		}
		else if (cmd == "dismiss_notice")
		{
			TransferNoticeState* st = nullptr;
			for (auto* s : _game->getStates())
			{
				if (auto* x = dynamic_cast<TransferNoticeState*>(s))
				{
					st = x;
				}
			}
			if (st)
			{
				st->btnOkClick(nullptr);
				resp["ok"] = true;
			}
			else
			{
				resp["error"] = "no TransferNoticeState in state stack";
			}
		}
		else if (cmd == "cancel_dialog")
		{
			TransferSoldierMenu* st = nullptr;
			for (auto* s : _game->getStates())
			{
				if (auto* x = dynamic_cast<TransferSoldierMenu*>(s))
				{
					st = x;
				}
			}
			if (st)
			{
				st->btnCancelClick(nullptr);
				resp["ok"] = true;
			}
			else
			{
				resp["error"] = "no TransferSoldierMenu in state stack";
			}
		}
		else if (cmd == "get_palettes")
		{
			// First N palette entries of the top two states, for asserting
			// that a dialog adopted its parent's palette (flicker check).
			Json::Value states(Json::arrayValue);
			auto& stack = _game->getStates();
			for (auto* s : stack)
			{
				Json::Value e;
				e["state"] = typeid(*s).name();
				Json::Value cols(Json::arrayValue);
				SDL_Color* pal = s->getPalette();
				for (int i = 0; i < 16; ++i)
				{
					cols.append((pal[i].r << 16) | (pal[i].g << 8) | pal[i].b);
				}
				e["colors"] = cols;
				states.append(e);
			}
			resp["states"] = states;
			resp["ok"] = true;
		}
		else if (cmd == "transfer")
		{
			std::string name = req.get("name", "").asString();
			int owner = req.get("owner", -1).asInt();
			if (name.empty() || owner < 0)
			{
				resp["error"] = "need name and owner";
			}
			else if (!_game->getSavedGame())
			{
				resp["error"] = "no save loaded";
			}
			else
			{
				Soldier* found = nullptr;
				for (auto* base : *_game->getSavedGame()->getBases())
				{
					for (auto* s : *base->getSoldiers())
					{
						if (s->getName().find(name) != std::string::npos)
						{
							found = s;
							break;
						}
					}
					if (found) break;
				}
				if (!found)
				{
					resp["error"] = "soldier not found: " + name;
				}
				else
				{
					coop->transferSoldierOwnership(found, owner, true);
					resp["soldier"] = soldierToJson(found);
					resp["ok"] = true;
				}
			}
		}
		else if (cmd == "save_game")
		{
			std::string file = req.get("file", "").asString();
			if (file.empty() || !_game->getSavedGame())
			{
				resp["error"] = "need file + loaded save";
			}
			else
			{
				_game->getSavedGame()->save(file, _game->getMod());
				resp["ok"] = true;
			}
		}
		else if (cmd == "client_reload_progress")
		{
			// Reconnect flow: ask the host for our world (same as the client
			// branch of Profile::buttonOK).
			if (connectionTCP::getServerOwner())
			{
				resp["error"] = "host cannot reload progress";
			}
			else if (connectionTCP::saveID == 0)
			{
				resp["error"] = "no saveID";
			}
			else
			{
				Json::Value root;
				root["state"] = "request_load_progress";
				coop->sendTCPPacketData(root.toStyledString());
				resp["ok"] = true;
			}
		}
		else if (cmd == "has_coop_file")
		{
			std::string key = req.get("key", "").asString();
			resp["present"] = connectionTCP::hasCoopFile(key);
			resp["ok"] = true;
		}
		else if (cmd == "set_option")
		{
			std::string name = req.get("name", "").asString();
			if (name == "HostSaveProgress")
			{
				Options::HostSaveProgress = req.get("value", false).asBool();
				resp["ok"] = true;
			}
			else
			{
				resp["error"] = "unknown option: " + name;
			}
		}
		else
		{
			resp["error"] = "unknown cmd: " + cmd;
		}
	}

	Json::FastWriter w;
	std::string out = w.write(resp);
	if (!out.empty() && out.back() == '\n')
	{
		out.pop_back();
	}
	return out;
}

}
