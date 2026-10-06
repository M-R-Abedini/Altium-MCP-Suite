# UI automation

Driving Altium's interface directly: menus, dialogs, panels, grids and
the canvas. It is on by default and can be switched off with one
environment variable.

```
EDA_AGENT_UI_AUTOMATION=0
```

Off refuses every synthesised keystroke and mouse event. Reading stays
available, which is deliberate: dialog detection is how the rest of the
system notices Altium is blocked on a modal, and disabling automation to
be safer must not remove the checks that keep it safe.

## Why it exists

Most of this project talks to Altium through the scripting bridge, which
is precise, addressable and has no side effects on focus. UI automation
exists for the parts of Altium the bridge cannot reach at all.

- **`application.execute_menu` reports success and invokes nothing.**
  `RunProcess` ignores ids it does not know and cannot report failure,
  so a caller is told the command ran when nothing happened.
- **`GetMenu` on the main frame returns 0.** Altium draws its menus with
  DevExpress bars: no menu ids and no `WM_COMMAND` route.
- **Whole dialogs have no scripting API.** Update From Libraries,
  Preferences and the wizards are reachable only through the interface.
  There is no DelphiScript call that opens them, reads a page or reports
  a setting.
- **Some things are readable and not writable through the bridge**, and
  the dialog is the only place the value can be changed.

Without it, those parts of Altium are simply unavailable.

## What the risks actually are

Synthesised input is not addressed to a window. `keybd_event` and
`mouse_event` are delivered to whatever is **active at the instant they
fire**, and a click lands on whatever is under the pointer. Every other
call in this package addresses a window handle and physically cannot go
astray; only the synthesis path can.

The concrete hazards:

- **A keystroke meant for Altium reaching another application**, if
  focus moved between one event and the next. This is not theoretical:
  it was measured here, with a menu walk still sending keys after Altium
  had lost the foreground.
- **A click landing on another window**, if the coordinates are outside
  Altium or something is floating on top of it. A click can also raise
  that window, so the following event goes there too.
- **A key held across a focus change**, releasing into whatever became
  active.
- **Focus being taken from you.** Menus and clicks require the window in
  front, so running these while you are typing will interrupt you.
- **Actions with no read-back.** A keystroke or a pointer gesture cannot
  be confirmed the way a property write can. Anything that matters is
  verified afterwards with a bridge read.

## What contains those risks

**Every synthesised event is preceded by a check, immediately before it
fires** rather than once per operation, including between a key press
and its release. `tests/test_foreground_guard.py` enforces this per
line, because a function that checks once and then emits five events in
a loop would pass a naive test while firing four unchecked events.

**The check refocuses rather than refusing.** Focus moves for ordinary
reasons, and failing an operation because you glanced at another window
would make this unusable and create pressure to remove the guard. The
target is restored and raised, and only a window that will not come
forward raises `ForegroundLost`.

Raising it needs `AttachThreadInput`. A bare `SetForegroundWindow` from
a background process is denied by Windows and **fails silently**, which
is how `bring_to_front` used to report a success it had never confirmed.

**Coordinates are confined to the application.** A foreground check
proves the right application is active; it says nothing about where the
pointer is. `click_at`, `drag` and the right click all verify the point
is over a window belonging to the target process, and refuse with
`ForeignTarget` otherwise. Both ends of a drag are checked; the points
in between cannot be delivered elsewhere because Windows captures the
mouse to whichever window received the button-down.

**No tool accepts a window handle from the caller.** Every target is
resolved by name out of `dialogs(pid)` or `frame(pid)`, both scoped to
the one process, so a caller cannot address an arbitrary window. A test
enforces this, since adding an `hwnd` parameter would bypass the
coordinate containment entirely.

**Discovery does not invoke.** Listing a menu must not be able to run
the thing it is describing.

## Known limitation

Altium's menu bar carries entries that are commands rather than menus:
Place a Comment, Share, Open Home page, Preferences, and any customised
ones. **Nothing distinguishes them from real menus.** Measured across
all 17 bar items: identical MSAA state including `HASPOPUP`, identical
`accDefaultAction` of `Open`, and identical UIA control type of `menu
item`. They are indistinguishable until something opens.

So listing one of those entries clicks it, and clicking it runs it. A
keyboard walk was tried as a way round this and rejected: menu mode
resumes at the last menu used rather than at the first, so a positional
walk returned another menu's contents under the requested name, which is
worse than the problem it solved.

If you need to enumerate the bar, list the menus you know are menus.
