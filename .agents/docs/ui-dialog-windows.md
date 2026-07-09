# UI dialog windows (OpenXcom / OXCE)

How dialog windows, menus, and screens are built in this codebase. Worked
example: the **Direct Connect** dialog (Main Menu → New Battle → COOP → Server
Browser → "Direct Connect"), defined in
[`src/CoopMod/DirectConnect.h`](../../src/CoopMod/DirectConnect.h) and
[`src/CoopMod/DirectConnect.cpp`](../../src/CoopMod/DirectConnect.cpp).

## Mental model

- **Every dialog/menu/screen is a `State`** (`src/Engine/State.h`). A `State`
  owns a set of UI surfaces and receives input.
- The game keeps a **stack of states** (`Game::pushState` / `Game::popState`,
  `src/Engine/Game.cpp`). The top state is active/drawn; lower states stay in
  memory. A "dialog" is just a non-fullscreen state (`_screen = false`) drawn on
  top of whatever is below it.
- Widgets are **`Surface`s**. Interactive ones (buttons, text edits, combo
  boxes, lists) derive from `InteractiveSurface` (`src/Engine/InteractiveSurface.h`),
  which provides the event hooks. Plain `Text` labels are non-interactive.
- Widget classes live in `src/Interface/` (`Window`, `Text`, `TextButton`,
  `TextEdit`, `ComboBox`, `TextList`, `ArrowButton`, `ToggleTextButton`,
  `Slider`, `Frame`, …).

## Anatomy of a dialog

### Header (`DirectConnect.h`)

Declares the class deriving from `State`, the widget pointers as members, the
backing-data members, and one method per event handler (each takes `Action*`):

```cpp
class DirectConnect : public State
{
  private:
    TextButton *_btnCancel, *_tcpButtonJoin;     // buttons
    TextEdit   *_ipAddress, *_playerName, *_port; // text input fields
    Window     *_window;                          // background box
    Text       *_txtTitle, *_txtData, *_txtInfo;  // labels
    ComboBox   *_cbxNetworkProtocol;              // dropdown
    std::vector<std::string> _networkProtocolTypes; // dropdown options (backing)
    bool isUDP = false;                            // backing state
    void convertUnits();
    bool parseUdpPort(const std::string& text, uint16_t& outPort);
  public:
    DirectConnect();
    ~DirectConnect();
    void init() override;                          // re-entry hook
    void btnCancelClick(Action *action);           // event handlers
    void joinTCPGame(Action *action);
    void edtPlayerNameChange(Action* action);
    void cbxNetworkProtocolChange(Action* action);
};
```

### Source (`DirectConnect.cpp`) — the construction recipe

The **constructor builds the whole window once**. The standard order is:

```cpp
DirectConnect::DirectConnect()
{
    _screen = false;                       // 1. dialog (not fullscreen)

    // 2. Construct widgets (position/size in 320x200 virtual pixels).
    //    Note which widgets take the owning State (`this`) as first arg:
    _window            = new Window(this, 216, 160, 20, 20, POPUP_BOTH);
    _cbxNetworkProtocol= new ComboBox(this, 180, 18, 38, 50);
    _ipAddress         = new TextEdit(this, 180, 18, 38, 72);
    _port              = new TextEdit(this, 180, 18, 38, 92);
    _playerName        = new TextEdit(this, 180, 18, 38, 112);
    _tcpButtonJoin     = new TextButton(180, 18, 38, 132);   // TextButton: no State
    _btnCancel         = new TextButton(180, 18, 38, 152);
    _txtTitle          = new Text(206, 17, 25, 32);          // Text: no State

    // 3. Choose the interface ruleset (palette/colors) BEFORE add().
    setInterface("pauseMenu", false,
                 _game->getSavedGame() ? _game->getSavedGame()->getSavedBattle() : 0);

    // 4. Register each widget with the state: add(surface, elementId, category).
    //    elementId+category map into the interface ruleset for colors.
    add(_window, "window", "pauseMenu");
    add(_ipAddress);                         // add(surface) with no id = no ruleset theming
    add(_port);
    add(_playerName);
    add(_tcpButtonJoin, "button", "pauseMenu");
    add(_cbxNetworkProtocol, "button", "pauseMenu");
    add(_btnCancel, "button", "pauseMenu");
    add(_txtTitle, "text", "pauseMenu");

    // 5. Center the popup on screen, then paint the window background.
    centerAllSurfaces();
    setWindowBackground(_window, "pauseMenu");

    // 6. Configure each widget: text, color, handlers, visibility (see below).
    ...
}
```

Key points:

- **Who takes `this` (the State)?** `Window`, `TextEdit`, and `ComboBox` take
  the owning `State*` as their first constructor argument (they need it to grab
  focus / push their popup). `TextButton`, `Text`, and `TextList` take only
  `(width, height, x, y)`.
- **Coordinate space** is the 320×200 virtual screen; `centerAllSurfaces()`
  offsets everything so the window is centered regardless of the real
  resolution.
- **`setInterface(category, alterPal, battleGame)`** loads the palette and the
  per-element color rules for that interface name. Call it before `add()`.
- **`add(surface, id, category, parent=0)`** attaches the widget to the state.
  The `id`/`category` look up colors in the interface ruleset (e.g. element
  `"button"` in interface `"pauseMenu"`). The bare `add(surface)` overload adds
  a widget without ruleset theming (it just uses the state palette) — used here
  for the `TextEdit`s, which are colored manually instead.

## Widget reference

### Window (background box) — `src/Interface/Window.h`
```cpp
_window = new Window(this, w, h, x, y, POPUP_BOTH); // popup anim: NONE/HORIZONTAL/VERTICAL/BOTH
setWindowBackground(_window, "pauseMenu");          // background image from the interface ruleset
```

### Text (static label) — `src/Interface/Text.h`
Non-interactive. Used for titles and info lines.
```cpp
_txtTitle->setBig();                 // or setSmall()
_txtTitle->setAlign(ALIGN_CENTER);
_txtTitle->setText(tr("STR_SOMETHING"));   // tr(...) = localized; or a literal "DIRECT CONNECT"
_txtTitle->setColor(color);
_txtTitle->setVisible(false);        // toggled at runtime
```

### TextButton — `src/Interface/TextButton.h`
```cpp
_tcpButtonJoin->setText("JOIN");
_tcpButtonJoin->onMouseClick((ActionHandler)&DirectConnect::joinTCPGame);
// Optional keyboard shortcut (Options::keyOk / keyCancel are the configured keys):
_tcpButtonJoin->onKeyboardPress((ActionHandler)&DirectConnect::joinTCPGame, Options::keyOk);
_btnCancel->onKeyboardPress((ActionHandler)&DirectConnect::btnCancelClick, Options::keyCancel);
```
- Radio-button groups: `setGroup(TextButton**)` makes a set mutually exclusive
  (only one pressed). See option screens for examples.

### TextEdit (text input field) — `src/Interface/TextEdit.h`
The editable widget. Takes the `State*`.
```cpp
_playerName->setColor(color);
_playerName->setBorderColor(color);
_playerName->setBig();
_playerName->setText("Player");                  // initial / current value
_playerName->setVisible(true);
_playerName->onChange((ActionHandler)&DirectConnect::edtPlayerNameChange); // fires on each edit
// _playerName->onEnter(handler);                // fires when ENTER pressed
// Restrict input to digits (e.g. a port field):
// _port->setConstraint(TEC_NUMERIC_POSITIVE);   // TEC_NONE / TEC_NUMERIC / TEC_NUMERIC_POSITIVE
std::string value = _playerName->getText();      // read it back
```

### ComboBox (dropdown) — `src/Interface/ComboBox.h`
A button that drops down a selectable list. Takes the `State*`.
```cpp
// 1. Build the option strings (kept as a member so indices stay meaningful):
_networkProtocolTypes.push_back("NETWORK: TCP");   // index 0
_networkProtocolTypes.push_back("NETWORK: UDP");   // index 1
// 2. Feed them in (translate=false means the strings are literal, not STR_ keys):
_cbxNetworkProtocol->setOptions(_networkProtocolTypes, false);
// 3. React to selection changes:
_cbxNetworkProtocol->onChange((ActionHandler)&DirectConnect::cbxNetworkProtocolChange);
// 4. (optional) setSelected(idx), setText("..."), setBackground(_window),
//    new ComboBox(this, w, h, x, y, /*popupAboveButton=*/true)

void DirectConnect::cbxNetworkProtocolChange(Action*)
{
    int sel = _cbxNetworkProtocol->getSelected();  // 0-based index into the options
    if (sel == 0) isUDP = false;                   // map index -> meaning
    else if (sel == 1) isUDP = true;
}
```
> Gotcha: `getSelected()` returns the **index**, not the string. Keep the
> options vector and the index→meaning mapping in sync.

### TextList (scrollable rows) — `src/Interface/TextList.h`
Used for tables like the server list. Briefly:
```cpp
_lstServers->setColumns(4, 110, 53, 57, 77);
_lstServers->setSelectable(true);
_lstServers->setBackground(_window);
_lstServers->onMousePress((ActionHandler)&ServerList::lstServerPress);
_lstServers->addRow(4, col0.c_str(), col1.c_str(), col2.c_str(), col3.c_str());
int row = _lstServers->getSelectedRow();
_lstServers->clearList();
```

## Event handling (`ActionHandler`)

- `ActionHandler` is `typedef void (State::*)(Action*)` (see
  `InteractiveSurface.h`). Every handler is a member method `void f(Action*)`.
- Hook handlers with the cast `(ActionHandler)&ClassName::method`:
  - `onMouseClick(handler, button=SDL_BUTTON_LEFT)`
  - `onMousePress` / `onMouseRelease` / `onMouseIn` / `onMouseOver` / `onMouseOut`
  - `onKeyboardPress(handler, key=SDLK_ANY)` / `onKeyboardRelease`
  - `onChange` (TextEdit, ComboBox, Slider), `onEnter` (TextEdit)
- The `Action*` carries the raw event. Common use — distinguish mouse buttons:
  ```cpp
  if (action->getDetails()->button.button == SDL_BUTTON_LEFT)  { ... }
  else if (action->getDetails()->button.button == SDL_BUTTON_RIGHT) { ... }
  ```

## Wiring inputs to backing data

The codebase's normal pattern: **seed the widget from a model/file in the
constructor, and write back in the change handler.** From Direct Connect /
Server Browser:

- **Read initial values** (constructor) — e.g. parse `ip_address.json` from
  `Options::getMasterUserFolder()` and `setText()` the fields, applying
  defaults when empty.
- **Write on change** — the `onChange` handler pushes the value into the model
  and/or persists it:
  ```cpp
  void DirectConnect::edtPlayerNameChange(Action*)
  {
      _game->getCoopMod()->setHostName(_playerName->getText());
  }
  ```
  (`ServerList::edtPlayerNameChange` additionally writes it back to
  `ip_address.json`.)
- **Numeric fields**: read via `getText()` then validate/convert (see
  `parseUdpPort`), and/or constrain input with `setConstraint(TEC_NUMERIC*)`.
- `_game` is a `static` member of `State`, so any state can reach the core
  game, the mod (`_game->getMod()`), the save (`_game->getSavedGame()`), and the
  co-op singleton (`_game->getCoopMod()`).

## Opening and closing dialogs

```cpp
_game->pushState(new DirectConnect());   // open a dialog on top of the current screen
_game->popState();                        // close the current dialog (typical Cancel handler)
```

- **Lifecycle**: constructor builds the UI once → `init()` runs every time the
  state becomes active (including when a dialog you pushed on top is popped, so
  use it to refresh/re-show state) → `think()` runs every frame (background
  polling, async results) → `handle(Action*)` routes input → `blit()` draws.
- **Returning from a child dialog**: when you `pushState` another dialog and it
  later `popState`s, your `init()` is called again — re-apply any visibility or
  data that may have changed.
- ⚠️ **Do not call `_game->popState()` from a state's constructor.** `pushState`
  appends after the constructor returns, so a `popState()` during construction
  pops the state *underneath* the one being created and destroys the wrong
  window. (This exact ordering caused a real bug where an error dialog tore down
  the Server Browser before the user could interact with it.)

## Theming / colors

- Colors come from the **interface ruleset** keyed by the `category` you pass to
  `setInterface()` and the `id`/`category` you pass to `add()` (e.g. `"window"`,
  `"button"`, `"text"`, `"list"` under `"pauseMenu"` / `"saveMenus"` /
  `"geoscape"`). Ruleset files are in the mod data.
- For widgets added without an id, set colors manually
  (`setColor`, `setBorderColor`, `setSecondaryColor`).
- Battlescape variants: `applyBattlescapeTheme("category")` and a different
  palette index when `_game->getSavedGame()->getSavedBattle()` is non-null
  (dialogs commonly switch a `color` value 239↔255 based on this).

## Skeleton for a new dialog

`src/CoopMod/MyDialog.h`:
```cpp
#pragma once
#include "../Engine/State.h"
namespace OpenXcom
{
class Window; class Text; class TextButton; class TextEdit;
class MyDialog : public State
{
private:
    Window     *_window;
    Text       *_txtTitle;
    TextEdit   *_edtValue;
    TextButton *_btnOk, *_btnCancel;
public:
    MyDialog();
    void btnOkClick(Action* action);
    void btnCancelClick(Action* action);
    void edtValueChange(Action* action);
};
}
```

`src/CoopMod/MyDialog.cpp`:
```cpp
#include "MyDialog.h"
#include "../Engine/Game.h"
#include "../Engine/Action.h"
#include "../Engine/Options.h"
#include "../Interface/Window.h"
#include "../Interface/Text.h"
#include "../Interface/TextButton.h"
#include "../Interface/TextEdit.h"
namespace OpenXcom
{
MyDialog::MyDialog()
{
    _screen = false;
    _window    = new Window(this, 216, 100, 52, 50, POPUP_BOTH);
    _txtTitle  = new Text(206, 17, 57, 60);
    _edtValue  = new TextEdit(this, 180, 16, 70, 80);
    _btnOk     = new TextButton(90, 16, 60, 120);
    _btnCancel = new TextButton(90, 16, 160, 120);

    setInterface("pauseMenu");
    add(_window, "window", "pauseMenu");
    add(_txtTitle, "text", "pauseMenu");
    add(_edtValue);
    add(_btnOk, "button", "pauseMenu");
    add(_btnCancel, "button", "pauseMenu");
    centerAllSurfaces();
    setWindowBackground(_window, "pauseMenu");

    _txtTitle->setBig();
    _txtTitle->setAlign(ALIGN_CENTER);
    _txtTitle->setText("MY DIALOG");

    _edtValue->setText("");
    _edtValue->onChange((ActionHandler)&MyDialog::edtValueChange);

    _btnOk->setText("OK");
    _btnOk->onMouseClick((ActionHandler)&MyDialog::btnOkClick);
    _btnOk->onKeyboardPress((ActionHandler)&MyDialog::btnOkClick, Options::keyOk);

    _btnCancel->setText(tr("STR_CANCEL"));
    _btnCancel->onMouseClick((ActionHandler)&MyDialog::btnCancelClick);
    _btnCancel->onKeyboardPress((ActionHandler)&MyDialog::btnCancelClick, Options::keyCancel);
}
void MyDialog::btnOkClick(Action*)     { /* use _edtValue->getText() */ _game->popState(); }
void MyDialog::btnCancelClick(Action*) { _game->popState(); }
void MyDialog::edtValueChange(Action*) { /* live-validate / store */ }
}
```

Open it from another state with `_game->pushState(new MyDialog());`.

## Build registration (important)

New `.cpp`/`.h` under `src/CoopMod/` **must be added to the build**, or they
silently won't compile/link:
- Visual Studio: `src/OpenXcom.2010.vcxproj` (and `.filters`) — or the local
  `Directory.Build.props` used in this dev setup.
- CMake: the `coopmod_src` list in `CMakeLists.txt`.

The committed project files are known to lag behind newly added co-op sources,
so double-check your new files are listed.

## File map

- Co-op dialogs: `src/CoopMod/*.{h,cpp}` (e.g. `DirectConnect`, `HostMenu`,
  `ServerList`, `CoopMenu`, `AddServerMenu`, `FilterMenu`, `PasswordCheckMenu`,
  `ModCheckMenu`, `LobbyMenu`).
- State base + engine: `src/Engine/State.{h,cpp}`,
  `src/Engine/InteractiveSurface.h`, `src/Engine/Surface.h`,
  `src/Engine/Game.{h,cpp}`, `src/Engine/Action.h`.
- Widgets: `src/Interface/` (`Window`, `Text`, `TextButton`, `TextEdit`,
  `ComboBox`, `TextList`, `ArrowButton`, `ToggleTextButton`, `Slider`, `Frame`).
