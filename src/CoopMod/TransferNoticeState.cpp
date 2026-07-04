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
#include "TransferNoticeState.h"

#include "../Engine/Action.h"
#include "../Engine/Game.h"
#include "../Engine/Options.h"
#include "../Interface/Text.h"
#include "../Interface/TextButton.h"
#include "../Interface/Window.h"
#include "../Mod/Mod.h"
#include "../Mod/RuleInterface.h"

namespace OpenXcom
{

TransferNoticeState::TransferNoticeState(const std::string &message)
{
	_screen = false;

	_window = new Window(this, 256, 88, 32, 56, POPUP_BOTH);
	_txtMessage = new Text(236, 40, 42, 68);
	_btnOk = new TextButton(120, 16, 100, 118);

	// Adopt the palette of whatever screen we're over - no palette swap, no
	// flicker, works on geoscape, basescape and the peer-base view alike.
	if (!_game->getStates().empty())
	{
		setStatePalette(_game->getStates().back()->getPalette());
	}

	add(_window, "window", "sackSoldier");
	add(_txtMessage, "text", "sackSoldier");
	add(_btnOk, "button", "sackSoldier");

	centerAllSurfaces();
	setWindowBackground(_window, "sackSoldier");

	_txtMessage->setAlign(ALIGN_CENTER);
	_txtMessage->setWordWrap(true);
	_txtMessage->setText(message);

	_btnOk->setText(tr("STR_OK"));
	_btnOk->onMouseClick((ActionHandler)&TransferNoticeState::btnOkClick);
	_btnOk->onKeyboardPress((ActionHandler)&TransferNoticeState::btnOkClick, Options::keyOk);
	_btnOk->onKeyboardPress((ActionHandler)&TransferNoticeState::btnOkClick, Options::keyCancel);
}

void TransferNoticeState::btnOkClick(Action *)
{
	_game->popState();
}

}
