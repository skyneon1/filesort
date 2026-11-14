#!/usr/bin/env python3
# Copyright (c) 2026 FileSort. All rights reserved. See LICENSE.
"""TransferByExt - desktop window for sorting files into folders by extension."""
from __future__ import annotations

import queue
import sys
import threading
import tkinter as tk
from collections import defaultdict
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import filesort as core

APP_NAME = "FileSort"
MODE_LABELS = {"Extension": "extension", "File type": "type", "Month modified": "date"}
PREVIEW_LIMIT = 300  # file rows shown per folder in the preview

BG = "#f5f6f8"
CARD = "#ffffff"
TEXT = "#1f2933"
MUTED = "#6b7280"
ACCENT = "#2563eb"
ACCENT_DARK = "#1d4ed8"
BORDER = "#d9dde3"


class App(tk.Tk):
    def __init__(self, start_dir: str | None = None) -> None:
        super().__init__()
        self.title(APP_NAME)
        self.geometry("900x620")
        self.minsize(680, 460)
        self.configure(bg=BG)

        self.root_dir: Path | None = None
        self.plan: list[tuple[Path, Path]] = []
        self.busy = False
        self.scan_token = 0
        self.events: queue.Queue = queue.Queue()

        self.folder_var = tk.StringVar()
        self.recursive_var = tk.BooleanVar(value=False)
        self.hidden_var = tk.BooleanVar(value=False)
        self.prune_var = tk.BooleanVar(value=False)
        self.summary_var = tk.StringVar(value="Choose a folder to get started")
        self.status_var = tk.StringVar(value="")

        self._style()
        self._build()
        self._refresh_controls()
        self.after(50, self._poll)
        if start_dir:
            self.set_folder(start_dir)

    # ---------------------------------------------------------------- look
    def _style(self) -> None:
        style = ttk.Style(self)
        if sys.platform.startswith("linux"):
            style.theme_use("clam")
        style.configure(".", background=BG, foreground=TEXT)
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Title.TLabel", font=("TkDefaultFont", 15, "bold"))
        style.configure("Summary.TLabel", font=("TkDefaultFont", 11, "bold"))
        style.configure("TCheckbutton", background=BG, foreground=TEXT)
        style.map("TCheckbutton", background=[("active", BG)])
        style.configure("TButton", padding=(14, 7))
        style.configure("Accent.TButton", padding=(18, 8), font=("TkDefaultFont", 10, "bold"))
        if style.theme_use() == "clam":
            style.configure("TButton", background=CARD, bordercolor=BORDER, lightcolor=CARD, darkcolor=CARD)
            style.map("TButton", background=[("active", "#eef1f5")])
            style.configure("Accent.TButton", background=ACCENT, foreground="white",
                            bordercolor=ACCENT, lightcolor=ACCENT, darkcolor=ACCENT)
            style.map("Accent.TButton",
                      background=[("disabled", "#aab4c3"), ("active", ACCENT_DARK)],
                      foreground=[("disabled", "#eef1f5")],
                      bordercolor=[("disabled", "#aab4c3")],
                      lightcolor=[("disabled", "#aab4c3")],
                      darkcolor=[("disabled", "#aab4c3")])
            style.configure("TEntry", fieldbackground=CARD, bordercolor=BORDER, padding=6)
            style.configure("Treeview", background=CARD, fieldbackground=CARD, bordercolor=BORDER,
                            rowheight=26)
            style.configure("Treeview.Heading", background="#eceff3", foreground=MUTED,
                            relief="flat", padding=(8, 6))
            style.map("Treeview", background=[("selected", "#dbe7ff")], foreground=[("selected", TEXT)])
            style.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor="#e3e6eb",
                            bordercolor="#e3e6eb", lightcolor=ACCENT, darkcolor=ACCENT)
        else:
            style.configure("Treeview", rowheight=24)

    def _build(self) -> None:
        outer = ttk.Frame(self, padding=18)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(4, weight=1)

        # header
        head = ttk.Frame(outer)
        head.grid(row=0, column=0, sticky="ew")
        ttk.Label(head, text=APP_NAME, style="Title.TLabel").pack(side="left")
        ttk.Label(head, text="   Put every file into a folder by type, extension or month",
                  style="Muted.TLabel").pack(side="left", pady=(4, 0))

        # folder picker
        pick = ttk.Frame(outer)
        pick.grid(row=1, column=0, sticky="ew", pady=(16, 0))
        pick.columnconfigure(0, weight=1)
        self.folder_entry = ttk.Entry(pick, textvariable=self.folder_var)
        self.folder_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.folder_entry.bind("<Return>", lambda _e: self.set_folder(self.folder_var.get()))
        self.browse_btn = ttk.Button(pick, text="Browse…", command=self.browse)
        self.browse_btn.grid(row=0, column=1)

        # options
        opts = ttk.Frame(outer)
        opts.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        ttk.Label(opts, text="Sort by").pack(side="left")
        self.mode_box = ttk.Combobox(opts, state="readonly", width=15, values=list(MODE_LABELS))
        self.mode_box.current(0)
        self.mode_box.pack(side="left", padx=(8, 18))
        self.mode_box.bind("<<ComboboxSelected>>", lambda _e: self.scan())
        self.recursive_chk = ttk.Checkbutton(opts, text="Include subfolders",
                                             variable=self.recursive_var, command=self._options_changed)
        self.recursive_chk.pack(side="left")
        self.hidden_chk = ttk.Checkbutton(opts, text="Include hidden files",
                                          variable=self.hidden_var, command=self._options_changed)
        self.hidden_chk.pack(side="left", padx=(18, 0))
        self.prune_chk = ttk.Checkbutton(opts, text="Remove folders left empty", variable=self.prune_var)
        self.prune_chk.pack(side="left", padx=(18, 0))

        # summary line
        summ = ttk.Frame(outer)
        summ.grid(row=3, column=0, sticky="ew", pady=(18, 8))
        ttk.Label(summ, textvariable=self.summary_var, style="Summary.TLabel").pack(side="left")
        self.rescan_btn = ttk.Button(summ, text="Rescan", command=self.scan)
        self.rescan_btn.pack(side="right")

        # preview
        table = ttk.Frame(outer)
        table.grid(row=4, column=0, sticky="nsew")
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(table, columns=("count", "dest"), selectmode="browse")
        self.tree.heading("#0", text="Folder / file", anchor="w")
        self.tree.heading("count", text="Files", anchor="e")
        self.tree.heading("dest", text="Moves to", anchor="w")
        self.tree.column("#0", width=380, stretch=True)
        self.tree.column("count", width=80, anchor="e", stretch=False)
        self.tree.column("dest", width=320, stretch=True)
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.tag_configure("group", font=("TkDefaultFont", 10, "bold"))
        self.tree.tag_configure("more", foreground=MUTED)

        # footer
        foot = ttk.Frame(outer)
        foot.grid(row=5, column=0, sticky="ew", pady=(14, 0))
        foot.columnconfigure(0, weight=1)
        left = ttk.Frame(foot)
        left.grid(row=0, column=0, sticky="ew", padx=(0, 16))
        left.columnconfigure(0, weight=1)
        ttk.Label(left, textvariable=self.status_var, style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        self.progress = ttk.Progressbar(left, mode="determinate")
        self.progress.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self.progress.grid_remove()
        self.undo_btn = ttk.Button(foot, text="Undo last run", command=self.undo)
        self.undo_btn.grid(row=0, column=1, padx=(0, 8))
        self.go_btn = ttk.Button(foot, text="Organize files", style="Accent.TButton", command=self.organize)
        self.go_btn.grid(row=0, column=2)
        ttk.Label(outer, text="\u00a9 2026 FileSort", style="Muted.TLabel",
                  font=("TkDefaultFont", 8)).grid(row=6, column=0, sticky="e", pady=(10, 0))

    # ------------------------------------------------------- background work
    def _bg(self, job, done) -> None:
        def run() -> None:
            try:
                result = job()
            except Exception as exc:  # shown to the user, never swallowed
                self.events.put((self._failed, (exc,)))
            else:
                self.events.put((done, (result,)))

        threading.Thread(target=run, daemon=True).start()

    def _poll(self) -> None:
        try:
            while True:
                func, args = self.events.get_nowait()
                func(*args)
        except queue.Empty:
            pass
        self.after(50, self._poll)

    def _progress(self, done: int, total: int) -> None:
        if done == total or done % 25 == 0:
            self.events.put((self._show_progress, (done, total)))

    def _show_progress(self, done: int, total: int) -> None:
        self.progress.configure(maximum=total, value=done)

    def _failed(self, exc: Exception) -> None:
        self._set_busy(False)
        self.status_var.set("Something went wrong.")
        messagebox.showerror(APP_NAME, str(exc), parent=self)

    def _set_busy(self, busy: bool, status: str | None = None) -> None:
        self.busy = busy
        if status is not None:
            self.status_var.set(status)
        if busy:
            self.progress.configure(value=0)
            self.progress.grid()
        else:
            self.progress.grid_remove()
        self._refresh_controls()

    def _refresh_controls(self) -> None:
        def state(enabled: bool) -> str:
            return "normal" if enabled else "disabled"

        idle = not self.busy
        has_dir = self.root_dir is not None
        for widget in (self.folder_entry, self.browse_btn, self.recursive_chk, self.hidden_chk):
            widget.configure(state=state(idle))
        self.mode_box.configure(state="readonly" if idle else "disabled")
        self.prune_chk.configure(state=state(idle and self.recursive_var.get()))
        self.rescan_btn.configure(state=state(idle and has_dir))
        self.go_btn.configure(state=state(idle and bool(self.plan)))

        last = None
        if has_dir:
            try:
                last = core.last_run(self.root_dir)
            except RuntimeError:
                last = None
        if last:
            when = last["time"].replace("T", " ")[:16]
            self.undo_btn.configure(text=f"Undo last run ({len(last['moves'])} files, {when})")
        else:
            self.undo_btn.configure(text="Undo last run")
        self.undo_btn.configure(state=state(idle and bool(last)))

    # --------------------------------------------------------------- actions
    def browse(self) -> None:
        start = str(self.root_dir) if self.root_dir else str(Path.home())
        chosen = filedialog.askdirectory(parent=self, initialdir=start, title="Choose a folder to organize")
        if chosen:
            self.set_folder(chosen)

    def set_folder(self, raw: str) -> None:
        path = Path(raw.strip()).expanduser()
        if not raw.strip() or not path.is_dir():
            messagebox.showwarning(APP_NAME, f"That is not a folder:\n{raw}", parent=self)
            return
        self.root_dir = path.resolve()
        self.folder_var.set(str(self.root_dir))
        self.scan()

    def _options_changed(self) -> None:
        if not self.recursive_var.get():
            self.prune_var.set(False)
        self._refresh_controls()
        self.scan()

    def scan(self) -> None:
        if self.busy or self.root_dir is None:
            return
        self.scan_token += 1
        token, root = self.scan_token, self.root_dir
        recursive, hidden = self.recursive_var.get(), self.hidden_var.get()
        mode = MODE_LABELS[self.mode_box.get()]
        self.status_var.set("Scanning…")

        def done(plan) -> None:
            if token != self.scan_token or root != self.root_dir:
                return  # a newer scan replaced this one
            self.plan = plan
            self._show_plan()
            self.status_var.set("Preview only — nothing has been moved yet." if plan else "")
            self._refresh_controls()

        self._bg(lambda: core.scan(root, recursive, hidden, mode), done)

    def _show_plan(self) -> None:
        self.tree.delete(*self.tree.get_children())
        root = self.root_dir
        groups: dict[str, list[tuple[Path, Path]]] = defaultdict(list)
        for src, dest in self.plan:
            groups[dest.parent.name].append((src, dest))

        if not self.plan:
            self.summary_var.set("Nothing to move — this folder is already sorted")
            return
        self.summary_var.set(f"{len(self.plan)} file(s) will move into {len(groups)} folder(s)")

        for name, items in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            node = self.tree.insert("", "end", text=f" {name}/", values=(len(items), ""),
                                    tags=("group",), open=len(groups) <= 3)
            for src, dest in items[:PREVIEW_LIMIT]:
                self.tree.insert(node, "end", text=f" {src.relative_to(root)}",
                                 values=("", str(dest.relative_to(root))))
            if len(items) > PREVIEW_LIMIT:
                self.tree.insert(node, "end", text=f" … and {len(items) - PREVIEW_LIMIT} more",
                                 values=("", ""), tags=("more",))

    def organize(self) -> None:
        if self.busy or not self.plan or self.root_dir is None:
            return
        root, plan = self.root_dir, self.plan
        recursive = self.recursive_var.get()
        prune = recursive and self.prune_var.get()
        folders = len({dest.parent for _, dest in plan})

        message = f"Move {len(plan)} file(s) into {folders} folder(s) inside\n{root} ?"
        if recursive:
            message += "\n\nFiles inside subfolders will be pulled out of them."
        if root == Path.home() or root == Path(root.anchor):
            message += ("\n\nWARNING: this is a home or system folder. Reorganizing it can break "
                        "programs and projects that expect files where they are.")
        message += "\n\nYou can reverse this afterwards with “Undo last run”."
        if not messagebox.askokcancel(APP_NAME, message, parent=self):
            return

        self._set_busy(True, "Moving files…")

        def job():
            moves, failures = core.apply_plan(root, plan, self._progress)
            pruned = core.prune_emptied_dirs(root, plan) if prune else 0
            return moves, failures, pruned

        def done(result) -> None:
            moves, failures, pruned = result
            text = f"Moved {len(moves)} file(s)."
            if pruned:
                text += f" Removed {pruned} empty folder(s)."
            if failures:
                text += f" {len(failures)} could not be moved."
            self.plan = []
            self._show_plan()
            self._set_busy(False, text)
            self._rescan_keeping_status(text)
            if failures:
                self._show_problems("Some files could not be moved:",
                                    [f"{src.name}: {reason}" for src, reason in failures])

        self._bg(job, done)

    def undo(self) -> None:
        if self.busy or self.root_dir is None:
            return
        root = self.root_dir
        try:
            last = core.last_run(root)
        except RuntimeError as exc:
            messagebox.showerror(APP_NAME, str(exc), parent=self)
            return
        if not last:
            return
        if not messagebox.askokcancel(
                APP_NAME, f"Put {len(last['moves'])} file(s) back where they were?", parent=self):
            return
        self._set_busy(True, "Putting files back…")

        def done(result) -> None:
            restored, problems = result
            text = f"Restored {restored} file(s)."
            if problems:
                text += f" {len(problems)} problem(s)."
            self._set_busy(False, text)
            self._rescan_keeping_status(text)
            if problems:
                self._show_problems("Some files could not be restored:", problems)

        self._bg(lambda: core.undo_last(root, self._progress), done)

    def _rescan_keeping_status(self, text: str) -> None:
        """Refresh the preview after a run, but leave the result message visible."""
        self.scan_token += 1
        token, root = self.scan_token, self.root_dir
        recursive, hidden = self.recursive_var.get(), self.hidden_var.get()
        mode = MODE_LABELS[self.mode_box.get()]

        def done(plan) -> None:
            if token != self.scan_token or root != self.root_dir:
                return
            self.plan = plan
            self._show_plan()
            self.status_var.set(text)
            self._refresh_controls()

        self._bg(lambda: core.scan(root, recursive, hidden, mode), done)

    def _show_problems(self, heading: str, lines: list[str]) -> None:
        shown = "\n".join(lines[:12])
        if len(lines) > 12:
            shown += f"\n… and {len(lines) - 12} more"
        messagebox.showwarning(APP_NAME, f"{heading}\n\n{shown}", parent=self)


def main() -> None:
    if sys.platform == "win32":  # crisp text on high-DPI Windows screens
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    start = sys.argv[1] if len(sys.argv) > 1 else None
    App(start).mainloop()


if __name__ == "__main__":
    main()
