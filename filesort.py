#!/usr/bin/env python3
# Copyright (c) 2026 FileSort. All rights reserved. See LICENSE.
"""filesort - sort files into folders named after their extension.

    python3 filesort.py ~/Downloads            # preview, then ask
    python3 filesort.py ~/Downloads -r         # include subfolders
    python3 filesort.py ~/Downloads --dry-run  # preview only
    python3 filesort.py ~/Downloads --undo     # put everything back
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

LOG_NAME = ".filesort-undo.json"
NO_EXT_DIR = "no_extension"


MODES = ("extension", "type", "date")

CATEGORIES = {
    "Images": "jpg jpeg png gif webp bmp tif tiff svg heic heif ico avif raw cr2 nef arw dng",
    "Videos": "mp4 mkv mov avi webm wmv flv m4v mpg mpeg 3gp",
    "Audio": "mp3 wav flac aac ogg m4a wma opus aiff mid midi",
    "Documents": "pdf doc docx odt rtf txt md tex pages",
    "Spreadsheets": "xls xlsx xlsm ods csv tsv numbers",
    "Presentations": "ppt pptx odp key",
    "Ebooks": "epub mobi azw azw3 djvu",
    "Archives": "zip rar 7z tar gz bz2 xz tgz zst iso",
    "Installers": "exe msi dmg pkg deb rpm appimage apk",
    "Code": "py js ts jsx tsx html css scss json xml yml yaml toml sh bat ps1 c h cpp hpp cs java "
            "kt go rs rb php swift sql ipynb lua r",
    "Design": "psd ai fig sketch xd indd blend obj stl fbx",
    "Fonts": "ttf otf woff woff2",
}
EXT_TO_CATEGORY = {ext: name for name, exts in CATEGORIES.items() for ext in exts.split()}


def folder_for(path: Path, mode: str = "extension") -> str:
    """Folder a file belongs in.

    extension: 'photo.JPG' -> 'jpg', 'Makefile' -> 'no_extension'
    type:      'photo.JPG' -> 'Images', unknown -> 'Other'
    date:      last-modified month, e.g. '2026-11'
    """
    ext = path.suffix.lower().lstrip(".")
    if mode == "type":
        return EXT_TO_CATEGORY.get(ext, "Other")
    if mode == "date":
        return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m")
    return ext or NO_EXT_DIR


# Files that belong to the operating system, never sorted.
SKIP_FILES = {"desktop.ini", "thumbs.db"}
# Folders that only work as a whole. "Include subfolders" never reaches inside them.
SKIP_DIRS = {"node_modules", "__pycache__"}
PACKAGE_SUFFIXES = (".app", ".framework", ".bundle", ".plugin", ".kext", ".photoslibrary",
                    ".xcodeproj", ".xcworkspace")


def is_hidden(entry: Path) -> bool:
    """Dotfiles everywhere, plus files flagged hidden on Windows."""
    if entry.name.startswith("."):
        return True
    try:
        return bool(getattr(entry.lstat(), "st_file_attributes", 0) & 2)
    except OSError:
        return False


def is_protected_dir(directory: Path) -> bool:
    """App bundles, dependency folders and git repositories break if taken apart."""
    name = directory.name.lower()
    return name in SKIP_DIRS or name.endswith(PACKAGE_SUFFIXES) or (directory / ".git").exists()


def find_files(root: Path, recursive: bool, include_hidden: bool) -> list[Path]:
    """Collect the files that should be considered for moving."""
    own_files = {Path(__file__).resolve(), Path(sys.executable).resolve()}
    found: list[Path] = []

    def walk(directory: Path) -> None:
        try:
            entries = sorted(directory.iterdir())
        except PermissionError:
            print(f"  skipped (no permission): {directory}", file=sys.stderr)
            return
        for entry in entries:
            if entry.name == LOG_NAME or entry.name.lower() in SKIP_FILES:
                continue
            if not include_hidden and is_hidden(entry):
                continue
            if entry.is_symlink():
                continue
            if entry.is_dir():
                if recursive and not is_protected_dir(entry):
                    walk(entry)
            elif entry.is_file() and entry.resolve() not in own_files:
                found.append(entry)

    walk(root)
    return found


def unique_destination(dest: Path, taken: set[Path]) -> Path:
    """Avoid overwriting: 'a.txt' -> 'a (1).txt' if the name is already used."""
    candidate, n = dest, 1
    while candidate.exists() or candidate in taken:
        candidate = dest.with_name(f"{dest.stem} ({n}){dest.suffix}")
        n += 1
    return candidate


def build_plan(root: Path, files: list[Path], mode: str = "extension") -> list[tuple[Path, Path]]:
    plan: list[tuple[Path, Path]] = []
    taken: set[Path] = set()
    for src in files:
        try:
            folder = folder_for(src, mode)
        except OSError:  # file vanished or cannot be read
            continue
        dest_dir = root / folder
        # Already sorted. Compared without case so "JPG/" and "jpg/" count as the
        # same folder, as they are on Windows and macOS disks.
        if src.parent.parent == root and src.parent.name.lower() == folder.lower():
            continue
        dest = unique_destination(dest_dir / src.name, taken)
        taken.add(dest)
        plan.append((src, dest))
    return plan


def print_plan(root: Path, plan: list[tuple[Path, Path]], verbose: bool) -> None:
    counts = Counter(dest.parent.name for _, dest in plan)
    print(f"\n{len(plan)} file(s) to move into {len(counts)} folder(s) in {root}:\n")
    for name, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"  {name + '/':<20} {count:>5} file(s)")
    if verbose:
        print()
        for src, dest in plan:
            print(f"  {src.relative_to(root)}  ->  {dest.relative_to(root)}")
    print()


def prune_emptied_dirs(root: Path, plan: list[tuple[Path, Path]]) -> int:
    """Remove the folders this run emptied. Folders that were already empty,
    or that still hold anything, are left alone."""
    candidates: set[Path] = set()
    for src, _ in plan:
        directory = src.parent
        while directory != root and root in directory.parents:
            candidates.add(directory)
            directory = directory.parent
    removed = 0
    for directory in sorted(candidates, key=lambda d: len(d.parts), reverse=True):
        try:
            directory.rmdir()  # only succeeds when empty
            removed += 1
        except OSError:
            pass
    return removed


def load_log(root: Path) -> list[dict]:
    log_path = root / LOG_NAME
    if not log_path.exists():
        return []
    try:
        return json.loads(log_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        raise RuntimeError(f"Could not read {log_path}; it looks corrupted.")


def last_run(root: Path) -> dict | None:
    """The most recent run recorded in this directory, if any."""
    runs = load_log(root)
    return runs[-1] if runs else None


def scan(root: Path, recursive: bool = False, include_hidden: bool = False,
         mode: str = "extension") -> list[tuple[Path, Path]]:
    """Work out which files would move where. Touches nothing."""
    return build_plan(root, find_files(root, recursive, include_hidden), mode)


def apply_plan(root: Path, plan: list[tuple[Path, Path]], on_progress=None):
    """Move the files and record the run. Returns (moves, failures)."""
    moves: list[dict] = []
    failures: list[tuple[Path, str]] = []
    for i, (src, dest) in enumerate(plan, 1):
        try:
            dest.parent.mkdir(exist_ok=True)
            dest = unique_destination(dest, set())  # re-check: never overwrite
            shutil.move(str(src), str(dest))
            moves.append({"from": src.relative_to(root).as_posix(), "to": dest.relative_to(root).as_posix()})
        except OSError as exc:
            failures.append((src, str(exc)))
        if on_progress:
            on_progress(i, len(plan))
    if moves:
        runs = load_log(root)
        runs.append({"time": datetime.now().isoformat(timespec="seconds"), "moves": moves})
        (root / LOG_NAME).write_text(json.dumps(runs, indent=2), encoding="utf-8")
    return moves, failures


def undo_last(root: Path, on_progress=None):
    """Reverse the most recent run. Returns (restored_count, problems)."""
    runs = load_log(root)
    if not runs:
        return 0, ["Nothing to undo (no log found in this directory)."]
    last = runs[-1]
    restored, remaining, problems, touched = 0, [], [], set()
    total = len(last["moves"])
    for i, move in enumerate(reversed(last["moves"]), 1):
        current, original = root / move["to"], root / move["from"]
        if not current.exists():
            problems.append(f"missing, skipped: {move['to']}")
        elif original.exists():
            problems.append(f"original path is occupied, skipped: {move['from']}")
            remaining.append(move)
        else:
            try:
                original.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(current), str(original))
                touched.add(current.parent)
                restored += 1
            except OSError as exc:
                problems.append(f"failed: {move['to']} ({exc})")
                remaining.append(move)
        if on_progress:
            on_progress(i, total)

    for directory in touched:  # drop extension folders we emptied
        try:
            directory.rmdir()
        except OSError:
            pass

    runs.pop()
    if remaining:
        runs.append({"time": last["time"], "moves": list(reversed(remaining))})
    log_path = root / LOG_NAME
    if runs:
        log_path.write_text(json.dumps(runs, indent=2), encoding="utf-8")
    else:
        log_path.unlink()
    return restored, problems


def organize(root: Path, args: argparse.Namespace) -> int:
    plan = scan(root, args.recursive, args.hidden, args.by)
    if not plan:
        print("Nothing to do - everything is already sorted.")
        return 0

    print_plan(root, plan, args.verbose)
    if args.dry_run:
        print("Dry run: nothing was moved.")
        return 0
    if not args.yes:
        if input("Proceed? [y/N] ").strip().lower() not in ("y", "yes"):
            print("Cancelled.")
            return 1

    moves, failures = apply_plan(root, plan)
    for src, reason in failures:
        print(f"  failed: {src} ({reason})", file=sys.stderr)

    pruned = 0
    if args.recursive and args.prune_empty:
        pruned = prune_emptied_dirs(root, plan)

    print(f"Moved {len(moves)} file(s).", end="")
    if pruned:
        print(f" Removed {pruned} empty folder(s).", end="")
    if failures:
        print(f" {len(failures)} failed.", end="")
    print(f"\nUndo with: python3 {Path(__file__).name} {root} --undo")
    return 1 if failures else 0


def undo(root: Path, args: argparse.Namespace) -> int:
    last = last_run(root)
    if not last:
        print("Nothing to undo (no log found in this directory).")
        return 1
    print(f"Undoing run from {last['time']}: {len(last['moves'])} file(s).")
    if args.dry_run:
        print("Dry run: nothing was moved.")
        return 0
    restored, problems = undo_last(root)
    for problem in problems:
        print(f"  {problem}", file=sys.stderr)
    print(f"Restored {restored} file(s)." + (f" {len(problems)} problem(s)." if problems else ""))
    return 1 if problems else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sort files into folders named after their extension."
    )
    parser.add_argument("directory", nargs="?", default=".", help="directory to organize (default: current)")
    parser.add_argument("--by", choices=MODES, default="extension",
                        help="group by extension (default), file type, or month modified")
    parser.add_argument("-r", "--recursive", action="store_true", help="also pull files out of subfolders")
    parser.add_argument("-n", "--dry-run", action="store_true", help="show what would happen, move nothing")
    parser.add_argument("-y", "--yes", action="store_true", help="don't ask for confirmation")
    parser.add_argument("-v", "--verbose", action="store_true", help="list every file move")
    parser.add_argument("--hidden", action="store_true", help="include hidden files (dotfiles)")
    parser.add_argument("--prune-empty", action="store_true", help="with -r: delete folders left empty")
    parser.add_argument("--undo", action="store_true", help="reverse the last run in this directory")
    args = parser.parse_args()

    root = Path(args.directory).expanduser().resolve()
    if not root.is_dir():
        parser.error(f"not a directory: {root}")

    try:
        return undo(root, args) if args.undo else organize(root, args)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
