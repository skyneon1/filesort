# Copyright (c) 2026 FileSort. All rights reserved. See LICENSE.
"""Tests for the sorting logic. Run on every OS:  python -m unittest discover -s tests -v"""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import filesort as core


def make(root: Path, *names: str) -> None:
    for name in names:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name, encoding="utf-8")  # content = original path, to prove nothing gets overwritten


def snapshot(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): p.read_text(encoding="utf-8")
            for p in root.rglob("*") if p.is_file() and p.name != core.LOG_NAME}


class CoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()

    def tearDown(self):
        self._tmp.cleanup()

    def sort(self, **kw):
        plan = core.scan(self.root, **kw)
        moves, failures = core.apply_plan(self.root, plan)
        self.assertEqual(failures, [])
        return plan, moves

    def test_by_extension(self):
        make(self.root, "a.txt", "B.TXT", "c.jpg", "Makefile", "archive.tar.gz")
        self.sort()
        self.assertEqual(set(snapshot(self.root)),
                         {"txt/a.txt", "txt/B.TXT", "jpg/c.jpg", "no_extension/Makefile", "gz/archive.tar.gz"})

    def test_by_type(self):
        make(self.root, "photo.PNG", "report.pdf", "song.mp3", "strange.xyz")
        self.sort(mode="type")
        self.assertEqual(set(snapshot(self.root)),
                         {"Images/photo.PNG", "Documents/report.pdf", "Audio/song.mp3", "Other/strange.xyz"})

    def test_by_month(self):
        make(self.root, "old.txt")
        stamp = time.mktime((2024, 3, 15, 12, 0, 0, 0, 0, -1))
        os.utime(self.root / "old.txt", (stamp, stamp))
        self.sort(mode="date")
        self.assertEqual(set(snapshot(self.root)), {"2024-03/old.txt"})

    def test_never_overwrites(self):
        make(self.root, "a.txt", "sub/a.txt", "txt/a.txt")
        self.sort(recursive=True)
        files = snapshot(self.root)
        self.assertEqual(set(files), {"txt/a.txt", "txt/a (1).txt", "txt/a (2).txt"})
        self.assertEqual(sorted(files.values()), ["a.txt", "sub/a.txt", "txt/a.txt"])  # all three survive

    def test_default_leaves_subfolders_alone(self):
        make(self.root, "a.txt", "sub/b.txt")
        self.sort()
        self.assertEqual(set(snapshot(self.root)), {"txt/a.txt", "sub/b.txt"})

    def test_second_run_does_nothing(self):
        make(self.root, "a.txt", "sub/b.pdf")
        self.sort(recursive=True)
        self.assertEqual(core.scan(self.root, recursive=True), [])

    def test_skips_hidden_system_and_log_files(self):
        make(self.root, "a.txt", ".secret", "desktop.ini", "Thumbs.db")
        self.sort()
        self.assertEqual(set(snapshot(self.root)), {"txt/a.txt", ".secret", "desktop.ini", "Thumbs.db"})
        self.assertEqual(core.scan(self.root), [])  # the undo log itself is not sorted

    def test_hidden_files_on_request(self):
        make(self.root, ".env.txt")
        self.sort(include_hidden=True)
        self.assertEqual(set(snapshot(self.root)), {"txt/.env.txt"})

    def test_recursive_never_takes_programs_apart(self):
        make(self.root, "repo/.git/HEAD", "repo/src/main.py", "My.app/Contents/Info.plist",
             "node_modules/x/index.js", "plain/b.pdf")
        self.sort(recursive=True)
        self.assertEqual(set(snapshot(self.root)),
                         {"repo/.git/HEAD", "repo/src/main.py", "My.app/Contents/Info.plist",
                          "node_modules/x/index.js", "pdf/b.pdf"})

    def test_existing_folder_with_other_capitalisation(self):
        make(self.root, "JPG/photo.jpg")
        self.assertEqual(core.scan(self.root, recursive=True), [])

    def test_prune_removes_only_folders_this_run_emptied(self):
        make(self.root, "gone/a.txt", "gone/deep/b.txt", "stays/c.txt", "stays/.hidden")
        (self.root / "was-empty").mkdir()
        plan, _ = self.sort(recursive=True)
        removed = core.prune_emptied_dirs(self.root, plan)
        self.assertEqual(removed, 2)
        self.assertFalse((self.root / "gone").exists())
        self.assertTrue((self.root / "stays").is_dir())
        self.assertTrue((self.root / "was-empty").is_dir())

    def test_undo_restores_everything(self):
        make(self.root, "a.txt", "sub/a.txt", "sub/deep/x.pdf", "c.jpg")
        before = snapshot(self.root)
        plan, _ = self.sort(recursive=True)
        core.prune_emptied_dirs(self.root, plan)
        self.assertNotEqual(snapshot(self.root), before)
        restored, problems = core.undo_last(self.root)
        self.assertEqual((restored, problems), (4, []))
        self.assertEqual(snapshot(self.root), before)
        self.assertFalse((self.root / core.LOG_NAME).exists())
        self.assertFalse((self.root / "txt").exists())  # emptied sort folders are removed again

    def test_undo_does_not_overwrite_a_new_file(self):
        make(self.root, "a.txt")
        self.sort()
        make(self.root, "a.txt")  # user creates a new file with the old name
        restored, problems = core.undo_last(self.root)
        self.assertEqual(restored, 0)
        self.assertEqual(len(problems), 1)
        self.assertEqual(set(snapshot(self.root)), {"a.txt", "txt/a.txt"})

    def test_log_uses_forward_slashes(self):
        make(self.root, "sub/a.txt")
        self.sort(recursive=True)
        log = json.loads((self.root / core.LOG_NAME).read_text(encoding="utf-8"))
        self.assertEqual(log[-1]["moves"], [{"from": "sub/a.txt", "to": "txt/a.txt"}])

    def test_unicode_and_spaces(self):
        make(self.root, "résumé final (2).pdf", "写真 01.jpg")
        self.sort()
        self.assertEqual(set(snapshot(self.root)), {"pdf/résumé final (2).pdf", "jpg/写真 01.jpg"})


if __name__ == "__main__":
    unittest.main()
