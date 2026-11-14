# Copyright (c) 2026 FileSort. All rights reserved. See LICENSE.
"""Drives the desktop window without a person: scan, sort, undo."""
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import tkinter
    from tkinter import messagebox
    import app as gui
    _probe = tkinter.Tk()
    _probe.destroy()
    HAVE_DISPLAY = True
except Exception:  # no Tk or no screen (e.g. a server)
    HAVE_DISPLAY = False


@unittest.skipUnless(HAVE_DISPLAY, "needs Tk and a display")
class WindowTests(unittest.TestCase):
    def test_scan_sort_undo(self):
        messagebox.askokcancel = lambda *a, **k: True
        messagebox.showwarning = lambda *a, **k: self.fail(f"warning shown: {a}")
        messagebox.showerror = lambda *a, **k: self.fail(f"error shown: {a}")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            for name in ("a.txt", "b.TXT", "c.jpg", "sub/d.pdf"):
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_text(name)
            before = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())

            win = gui.App(str(root))
            win.withdraw()

            def pump(condition, what, seconds=15):
                end = time.time() + seconds
                while time.time() < end:
                    win.update()
                    time.sleep(0.02)
                    if condition():
                        return
                self.fail(f"timed out waiting for: {what}")

            try:
                pump(lambda: len(win.plan) == 3, "first scan")
                win.recursive_var.set(True)
                win._options_changed()
                pump(lambda: len(win.plan) == 4, "recursive scan")
                win.organize()
                pump(lambda: not win.busy and "Moved 4" in win.status_var.get() and not win.plan, "sort")
                self.assertTrue((root / "pdf" / "d.pdf").is_file())
                pump(lambda: str(win.undo_btn["state"]) == "normal", "undo button")
                win.undo()
                pump(lambda: not win.busy and "Restored 4" in win.status_var.get(), "undo")
                after = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
                self.assertEqual(after, before)
            finally:
                win.destroy()


if __name__ == "__main__":
    unittest.main()
