"""Terminator plugin: Shift+Enter sends a line feed (Ctrl+J), so that claude starts a new line.

VTE sends the same carriage return for Enter and Shift+Enter, and terminator has no binding that
sends text. So the plugin wraps the key handler of its terminals, once the first terminal loads the
plugins, before that terminal connects its keys. A shell runs the line on a line feed, as on Enter.
agent-terminator.py links it into ~/.config/terminator/plugins and enables it in its windows.
"""

from gi.repository import Gdk
from terminatorlib import plugin
from terminatorlib.terminal import Terminal

AVAILABLE = ["ShiftEnterNewline"]
ENTER = {Gdk.KEY_KP_Enter, Gdk.KEY_Return}
MODIFIERS = (
    Gdk.ModifierType.CONTROL_MASK
    | Gdk.ModifierType.MOD1_MASK
    | Gdk.ModifierType.SHIFT_MASK
    | Gdk.ModifierType.SUPER_MASK
)


class ShiftEnterNewline(plugin.Plugin):
    capabilities = ()

    def __init__(self):
        super().__init__()
        on_keypress = Terminal.on_keypress

        def on_shift_enter(terminal, widget, event):
            if event.keyval in ENTER and event.get_state() & MODIFIERS == Gdk.ModifierType.SHIFT_MASK:
                terminal.vte.feed_child(b"\n")
                return True
            return on_keypress(terminal, widget, event)

        Terminal.on_keypress = on_shift_enter
