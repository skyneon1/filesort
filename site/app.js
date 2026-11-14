/* Copyright (c) 2026 FileSort. All rights reserved. See LICENSE. */
// FileSort web app. Everything runs in the browser through the File System
// Access API: no upload, no server, no account.

const LOG_NAME = ".filesort-undo.json"; // same log the desktop app writes
const NO_EXT = "no_extension";
const PREVIEW_LIMIT = 200;

// Where the download buttons point: the files of the latest GitHub release.
const RELEASE = "https://github.com/skyneon1/filesort/releases/latest/download/";
const DOWNLOADS = {
  windows: RELEASE + "FileSort-windows.exe",
  macos: RELEASE + "FileSort-macos.zip",
  linux: RELEASE + "FileSort-linux.tar.gz",
};

const CATEGORIES = {
  Images: "jpg jpeg png gif webp bmp tif tiff svg heic heif ico avif raw cr2 nef arw dng",
  Videos: "mp4 mkv mov avi webm wmv flv m4v mpg mpeg 3gp",
  Audio: "mp3 wav flac aac ogg m4a wma opus aiff mid midi",
  Documents: "pdf doc docx odt rtf txt md tex pages",
  Spreadsheets: "xls xlsx xlsm ods csv tsv numbers",
  Presentations: "ppt pptx odp key",
  Ebooks: "epub mobi azw azw3 djvu",
  Archives: "zip rar 7z tar gz bz2 xz tgz zst iso",
  Installers: "exe msi dmg pkg deb rpm appimage apk",
  Code: "py js ts jsx tsx html css scss json xml yml yaml toml sh bat ps1 c h cpp hpp cs java kt go rs rb php swift sql ipynb lua r",
  Design: "psd ai fig sketch xd indd blend obj stl fbx",
  Fonts: "ttf otf woff woff2",
};
const EXT_TO_CATEGORY = {};
for (const [name, exts] of Object.entries(CATEGORIES))
  for (const ext of exts.split(" ")) EXT_TO_CATEGORY[ext] = name;

// Files that belong to the operating system, never sorted.
const SKIP_FILES = new Set(["desktop.ini", "thumbs.db"]);
// Folders that only work as a whole. "Include subfolders" never reaches inside them.
const SKIP_DIRS = new Set([".git", "node_modules", "__pycache__"]);
const PACKAGE_SUFFIXES = [".app", ".framework", ".bundle", ".plugin", ".kext", ".photoslibrary", ".xcodeproj", ".xcworkspace"];

// ------------------------------------------------------------------ core

// App bundles, dependency folders and git repositories break if taken apart.
async function isProtectedDir(name, handle) {
  const lower = name.toLowerCase();
  if (SKIP_DIRS.has(lower) || PACKAGE_SUFFIXES.some((suffix) => lower.endsWith(suffix))) return true;
  try { await handle.getDirectoryHandle(".git"); return true; } catch (_) { return false; }
}

function splitName(name) {
  const dot = name.lastIndexOf(".");
  return dot > 0 ? [name.slice(0, dot), name.slice(dot)] : [name, ""];
}

function folderFor(name, mode, modified) {
  const ext = splitName(name)[1].slice(1).toLowerCase();
  if (mode === "type") return EXT_TO_CATEGORY[ext] || "Other";
  if (mode === "date") {
    const d = new Date(modified);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
  }
  return ext || NO_EXT;
}

function uniqueName(name, taken) {
  const [stem, ext] = splitName(name);
  let candidate = name;
  for (let n = 1; taken.has(candidate.toLowerCase()); n++) candidate = `${stem} (${n})${ext}`;
  return candidate;
}

async function namesIn(root, folder) {
  const names = new Set();
  try {
    const dir = await root.getDirectoryHandle(folder);
    for await (const name of dir.keys()) names.add(name.toLowerCase());
  } catch (_) { /* folder does not exist yet */ }
  return names;
}

// Work out what would move where. Changes nothing on disk.
async function scan(root, { mode, recursive, hidden }) {
  const files = [];
  async function walk(dir, path) {
    for await (const [name, handle] of dir.entries()) {
      if (name === LOG_NAME || SKIP_FILES.has(name.toLowerCase())) continue;
      if (name.startsWith(".") && !hidden) continue;
      if (handle.kind === "directory") {
        if (recursive && !(await isProtectedDir(name, handle))) await walk(handle, [...path, name]);
      } else {
        files.push({ name, handle, parent: dir, path });
      }
    }
  }
  await walk(root, []);
  files.sort((a, b) => [...a.path, a.name].join("/").localeCompare([...b.path, b.name].join("/")));

  const taken = new Map();
  const plan = [];
  for (const f of files) {
    const modified = mode === "date" ? (await f.handle.getFile()).lastModified : 0;
    const folder = folderFor(f.name, mode, modified);
    // Already sorted. Compared without case: "JPG/" and "jpg/" are one folder on Windows and macOS.
    if (f.path.length === 1 && f.path[0].toLowerCase() === folder.toLowerCase()) continue;
    if (!taken.has(folder)) taken.set(folder, await namesIn(root, folder));
    const destName = uniqueName(f.name, taken.get(folder));
    taken.get(folder).add(destName.toLowerCase());
    plan.push({ ...f, folder, destName, from: [...f.path, f.name].join("/"), to: `${folder}/${destName}` });
  }
  return plan;
}

async function exists(dir, name) {
  try { await dir.getFileHandle(name); return true; } catch (_) {}
  try { await dir.getDirectoryHandle(name); return true; } catch (_) {}
  return false;
}

// Move one file. Uses the browser's native move when it has one, otherwise
// copies the bytes and deletes the original only after the copy succeeded.
async function moveFile(handle, parent, name, destDir, destName) {
  if (typeof handle.move === "function") {
    try { await handle.move(destDir, destName); return; } catch (_) { /* fall back to copy */ }
  }
  const file = await handle.getFile();
  const dest = await destDir.getFileHandle(destName, { create: true });
  try {
    const writable = await dest.createWritable();
    await file.stream().pipeTo(writable);
  } catch (err) {
    try { await destDir.removeEntry(destName); } catch (_) {}
    throw err;
  }
  await parent.removeEntry(name);
}

async function readLog(root) {
  try {
    const file = await (await root.getFileHandle(LOG_NAME)).getFile();
    const runs = JSON.parse(await file.text());
    return Array.isArray(runs) ? runs : [];
  } catch (_) { return []; }
}

async function writeLog(root, runs) {
  if (!runs.length) {
    try { await root.removeEntry(LOG_NAME); } catch (_) {}
    return;
  }
  const writable = await (await root.getFileHandle(LOG_NAME, { create: true })).createWritable();
  await writable.write(JSON.stringify(runs, null, 2));
  await writable.close();
}

async function applyPlan(root, plan, onProgress) {
  const moves = [], failures = [];
  for (let i = 0; i < plan.length; i++) {
    const item = plan[i];
    try {
      const destDir = await root.getDirectoryHandle(item.folder, { create: true });
      let destName = item.destName;
      if (await exists(destDir, destName)) destName = uniqueName(destName, await namesIn(root, item.folder));
      await moveFile(item.handle, item.parent, item.name, destDir, destName);
      moves.push({ from: item.from, to: `${item.folder}/${destName}` });
    } catch (err) {
      failures.push(`${item.from}: ${err.message || err.name}`);
    }
    if (onProgress) onProgress(i + 1, plan.length);
  }
  if (moves.length) {
    const run = { time: new Date().toISOString().slice(0, 19), moves };
    try {
      const runs = await readLog(root);
      runs.push(run);
      await writeLog(root, runs);
    } catch (_) { state.memoryRun = run; } // browser cannot write the log: keep undo for this visit
  }
  return { moves, failures };
}

async function resolveDir(root, parts, create) {
  let dir = root;
  for (const part of parts) dir = await dir.getDirectoryHandle(part, { create });
  return dir;
}

async function undoLast(root, onProgress) {
  const runs = await readLog(root);
  const last = runs.length ? runs.pop() : state.memoryRun;
  if (!last) return { restored: 0, problems: ["Nothing to undo."] };
  const fromMemory = last === state.memoryRun;
  const remaining = [], problems = [], touched = new Set();
  let restored = 0;
  const moves = [...last.moves].reverse();
  for (let i = 0; i < moves.length; i++) {
    const move = moves[i];
    const toParts = move.to.split("/"), fromParts = move.from.split("/");
    const toName = toParts.pop(), fromName = fromParts.pop();
    try {
      const currentDir = await resolveDir(root, toParts, false);
      const handle = await currentDir.getFileHandle(toName);
      const originalDir = await resolveDir(root, fromParts, true);
      if (await exists(originalDir, fromName)) {
        problems.push(`original name is taken, skipped: ${move.from}`);
        remaining.push(move);
      } else {
        await moveFile(handle, currentDir, toName, originalDir, fromName);
        touched.add(toParts.join("/"));
        restored++;
      }
    } catch (err) {
      problems.push(`could not restore ${move.to} (${err.message || err.name})`);
    }
    if (onProgress) onProgress(i + 1, moves.length);
  }
  for (const path of touched) { // remove sort folders that are empty again
    try { await root.removeEntry(path); } catch (_) {}
  }
  if (fromMemory) state.memoryRun = null;
  else {
    if (remaining.length) runs.push({ time: last.time, moves: remaining.reverse() });
    await writeLog(root, runs);
  }
  return { restored, problems };
}

// Remove the folders this run emptied. Folders that were already empty, or
// that still hold anything, are left alone. Returns how many were removed.
async function pruneEmpty(root, plan) {
  const paths = new Map();
  for (const item of plan)
    for (let depth = item.path.length; depth > 0; depth--) paths.set(item.path.slice(0, depth).join("/"), item.path.slice(0, depth));
  let removed = 0;
  for (const parts of [...paths.values()].sort((a, b) => b.length - a.length)) {
    try {
      const parent = await resolveDir(root, parts.slice(0, -1), false);
      await parent.removeEntry(parts[parts.length - 1]); // fails unless the folder is empty
      removed++;
    } catch (_) {}
  }
  return removed;
}

// ------------------------------------------- sorted copy as .zip (other browsers)
// Firefox, Safari and phones cannot change files on disk. There the folder is
// read (never uploaded) and a sorted copy is handed back as a .zip file.

const ZIP_MAX_BYTES = 3.5e9; // plain zip format: 4 GB total, 65535 files
const ZIP_MAX_FILES = 60000;

function isProtectedPath(path, repoDirs) {
  for (let depth = 1; depth <= path.length; depth++) {
    const lower = path[depth - 1].toLowerCase();
    if (SKIP_DIRS.has(lower) || PACKAGE_SUFFIXES.some((suffix) => lower.endsWith(suffix))) return true;
    if (repoDirs.has(path.slice(0, depth).join("/"))) return true;
  }
  return false;
}

// Same rules as scan(), for a list of { file, name, path } read from a file input.
function scanFiles(items, { mode, recursive, hidden }) {
  const repoDirs = new Set(); // folders that contain a .git folder
  for (const it of items) {
    const at = it.path.indexOf(".git");
    if (at > 0) repoDirs.add(it.path.slice(0, at).join("/"));
  }
  const usable = items.filter((it) => {
    if (it.name === LOG_NAME || SKIP_FILES.has(it.name.toLowerCase())) return false;
    if (!recursive && it.path.length) return false;
    if (!hidden && (it.name.startsWith(".") || it.path.some((part) => part.startsWith(".")))) return false;
    return !isProtectedPath(it.path, repoDirs);
  }).map((it) => ({ ...it, folder: folderFor(it.name, mode, it.file.lastModified), from: [...it.path, it.name].join("/") }));
  usable.sort((a, b) => a.from.localeCompare(b.from));

  const taken = new Map();
  const names = (folder) => { if (!taken.has(folder)) taken.set(folder, new Set()); return taken.get(folder); };
  const plan = [];
  plan.kept = []; // already in the right folder: copied as they are
  for (const it of usable) {
    if (it.path.length === 1 && it.path[0].toLowerCase() === it.folder.toLowerCase()) {
      names(it.folder).add(it.name.toLowerCase());
      plan.kept.push({ file: it.file, to: it.from });
    }
  }
  for (const it of usable) {
    if (it.path.length === 1 && it.path[0].toLowerCase() === it.folder.toLowerCase()) continue;
    const destName = uniqueName(it.name, names(it.folder));
    names(it.folder).add(destName.toLowerCase());
    plan.push({ ...it, destName, to: `${it.folder}/${destName}` });
  }
  return plan;
}

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    table[n] = c >>> 0;
  }
  return table;
})();

async function crcOfFile(file) {
  let crc = 0xffffffff;
  const feed = (bytes) => { for (let i = 0; i < bytes.length; i++) crc = CRC_TABLE[(crc ^ bytes[i]) & 0xff] ^ (crc >>> 8); };
  if (file.stream) {
    const reader = file.stream().getReader();
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      feed(value);
    }
  } else {
    feed(new Uint8Array(await file.arrayBuffer()));
  }
  return (crc ^ 0xffffffff) >>> 0;
}

// Builds an uncompressed .zip. Files are referenced, not copied into memory.
async function buildZip(entries, onProgress) {
  const encoder = new TextEncoder();
  const parts = [], central = [];
  let offset = 0;
  for (let i = 0; i < entries.length; i++) {
    const { path, file } = entries[i];
    const name = encoder.encode(path);
    const crc = await crcOfFile(file);
    const d = new Date(file.lastModified || Date.now());
    const time = (d.getHours() << 11) | (d.getMinutes() << 5) | (d.getSeconds() >> 1);
    const date = ((Math.max(d.getFullYear(), 1980) - 1980) << 9) | ((d.getMonth() + 1) << 5) | d.getDate();

    const local = new DataView(new ArrayBuffer(30));
    local.setUint32(0, 0x04034b50, true);
    local.setUint16(4, 20, true);
    local.setUint16(6, 0x0800, true); // file names are UTF-8
    local.setUint16(10, time, true);
    local.setUint16(12, date, true);
    local.setUint32(14, crc, true);
    local.setUint32(18, file.size, true);
    local.setUint32(22, file.size, true);
    local.setUint16(26, name.length, true);
    parts.push(local, name, file);

    const entry = new DataView(new ArrayBuffer(46));
    entry.setUint32(0, 0x02014b50, true);
    entry.setUint16(4, 0x0314, true); // made on Unix: every unzip tool then reads names as UTF-8
    entry.setUint16(6, 20, true);
    entry.setUint16(8, 0x0800, true);
    entry.setUint16(12, time, true);
    entry.setUint16(14, date, true);
    entry.setUint32(16, crc, true);
    entry.setUint32(20, file.size, true);
    entry.setUint32(24, file.size, true);
    entry.setUint16(28, name.length, true);
    entry.setUint32(38, (0o100644 << 16) >>> 0, true); // normal file permissions
    entry.setUint32(42, offset, true);
    central.push(entry, name);

    offset += 30 + name.length + file.size;
    if (onProgress) onProgress(i + 1, entries.length);
  }
  const centralSize = central.reduce((total, part) => total + part.byteLength, 0);
  const end = new DataView(new ArrayBuffer(22));
  end.setUint32(0, 0x06054b50, true);
  end.setUint16(8, entries.length, true);
  end.setUint16(10, entries.length, true);
  end.setUint32(12, centralSize, true);
  end.setUint32(16, offset, true);
  return new Blob([...parts, ...central, end], { type: "application/zip" });
}

function saveBlob(blob, filename) {
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 60000);
}

// -------------------------------------------------------------------- UI

const $ = (id) => document.getElementById(id);
const state = { root: null, files: null, rootName: "", plan: [], busy: false, token: 0, memoryRun: null, demo: false };
const canPick = "showDirectoryPicker" in window;
const canDemo = !!(navigator.storage && navigator.storage.getDirectory);
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

function options() {
  return {
    mode: document.querySelector("#modes button.on").dataset.mode,
    recursive: $("opt-recursive").checked,
    hidden: $("opt-hidden").checked,
    prune: $("opt-recursive").checked && $("opt-prune").checked,
  };
}

function escapeHtml(text) {
  return text.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

function setStatus(text, kind = "") {
  $("status").textContent = text;
  $("status").className = "status " + kind;
}

function setNote(text) {
  $("note").textContent = text;
  $("note").hidden = !text;
}

function setBusy(busy) {
  state.busy = busy;
  $("progress").hidden = !busy;
  if (busy) $("progress-bar").style.width = "0%";
  refreshButtons();
}

function showProgress(done, total) {
  $("progress-bar").style.width = `${Math.round((done / total) * 100)}%`;
}

async function refreshButtons() {
  const idle = !state.busy;
  const count = plural(state.plan.length, "file");
  $("go").disabled = !idle || !state.plan.length;
  if (state.files) $("go").textContent = state.plan.length ? `Download sorted copy of ${count} (.zip)` : "Download sorted copy (.zip)";
  else $("go").textContent = state.plan.length ? `Sort ${count}` : "Sort files";
  for (const el of document.querySelectorAll("#workspace input, #modes button, #rescan, #change")) el.disabled = !idle;
  $("opt-prune").disabled = !idle || !$("opt-recursive").checked;
  $("prune-label").hidden = !!state.files;
  $("undo").hidden = !!state.files;
  $("rescan").hidden = !!state.files;
  $("zip-note").hidden = !state.files;
  let last = null;
  if (state.root) {
    const runs = await readLog(state.root);
    last = runs[runs.length - 1] || state.memoryRun;
  }
  $("undo").disabled = !idle || !last;
  $("undo").textContent = last ? `Undo last sort (${plural(last.moves.length, "file")})` : "Undo last sort";
}

function render() {
  const groups = new Map();
  for (const item of state.plan) {
    if (!groups.has(item.folder)) groups.set(item.folder, []);
    groups.get(item.folder).push(item);
  }
  const sorted = [...groups.entries()].sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0]));
  const list = $("rows");
  list.innerHTML = "";
  if (!sorted.length) {
    $("summary").textContent = "Nothing to sort. Every file here is already in its folder.";
    return;
  }
  $("summary").textContent = `${plural(state.plan.length, "file")} will go into ${plural(sorted.length, "folder")}. Click a folder to see its files.`;
  for (const [folder, items] of sorted) {
    const row = document.createElement("details");
    const files = items.slice(0, PREVIEW_LIMIT).map((it) =>
      `<li>${escapeHtml(it.from)}${it.destName !== it.name ? ` <em>renamed to ${escapeHtml(it.destName)}</em>` : ""}</li>`).join("");
    const more = items.length > PREVIEW_LIMIT ? `<li><em>and ${items.length - PREVIEW_LIMIT} more</em></li>` : "";
    row.innerHTML = `<summary><span class="name">${escapeHtml(folder)}</span><span class="count">${plural(items.length, "file")}</span></summary><ul>${files}${more}</ul>`;
    list.appendChild(row);
  }
}

async function rescan(keepStatus) {
  if (!state.root && !state.files) return;
  const token = ++state.token;
  if (!keepStatus) setStatus("Reading folder…");
  try {
    const plan = state.files ? scanFiles(state.files, options()) : await scan(state.root, options());
    if (token !== state.token) return; // a newer scan replaced this one
    state.plan = plan;
    render();
    if (!keepStatus) setStatus(plan.length && !state.files ? "Nothing has been moved yet." : "");
  } catch (err) {
    if (token !== state.token) return;
    state.plan = [];
    render();
    setStatus(`Could not read this folder: ${err.message || err.name}`, "bad");
  }
  refreshButtons();
}

async function openFolder(handle, demo = false) {
  state.root = handle;
  state.files = null;
  state.demo = demo;
  state.memoryRun = null;
  $("folder-name").textContent = demo ? "Sample files (not your real files)" : handle.name;
  $("picker").hidden = true;
  $("workspace").hidden = false;
  setNote("");
  await rescan();
}

// Other browsers: files chosen through a normal file input. Read-only.
async function openFiles(fileList) {
  const files = [...fileList];
  if (!files.length) return;
  const withFolder = files.some((f) => f.webkitRelativePath);
  state.files = files.map((file) => {
    const parts = (file.webkitRelativePath || file.name).split("/");
    const name = parts.pop();
    return { file, name, path: withFolder ? parts.slice(1) : [] };
  });
  state.rootName = withFolder ? files[0].webkitRelativePath.split("/")[0] : "files";
  state.root = null;
  state.demo = false;
  $("folder-name").textContent = withFolder ? state.rootName : plural(files.length, "selected file");
  $("picker").hidden = true;
  $("workspace").hidden = false;
  setNote("");
  await rescan();
}

function closeFolder() {
  if (state.busy) return;
  state.root = null;
  state.files = null;
  state.plan = [];
  state.token++;
  $("dir-input").value = "";
  $("workspace").hidden = true;
  $("picker").hidden = false;
}

async function pickFolder() {
  if (state.busy) return;
  try {
    await openFolder(await window.showDirectoryPicker({ mode: "readwrite", id: "filesort" }));
  } catch (err) {
    if (err.name !== "AbortError") setNote(`Could not open that folder: ${err.message}`);
  }
}

const DEMO_FILES = [
  "IMG_2041.JPG", "IMG_2042.JPG", "screenshot 2026-03-01.png", "logo-final-v3.png", "meme.gif",
  "invoice-march.pdf", "resume.pdf", "resume.docx", "notes.txt", "todo.md", "budget.xlsx", "contacts.csv",
  "pitch-deck.pptx", "holiday.mp4", "screen-recording.mov", "podcast-ep12.mp3", "voice-memo.m4a",
  "backup.zip", "photos.tar.gz", "setup.exe", "installer.dmg", "script.py", "index.html", "styles.css",
  "data.json", "font.ttf", "mockup.psd", "book.epub", "README", "old stuff/resume.pdf",
  "old stuff/IMG_1999.jpg", "old stuff/archive/taxes-2024.pdf", "old stuff/archive/song.mp3",
];

async function openDemo() {
  if (state.busy) return;
  try {
    const sandbox = await navigator.storage.getDirectory();
    try { await sandbox.removeEntry("sample-files", { recursive: true }); } catch (_) {}
    const dir = await sandbox.getDirectoryHandle("sample-files", { create: true });
    for (const path of DEMO_FILES) {
      const parts = path.split("/");
      const name = parts.pop();
      await (await resolveDir(dir, parts, true)).getFileHandle(name, { create: true });
    }
    await openFolder(dir, true);
  } catch (err) {
    setNote(`Sample files are not available in this browser (${err.message || err.name}).`);
  }
}

async function downloadSortedCopy() {
  const plan = state.plan;
  const all = [...plan.map((p) => ({ file: p.file, to: p.to })), ...(plan.kept || [])];
  const bytes = all.reduce((total, entry) => total + entry.file.size, 0);
  if (all.length > ZIP_MAX_FILES || bytes > ZIP_MAX_BYTES) {
    return setStatus("This folder is too large for a .zip copy in the browser (limit: 3.5 GB or 60,000 files). Use Chrome or Edge, or the desktop app.", "bad");
  }
  const folders = new Set(plan.map((p) => p.folder));
  const top = `${state.rootName} sorted`;
  setBusy(true);
  setStatus("Preparing the sorted copy…");
  try {
    const blob = await buildZip(all.map((entry) => ({ path: `${top}/${entry.to}`, file: entry.file })), showProgress);
    saveBlob(blob, `${top}.zip`);
    setBusy(false);
    setStatus(`Done. "${top}.zip" holds ${plural(all.length, "file")} in ${plural(folders.size, "folder")}. Unpack it to get the sorted folder. Your original files were not changed.`, "good");
  } catch (err) {
    setBusy(false);
    setStatus(`Could not create the copy: ${err.message || err.name}`, "bad");
  }
}

async function organize() {
  if (state.busy || !state.plan.length) return;
  if (state.files) return downloadSortedCopy();
  const opts = options();
  const plan = state.plan;
  const folders = new Set(plan.map((p) => p.folder));
  let message = `Move ${plural(plan.length, "file")} into ${plural(folders.size, "folder")} inside "${state.root.name}"?`;
  if (opts.recursive) message += "\n\nFiles inside subfolders will be taken out of them.";
  message += "\n\nYou can put everything back with Undo.";
  if (!state.demo && !window.confirm(message)) return;

  setBusy(true);
  setStatus("Moving files…");
  const { moves, failures } = await applyPlan(state.root, plan, showProgress);
  const pruned = opts.prune ? await pruneEmpty(state.root, plan) : 0;
  let text = `Done. ${plural(moves.length, "file")} sorted into ${plural(folders.size, "folder")}.`;
  if (pruned) text += ` ${plural(pruned, "empty folder")} removed.`;
  if (failures.length) text += ` ${plural(failures.length, "file")} could not be moved: ${failures.slice(0, 3).join("; ")}`;
  state.plan = [];
  setBusy(false);
  setStatus(text, failures.length ? "bad" : "good");
  await rescan(true);
}

async function undo() {
  if (state.busy || !state.root) return;
  setBusy(true);
  setStatus("Putting files back…");
  const { restored, problems } = await undoLast(state.root, showProgress);
  let text = `${plural(restored, "file")} put back where ${restored === 1 ? "it was" : "they were"}.`;
  if (problems.length) text += ` ${plural(problems.length, "problem")}: ${problems.slice(0, 3).join("; ")}`;
  setBusy(false);
  setStatus(text, problems.length ? "bad" : "good");
  await rescan(true);
}

function setupTool() {
  const input = $("dir-input");
  const canPickFolderReadOnly = "webkitdirectory" in input;
  $("pick").addEventListener("click", canPick ? pickFolder : () => input.click());
  input.addEventListener("change", () => openFiles(input.files));
  $("change").addEventListener("click", closeFolder);
  $("demo").addEventListener("click", openDemo);
  $("go").addEventListener("click", organize);
  $("undo").addEventListener("click", undo);
  $("rescan").addEventListener("click", () => rescan());
  for (const button of document.querySelectorAll("#modes button")) {
    button.addEventListener("click", () => {
      document.querySelector("#modes button.on").classList.remove("on");
      button.classList.add("on");
      rescan();
    });
  }
  for (const id of ["opt-recursive", "opt-hidden"]) {
    $(id).addEventListener("change", () => {
      if (!$("opt-recursive").checked) $("opt-prune").checked = false;
      rescan();
    });
  }

  if (!canPick) { // Firefox, Safari, phones: read the folder, give back a sorted .zip
    input.multiple = true;
    if (canPickFolderReadOnly) input.webkitdirectory = true;
    else $("pick").textContent = "Select files";
    $("drop-hint").hidden = true;
    $("zip-hint").hidden = false;
  }
  if (!canDemo) $("demo").hidden = true;

  // Drag a folder onto the page.
  window.addEventListener("dragover", (e) => { e.preventDefault(); document.body.classList.add("dragging"); });
  window.addEventListener("dragleave", (e) => { if (!e.relatedTarget) document.body.classList.remove("dragging"); });
  window.addEventListener("drop", async (e) => {
    e.preventDefault();
    document.body.classList.remove("dragging");
    const item = e.dataTransfer.items && e.dataTransfer.items[0];
    if (!item || state.busy) return;
    if (!item.getAsFileSystemHandle) return setNote("Dropping a folder does not work in this browser. Use the Select button.");
    const handle = await item.getAsFileSystemHandle();
    if (!handle || handle.kind !== "directory") return setNote("Drop a folder, not a file.");
    if ((await handle.requestPermission({ mode: "readwrite" })) === "granted") openFolder(handle);
  });
}

async function setupDownloads() {
  if (!$("dl-main")) return;
  const names = { windows: "Windows", macos: "Mac", linux: "Linux" };
  const ua = navigator.userAgent;
  const mine = /Windows/.test(ua) ? "windows" : /Mac/.test(ua) ? "macos" : /Linux/.test(ua) ? "linux" : "";
  const ready = {};
  await Promise.all(Object.entries(DOWNLOADS).map(async ([os, url]) => {
    const link = $(`dl-${os}`);
    link.href = url;
    ready[os] = true;
    if (/^https?:/.test(url)) return; // full URL (e.g. a release link): assume it exists
    try {
      const res = await fetch(url, { method: "HEAD" });
      if (!res.ok) throw new Error("missing");
      const bytes = Number(res.headers.get("content-length"));
      if (bytes) $(`info-${os}`).textContent += `, ${Math.round(bytes / 1048576)} MB`;
    } catch (_) {
      ready[os] = false;
      link.removeAttribute("href");
      link.classList.add("off");
      link.textContent = "Not available yet";
    }
  }));
  if (!mine) return;
  $(`dl-${mine}`).closest("li").classList.add("mine");
  $(`start-${mine}`).open = true;
  if (ready[mine]) {
    $("dl-main").href = DOWNLOADS[mine];
    $("dl-main").textContent = `Download for ${names[mine]}`;
    $("dl-main").hidden = false;
    $("dl-main-info").textContent = `${$(`info-${mine}`).textContent}. Free.`;
  } else {
    $("dl-main-info").textContent = `The ${names[mine]} version is not available yet.`;
  }
}

if ($("pick")) setupTool();
setupDownloads();
