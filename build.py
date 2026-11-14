#!/usr/bin/env python3
# Copyright (c) 2026 FileSort. All rights reserved. See LICENSE.
"""Build the standalone FileSort executable for the OS you run this on.

    pip install pyinstaller
    python build.py            ->  dist/FileSort  (.exe on Windows, .app on macOS)
"""
import sys
from pathlib import Path

import PyInstaller.__main__

args = ["--noconfirm", "--clean", "--onefile", "--windowed", "--name", "FileSort", "app.py"]

# Some Linux Python builds keep the Tcl/Tk libraries where PyInstaller does not
# look for them; bundle them explicitly so the app starts on machines without Tk.
if sys.platform.startswith("linux"):
    for lib in Path(sys.base_prefix, "lib").glob("libtcl*.so*"):
        args += ["--add-binary", f"{lib}:."]
    for lib in Path(sys.base_prefix, "lib").glob("libtk*.so*"):
        args += ["--add-binary", f"{lib}:."]

PyInstaller.__main__.run(args)
