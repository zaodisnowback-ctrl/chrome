import sys

sys.dont_write_bytecode = True

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("WebKit", "6.0")

from browser import BrowserApplication


if __name__ == "__main__":
    raise SystemExit(BrowserApplication().run())
