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

#include "p2p_config.h"

#include <cstring>

namespace OpenXcom
{

// ---------------------------------------------------------------------------
// Fork-swappable constants. Fill these in for your deployment.
// ---------------------------------------------------------------------------

// Deployed signaling Worker URL (from `wrangler deploy`, https:// -> wss://).
// Leave as the PASTE_ placeholder in the open-source build.
// Example: "wss://openxcom-coop-signaling.example.workers.dev"
static const char* kSignalingUrl = ""; // PASTE_SIGNALING_WSS_URL_HERE

// Game version string used for browser row compatibility (grey out mismatches).
// Keep in sync with the value the rest of the build advertises.
static const char* kP2PGameVersion = "1.8.3 [v2026-06-20]";

// TURN (relay) credentials from a free metered.ca (Open Relay) account.
// Leave as PASTE_ placeholders in the open-source build; STUN still works for
// direct connections, TURN is only needed for symmetric-NAT peers.
static const char* kTurnUsername = "";   // PASTE_METERED_TURN_USERNAME_HERE
static const char* kTurnCredential = ""; // PASTE_METERED_TURN_CREDENTIAL_HERE

// STUN servers (no credentials). metered's STUN first, Google as an alternate.
static const char* kStunUrls[] = {
	"stun:stun.relay.metered.ca:80",
	"stun:stun.l.google.com:19302",
};

// TURN server URLs. Credentials (above) are attached to each at build time.
static const char* kTurnUrls[] = {
	"turn:global.relay.metered.ca:80?transport=udp",
	"turns:global.relay.metered.ca:443?transport=tcp",
};

// ---------------------------------------------------------------------------

static bool isPlaceholder(const char* s)
{
	return !s || s[0] == '\0' || std::strstr(s, "PASTE_") != nullptr;
}

const P2PConfig& getBuiltInP2PConfig()
{
	static const P2PConfig cfg = []() {
		P2PConfig c;
		c.signalingUrl = isPlaceholder(kSignalingUrl) ? std::string() : kSignalingUrl;
		c.gameVersion = kP2PGameVersion;

		for (const char* url : kStunUrls)
		{
			P2PIceServer s;
			s.url = url;
			c.iceServers.push_back(s);
		}

		// Only add TURN servers when credentials are actually configured;
		// a TURN entry without valid creds just wastes ICE gathering time.
		if (!isPlaceholder(kTurnUsername) && !isPlaceholder(kTurnCredential))
		{
			for (const char* url : kTurnUrls)
			{
				P2PIceServer s;
				s.url = url;
				s.username = kTurnUsername;
				s.credential = kTurnCredential;
				c.iceServers.push_back(s);
			}
		}
		return c;
	}();
	return cfg;
}

bool isP2PSignalingConfigured()
{
	return !getBuiltInP2PConfig().signalingUrl.empty();
}

bool isP2PTurnConfigured()
{
	for (const P2PIceServer& s : getBuiltInP2PConfig().iceServers)
	{
		if (!s.username.empty())
			return true;
	}
	return false;
}

} // namespace OpenXcom
