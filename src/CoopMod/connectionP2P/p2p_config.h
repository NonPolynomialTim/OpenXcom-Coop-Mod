#pragma once
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

/*
 * Built-in configuration for the OpenXcom co-op P2P (WebRTC) transport.
 *
 * Mirrors the rendezvous_config.cpp pattern: compile-time constants a fork can
 * swap without touching UI/transport code. Holds the signaling Worker URL and
 * the ICE (STUN/TURN) server list.
 *
 * In the open-source build the signaling URL and TURN credentials are left as
 * blank placeholders (like the blank rendezvous keys), so a fresh checkout does
 * not point at anyone's infrastructure. Fill them in after deploying your own
 * signaling-worker/ and creating a free metered.ca (Open Relay) TURN account.
 *
 * This header deliberately has NO libdatachannel dependency so it can be
 * included/compiled before the WebRTC stack is wired into the build.
 */

#include <string>
#include <vector>

namespace OpenXcom
{

// A single ICE server entry. For STUN, username/credential are empty.
struct P2PIceServer
{
	std::string url;        // e.g. "stun:stun.l.google.com:19302"
	                        // or   "turn:global.relay.metered.ca:80?transport=udp"
	std::string username;   // TURN only
	std::string credential; // TURN only
};

struct P2PConfig
{
	// wss:// URL of the deployed signaling Worker. Blank / "PASTE_" placeholder
	// means "not configured": the public Server Browser and P2P host/join are
	// unavailable, but Direct Connect (TCP) and LAN discovery still work.
	std::string signalingUrl;
	std::vector<P2PIceServer> iceServers;
	std::string gameVersion;
};

// Returns the built-in P2P configuration (constructed from the constants in
// p2p_config.cpp).
const P2PConfig& getBuiltInP2PConfig();

// True when a real signaling URL has been configured (non-empty, not a
// PASTE_ placeholder). Use this for the non-fatal "master server unavailable"
// warning path (like loadBuiltInKeysOrWarn) instead of hard-failing a refresh.
bool isP2PSignalingConfigured();

// True when at least one TURN (relay) server with credentials is configured.
// STUN-only still allows most direct connections; TURN is the symmetric-NAT
// fallback.
bool isP2PTurnConfigured();

} // namespace OpenXcom
