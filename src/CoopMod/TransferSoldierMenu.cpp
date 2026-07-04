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
#include "TransferSoldierMenu.h"

#include "../Engine/Action.h"
#include "../Engine/Game.h"
#include "../Engine/Options.h"
#include "../Interface/Text.h"
#include "../Interface/TextButton.h"
#include "../Interface/Window.h"
#include "../Savegame/SavedGame.h"
#include "../Savegame/Soldier.h"
#include "connectionTCP.h"

namespace OpenXcom
{

int TransferSoldierMenu::resolveOwnerId(Soldier *soldier)
{
	if (soldier->getOwnerPlayerId() != 999)
	{
		return soldier->getOwnerPlayerId();
	}
	// 999 = never explicitly assigned; the co-op control flag mirrors the
	// merged-world convention (0 = host, non-zero = client).
	return soldier->getCoop() != 0 ? 1 : 0;
}

TransferSoldierMenu::TransferSoldierMenu(Soldier *soldier, int currentOwnerId) : _soldier(soldier)
{
	_screen = false;

	// One button per player that is not the current owner. Player ids follow
	// the co-op convention: 0 = host, 1 = client. Built as a list so more
	// players slot in when the game grows past two.
	std::vector<std::pair<int, std::string> > targets;
	connectionTCP *coop = _game->getCoopMod();
	if (currentOwnerId != 0)
	{
		targets.push_back(std::make_pair(0, coop->getHostName()));
	}
	if (currentOwnerId != 1)
	{
		targets.push_back(std::make_pair(1, coop->getCurrentClientName()));
	}

	const int btnHeight = 16;
	const int btnSpacing = 4;
	const int windowWidth = 240;
	const int windowHeight = 60 + (int)(targets.size() + 1) * (btnHeight + btnSpacing);
	const int windowX = (320 - windowWidth) / 2;
	const int windowY = (200 - windowHeight) / 2;

	_window = new Window(this, windowWidth, windowHeight, windowX, windowY, POPUP_BOTH);
	_txtTitle = new Text(windowWidth - 20, 32, windowX + 10, windowY + 12);

	int y = windowY + 48;
	for (size_t i = 0; i < targets.size(); ++i)
	{
		_btnTargets.push_back(new TextButton(windowWidth - 40, btnHeight, windowX + 20, y));
		_targetIds.push_back(targets[i].first);
		y += btnHeight + btnSpacing;
	}
	_btnCancel = new TextButton(windowWidth - 40, btnHeight, windowX + 20, y);

	setInterface("pauseMenu", false, _game->getSavedGame() ? _game->getSavedGame()->getSavedBattle() : 0);

	add(_window, "window", "pauseMenu");
	add(_txtTitle, "text", "pauseMenu");
	for (auto *btn : _btnTargets)
	{
		add(btn, "button", "pauseMenu");
	}
	add(_btnCancel, "button", "pauseMenu");

	centerAllSurfaces();
	setWindowBackground(_window, "pauseMenu");

	_txtTitle->setAlign(ALIGN_CENTER);
	_txtTitle->setWordWrap(true);
	_txtTitle->setText("Transfer " + _soldier->getName() + " to another player?");

	for (size_t i = 0; i < _btnTargets.size(); ++i)
	{
		std::string name = targets[i].second;
		if (name.empty())
		{
			name = targets[i].first == 0 ? "HOST" : "CLIENT";
		}
		_btnTargets[i]->setText(name);
		_btnTargets[i]->onMouseClick((ActionHandler)&TransferSoldierMenu::btnTransferClick);
	}

	_btnCancel->setText(tr("STR_CANCEL"));
	_btnCancel->onMouseClick((ActionHandler)&TransferSoldierMenu::btnCancelClick);
	_btnCancel->onKeyboardPress((ActionHandler)&TransferSoldierMenu::btnCancelClick, Options::keyCancel);
}

void TransferSoldierMenu::btnTransferClick(Action *action)
{
	for (size_t i = 0; i < _btnTargets.size(); ++i)
	{
		if (action->getSender() == _btnTargets[i])
		{
			_game->getCoopMod()->transferSoldierOwnership(_soldier, _targetIds[i], true);
			break;
		}
	}
	_game->popState();
}

void TransferSoldierMenu::btnCancelClick(Action *)
{
	_game->popState();
}

}
