"""Entry point for the .exe: no arguments opens the window, otherwise command line."""
import sys

# The window is started via runpy, so PyInstaller can't see its imports. List them here.
import queue  # noqa: F401
import threading  # noqa: F401
import tkinter  # noqa: F401
import tkinter.filedialog  # noqa: F401
import tkinter.messagebox  # noqa: F401
import tkinter.ttk  # noqa: F401

import horimiya_order

sys.exit(horimiya_order.main())
