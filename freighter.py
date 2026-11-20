#!/usr/bin/env python3
"""
Freighter - move a music production setup between computers (Windows <-> Mac, or same OS).

Plugins move as a list (reinstall them from each maker). Your own files - projects,
samples, presets - move as files, via a bundle folder on an external drive.

  OLD computer:
    python3 freighter.py scan                         _Manifest report + list of your projects
    python3 freighter.py pack --list                  show what can be moved, with sizes
    python3 freighter.py pack --to /Volumes/DRIVE     copy it to a bundle on the drive
        options: --include FOLDER (extra folders, e.g. sample libraries), --skip ID

  NEW computer:
    python3 freighter.py unpack /Volumes/DRIVE/Freighter-PC-2026-11-12
    python3 freighter.py fix-ableton                  repoint samples in Ableton projects
    python3 freighter.py check-fl                     find moved/missing samples in FL projects
    python3 manifest.py --compare BUNDLE/plugins.csv   plugins still to reinstall

Needs only Python 3.8+. Nothing leaves your computer. Use "py" instead of "python3" on Windows.
"""

import argparse
import datetime as dt
import gzip
import hashlib
import html
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
import unicodedata
from pathlib import Path, PurePosixPath, PureWindowsPath

sys.path.insert(0, str(Path(__file__).resolve().parent))
import manifest as rs  # noqa: E402
from freighter_version import __version__  # noqa: E402

THIS_OS = "mac" if rs.IS_MAC else "windows" if rs.IS_WIN else "other"
BUNDLE_VERSION = 1
JUNK_FILES = {".ds_store", "thumbs.db", "desktop.ini", "icon\r"}
SKIP_DIRS = {".trash", "$recycle.bin", "freighter backup", "rig mover backup"}
REHEARSAL_MARKER = ".freighter-rehearsal"   # in a rehearsal's folder; scans and packs skip that folder


def skip_dir(parent, name):
    """Folders a scan never enters: trash, Freighter's backups, and Freighter's own output: rehearsals (marked, or
    the drive/ + new-computer/ layout older versions left without a mark) and bundles (manifest.json + index/ +
    data/), e.g. a half-made bundle on the Desktop."""
    if name.lower() in SKIP_DIRS:
        return True
    d = os.path.join(parent, name)
    if os.path.exists(os.path.join(d, REHEARSAL_MARKER)):
        return True
    if os.path.isfile(os.path.join(d, "manifest.json")) and os.path.isdir(os.path.join(d, "index")) \
            and os.path.isdir(os.path.join(d, "data")):
        return True
    return os.path.isdir(os.path.join(d, "drive")) and os.path.isdir(os.path.join(d, "new-computer"))


def real_home():
    return Path.home()


def home():
    """Home folder. FREIGHTER_HOME overrides it (rehearsals and tests use a stand-in)."""
    return Path(os.environ.get("FREIGHTER_HOME") or real_home())


# --------------------------------------------------------------------------- known folders

SHELL_FOLDERS_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"


def _registry_folders():
    """Where Windows really keeps Desktop, Documents and Music. OneDrive moves Desktop and Documents by
    default (e.g. C:\\Users\\you\\OneDrive\\Documents), and people move them to other drives."""
    if not rs.IS_WIN:
        return {}
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, SHELL_FOLDERS_KEY)
    except OSError:
        return {}
    found = {}
    with key:
        for name, value in (("Desktop", "Desktop"), ("Documents", "Personal"), ("Music", "My Music")):
            try:
                raw, _ = winreg.QueryValueEx(key, value)
                found[name] = Path(winreg.ExpandEnvironmentStrings(raw))
            except OSError:
                pass
    return found


def user_folders(h=None):
    """Desktop, Documents and Music for home folder h, where they really are on this computer.
    Moved folders are only looked up for this computer's real home folder: never for a rehearsal's
    stand-in or any other folder (the registry only describes the real one)."""
    h = home() if h is None else Path(h)
    folders = {"Music": h / "Music", "Documents": h / "Documents", "Desktop": h / "Desktop"}
    if THIS_OS == "windows" and not os.environ.get("FREIGHTER_HOME") and _norm_abs(h) == _norm_abs(real_home()):
        for name, path in _registry_folders().items():
            if path.is_dir():
                folders[name] = path
    return folders


def all_user_folders(h=None):
    """Every Desktop, Documents and Music folder worth searching: where Windows says they are (e.g. in OneDrive)
    and the usual ones in the home folder, which can still hold files from before the move to OneDrive."""
    h = home() if h is None else Path(h)
    out = list(user_folders(h).values()) + [h / "Music", h / "Documents", h / "Desktop"]
    return [f for f in dict.fromkeys(out) if f.is_dir()]


def known_roots(os_name, h):
    """Standard places producers keep their own files, by logical id, for one OS."""
    if os_name == THIS_OS:
        uf = user_folders(h)
        docs, music = uf["Documents"], uf["Music"]
    else:
        docs, music = h / "Documents", h / "Music"
    roots = {
        "fl_user": ("FL Studio user data (projects, presets, recordings)", docs / "Image-Line"),
        "ableton_user_library": ("Ableton User Library",
                                 (docs if os_name == "windows" else music) / "Ableton" / "User Library"),
        "splice_home": ("Splice samples", h / "Splice"),
        "splice_docs": ("Splice samples", docs / "Splice"),
        "ni_user": ("Native Instruments user content", docs / "Native Instruments"),
    }
    if os_name == "mac":
        roots["logic"] = ("Logic projects", music / "Logic")
        roots["audio_music_apps"] = ("Logic / GarageBand content", music / "Audio Music Apps")
    if os_name == THIS_OS:
        # a DAW folder left in the local Documents (from before OneDrive took Documents over) is still the one
        for rid, (label, path) in list(roots.items()):
            for moved, local in ((docs, h / "Documents"), (music, h / "Music")):
                if moved != local and not path.exists():
                    try:
                        rel = path.relative_to(moved)
                    except ValueError:
                        continue
                    if (local / rel).exists():
                        roots[rid] = (label, local / rel)
    return roots


def moved_root(source_host):
    return home() / "Music" / "Freighter" / source_host


# --------------------------------------------------------------------------- file helpers

WIN_BAD = re.compile(r'[<>:"|?*\x00-\x1f]')


def safe_rel(rel):
    """A relative path that is legal on Windows and exFAT drives too."""
    parts = []
    for part in rel.split("/"):
        p = WIN_BAD.sub("_", part).rstrip(" .") or "_"
        if p.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                                       *(f"LPT{i}" for i in range(1, 10))}:
            p = "_" + p
        parts.append(p)
    return "/".join(parts)


def long_path(p):
    """On Windows, the \\\\?\\ form of a path, which works past the old 260-character limit. Network
    paths (\\\\server\\share) need \\\\?\\UNC\\server\\share. Other systems: the path unchanged.
    abspath, not resolve(): resolve turns a mapped drive (Z:) into its network path."""
    s = str(p)
    if not rs.IS_WIN or s.startswith("\\\\?\\"):
        return s
    import ntpath
    s = ntpath.abspath(s)
    if s.startswith("\\\\"):
        return "\\\\?\\UNC\\" + s[2:]
    return "\\\\?\\" + s


def short_path(s):
    """The same path without the \\\\?\\ prefix, for showing to people and storing in reports."""
    s = str(s)
    if s.startswith("\\\\?\\UNC\\"):
        return "\\\\" + s[8:]
    return s[4:] if s.startswith("\\\\?\\") else s


def file_size(p):
    """Size of the file at p, or None if there isn't one (works for long Windows paths)."""
    try:
        st = os.stat(long_path(p))
    except OSError:
        return None
    return st.st_size if not os.path.isdir(long_path(p)) else None


def copy_and_hash(src, dst):
    """Copy src to dst through a temporary name; return the SHA-256 of what was copied."""
    dst = Path(dst)
    os.makedirs(long_path(dst.parent), exist_ok=True)
    tmp = dst.with_name(dst.name + ".freighter-part")
    h = hashlib.sha256()
    with open(long_path(src), "rb") as f, open(long_path(tmp), "wb") as out:
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
            out.write(chunk)
    os.replace(long_path(tmp), long_path(dst))
    try:
        shutil.copystat(long_path(src), long_path(dst))
    except OSError:
        pass
    return h.hexdigest()


def hash_file(p):
    h = hashlib.sha256()
    with open(long_path(p), "rb") as f:
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


# Programs, installers and plugin binaries. They don't belong in a move: plugins get reinstalled from
# their makers, and installers tucked into sample folders are a common way cracked software spreads malware.
# Only checked in sample, Desktop and Downloads folders, never inside a DAW's own user-data folder
# (FL Studio keeps settings as .scr files; Ableton's User Library holds Max for Live .js devices).
PROGRAM_EXTS = {
    ".exe", ".msi", ".msix", ".bat", ".cmd", ".ps1", ".vbs", ".jar", ".dll", ".sys",
    ".dmg", ".pkg", ".mpkg", ".app", ".command", ".dylib",
    ".vst", ".vst3", ".component", ".clap", ".aaxplugin",
}
# Extensions other software also uses for ordinary files: a program only if the header says so.
MAYBE_PROGRAM_EXTS = {".scr", ".com", ".bundle", ".so"}
EXEC_MAGIC = (b"MZ", b"\x7fELF", b"\xca\xfe\xba\xbe", b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe",
              b"\xfe\xed\xfa\xcf", b"\xfe\xed\xfa\xce")


def is_program(path):
    ext = os.path.splitext(str(path))[1].lower()
    if ext in PROGRAM_EXTS:
        return True
    if ext in MAYBE_PROGRAM_EXTS:
        p = Path(path)
        if p.is_dir():
            return (p / "Contents" / "MacOS").is_dir()
        try:
            with open(p, "rb") as f:
                head = f.read(4)
        except OSError:
            return False
        return any(head.startswith(m) for m in EXEC_MAGIC)
    return False


# OneDrive "Files On-Demand" placeholders: the file lives in the cloud and downloads when read
CLOUD_ONLY_ATTRS = 0x400000 | 0x40000 | 0x1000   # RECALL_ON_DATA_ACCESS, RECALL_ON_OPEN, OFFLINE
CLOUD_ONLY = []   # (path, size) of cloud-only files seen while measuring; cmd_pack reports them


def is_cloud_only(st):
    return bool(getattr(st, "st_file_attributes", 0) & CLOUD_ONLY_ATTRS)


def walk_files(root, left_behind=None):
    """Yield (relative posix path, absolute path, size) for every real file under root.
    With a left_behind list, programs and installers are skipped and recorded there instead."""
    root = Path(root)
    lroot = long_path(root)   # walk the long form so deep folders on Windows aren't skipped
    for cur, dirs, files in os.walk(lroot, onerror=lambda e: None):
        keep = []
        for d in dirs:
            if skip_dir(cur, d) or os.path.islink(os.path.join(cur, d)):
                continue
            if left_behind is not None and is_program(os.path.join(cur, d)):  # app and plugin bundles are folders
                left_behind.append(short_path(os.path.join(cur, d)))
                continue
            keep.append(d)
        dirs[:] = keep
        for fn in files:
            if fn.lower() in JUNK_FILES or fn.startswith("._") or fn.endswith(".freighter-part"):
                continue
            full = os.path.join(cur, fn)
            if left_behind is not None and is_program(full):
                left_behind.append(short_path(full))
                continue
            if os.path.islink(full):
                continue
            try:
                st = os.stat(full)
            except OSError:
                continue
            rel = Path(os.path.relpath(full, lroot)).as_posix()
            ap = root.joinpath(*rel.split("/"))
            if is_cloud_only(st):
                CLOUD_ONLY.append((ap, st.st_size))
            yield rel, ap, st.st_size


# The app watches long jobs through this: set PROGRESS.listener(label, done, total) on the job's thread.
# A listener may raise Cancelled to stop the job; an interrupted pack resumes where it stopped.
PROGRESS = threading.local()


class Cancelled(BaseException):
    """Raised to stop a job. A BaseException, so code that catches Exception to carry on can't swallow it."""


def report_progress(label, done, total):
    listener = getattr(PROGRESS, "listener", None)
    if listener:
        listener(label, done, total)


class Progress:
    def __init__(self, total_bytes, label):
        self.total, self.done, self.label, self.last, self.start = total_bytes, 0, label, 0.0, time.time()

    def add(self, n):
        self.done += n
        report_progress(self.label, self.done, self.total)
        now = time.time()
        if now - self.last > 2 or self.done >= self.total:
            self.last = now
            pct = 100 * self.done / self.total if self.total else 100
            rate = self.done / max(now - self.start, 0.001)
            print(f"  {self.label}: {pct:5.1f}%  {rs.human_size(self.done)} of {rs.human_size(self.total)}"
                  f"  ({rs.human_size(rate)}/s)", flush=True)


COMPLETE = "complete.json"   # written last by pack; unpack checks for it (issue #59)
PACK_REPORTS = (("pack-errors.txt", "file couldn't be copied", "files couldn't be copied"),
                ("already-missing.txt", "sample was already missing on the old computer",
                 "samples were already missing on the old computer"),
                ("left-behind.txt", "program or installer was left behind on purpose",
                 "programs or installers were left behind on purpose"))


def find_bundle(drive, host):
    """Where this pack goes: this computer's unfinished bundle if there is one (so a pack interrupted yesterday
    resumes instead of starting over), otherwise today's bundle."""
    pattern = re.compile(rf"Freighter-{re.escape(host)}-\d{{4}}-\d\d-\d\d$")
    mine = sorted((p for p in drive.glob("Freighter-*")
                   if pattern.match(p.name) and (p / "manifest.json").is_file()),
                  key=lambda p: p.stat().st_mtime)
    unfinished = [p for p in mine if not (p / COMPLETE).is_file()]
    return unfinished[-1] if unfinished else drive / f"Freighter-{host}-{dt.date.today().isoformat()}"


def report_count(path):
    """How many entries a pack report lists (lines after its blank-line-separated header)."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return 0
    body = text.split("\n\n", 2)[-1] if text.count("\n\n") >= 2 else text.split("\n\n", 1)[-1]
    return sum(1 for line in body.splitlines() if line.strip())


def bundle_problems(bundle, manifest):
    """Why this bundle isn't a finished, whole pack (empty list when it is)."""
    problems = []
    try:
        done = json.loads((bundle / COMPLETE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        problems.append("Packing this bundle wasn't finished: it was interrupted, or is still running.")
        done = {}
    for it in manifest["items"]:
        have = len(read_jsonl(bundle / "index" / f"{it['id']}.jsonl"))
        want = it.get("files")
        if want is not None and have < want:
            problems.append(f"{it['label']}: only {have} of {want} files were packed.")
    if done.get("errors"):
        problems.append(f"{done['errors']} files couldn't be copied (listed in pack-errors.txt on the drive).")
    return problems


def resume_records(bundle, it):
    """The records already packed for this item, if its index really belongs to this item's folder. An index
    written for a different folder (or by an older Freighter without a header) is set aside, never reused."""
    index_path = bundle / "index" / f"{it['id']}.jsonl"
    if not index_path.exists():
        return {}
    header = {}
    with open(index_path, encoding="utf-8") as f:
        try:
            header = json.loads(f.readline() or "{}")
        except ValueError:
            pass
    if norm(header.get("source_path", ""), THIS_OS == "windows") == norm(it["source_path"], THIS_OS == "windows"):
        return read_jsonl(index_path)
    n = 1
    while (bundle / "index" / f"{it['id']}.set-aside-{n}.jsonl").exists():
        n += 1
    os.replace(index_path, bundle / "index" / f"{it['id']}.set-aside-{n}.jsonl")
    data = bundle / "data" / it["id"]
    if data.exists():
        os.replace(data, bundle / "data" / f"{it['id']}.set-aside-{n}")
    return {}


def read_jsonl(p):
    out = {}
    if Path(p).is_file():
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rec = json.loads(line)
                        out[rec["rel"]] = rec
                    except (ValueError, KeyError):
                        pass  # half-written last line after an interruption
    return out


# --------------------------------------------------------------------------- pack

def item_id(prefix, path):
    """An item's ID, made from its folder so the same folder gets the same ID on every run. (IDs used to be
    positions in a list, so a new folder could shift them and a resumed pack read another folder's records: #57.)"""
    key = norm(str(path), THIS_OS == "windows")
    return f"{prefix}-{hashlib.sha1(key.encode('utf-8')).hexdigest()[:8]}"


def pack_items(includes, skips, missing=None, installed=None, needed=None):
    """What a move packs. If `missing` is a dict, it is filled with {project: [samples it uses that are
    already missing on this computer]}; if `installed` is a set, with samples that come with installed programs."""
    items = []
    for rid, (label, path) in known_roots(THIS_OS, home()).items():
        if path.is_dir() and rid not in skips:
            items.append({"id": rid, "label": label, "source_path": str(path), "kind": "known"})
    for inc in includes:
        p = Path(inc).expanduser().resolve()
        if not p.is_dir():
            sys.exit(f"Not a folder: {inc}")
        iid = item_id("extra", p)
        if iid not in skips:
            items.append({"id": iid, "label": p.name, "source_path": str(p), "kind": "extra"})
    left_out = [str(path) for rid, (_l, path) in known_roots(THIS_OS, home()).items() if rid in skips and path.is_dir()]
    folders, loose, gone = project_sample_folders(items, installed, left_out, needed)
    if missing is not None:
        missing.update(gone)
    for folder, label in folders.items():
        iid = item_id("used", folder)
        if iid not in skips:
            items.append({"id": iid, "label": f"{label}: {folder.name}",
                          "source_path": str(folder), "kind": "extra"})
    for folder, files in sorted(loose.items()):
        iid = item_id("loose", folder)
        if iid not in skips:
            items.append({"id": iid, "label": f"Loose files your projects use from {landing_name(folder)}",
                          "source_path": folder, "kind": "extra", "only": sorted(files)})
    return items


def sample_folder_for(sample, h, mac=None):
    """The folder to move for one referenced sample: two levels under home (e.g. ~/Desktop/DrumKits), or up to
    three levels from the root elsewhere (e.g. /Volumes/SSD/Samples, D:\\Samples\\Drums).
    None when the sample sits at the top of a drive (/Volumes/SSD/kick.wav, D:\\kick.wav): the caller then takes
    just that file, never the whole drive."""
    mac = rs.IS_MAC if mac is None else mac
    P = PurePosixPath if mac else PureWindowsPath
    p, hp = P(sample), P(str(h))
    try:
        rel = p.relative_to(hp)
        parts = rel.parts[:2] if len(rel.parts) > 2 else rel.parts[:1]
        return Path(str(hp.joinpath(*parts))) if parts else None
    except ValueError:
        folder = p.parent.parts                    # never the file itself
        if len(folder) < (4 if mac else 2):        # the top of a drive: / Volumes SSD, or D:\\
            return None
        return Path(str(P(*folder[:4 if mac else 3])))


def landing_name(source_path):
    """Folder name for a moved item on the new computer. A drive root (D:\\) becomes "D drive"."""
    name = source_path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    if re.fullmatch(r"[A-Za-z]:", name):
        return f"{name[0].upper()} drive"
    return safe_rel(name) if name else "Loose samples"


PROGRAM_BUNDLES = (".app", ".component", ".vst", ".vst3", ".bundle", ".framework", ".aaxplugin", ".plugin", ".clap")
WIN_PROGRAM_DIRS = {"program files", "program files (x86)", "programdata", "windows"}
MAC_PROGRAM_DIRS = ("/applications/", "/system/", "/library/application support/", "/library/audio/plug-ins/",
                    "/usr/", "/opt/")


def in_program_place(path):
    """True for files that come with an installed program or plugin: inside an app or plugin bundle, Program Files,
    ProgramData, or the Mac's system folders. They come back when the program is installed, so Freighter never
    packs them as samples (issue #60: a Core Library reference used to pack the Live app itself)."""
    s = str(path).replace("\\", "/")
    parts = [x.lower() for x in s.split("/") if x]
    if any(x.endswith(PROGRAM_BUNDLES) for x in parts[:-1]):
        return True
    if len(parts) > 1 and re.fullmatch(r"[a-z]:", parts[0]) and parts[1] in WIN_PROGRAM_DIRS:
        return True
    return (s.lower() + ("/" if not s.endswith("/") else "")).startswith(MAC_PROGRAM_DIRS)


def project_sample_folders(items, installed=None, excluded=(), needed=None):
    """What a move needs beyond the packed items: projects found in Desktop, Documents and Music (even ones
    that use no outside samples), and every sample folder FL/Ableton projects use.
    Returns ({folder: label}, {folder: set of file names to take loose},
             {project path: [samples it uses that are already missing]})."""
    h = home()
    # excluded: DAW folders the user chose to leave out (--skip). They count as handled, so nothing re-adds them.
    covered = [Path(it["source_path"]) for it in items] + [Path(e) for e in excluded]
    roots = all_user_folders(h) + [c for c in covered if c.is_dir()]
    left_out = [Path(e) for e in excluded]
    inside_left_out = lambda p: any(e == p or e in p.parents for e in left_out)
    flps = [p for p in find_projects(roots, ".flp", include_backups=True) if not inside_left_out(p)]
    alss = [p for p in find_projects(roots, ".als") if not inside_left_out(p)]
    refs = {}   # sample -> projects that use it
    for p in flps:
        try:
            for s in flp_samples(p)[1]:
                if not FL_PLACEHOLDER.match(s):
                    refs.setdefault(s, set()).add(p)
        except (ValueError, OSError, struct.error):
            pass
    for p in alss:
        try:
            text, _ = read_als(p)
            for ref in als_sample_refs(text):
                refs.setdefault(ref, set()).add(p)
        except Exception:
            pass
    bases = all_user_folders(h)
    folders, loose = {}, {}

    def inside_covered(path):
        return any(c == path or c in path.parents for c in covered)

    def take(path, label):
        """Add the folder that should carry this file, or the file alone when it sits loose."""
        f = folder_to_move(path, h, bases, covered)
        if f is None:
            loose.setdefault(str(path.parent), set()).add(path.name)
        elif f.is_dir() and f != h:
            if folders.get(f, label) != label:
                label = "Samples and projects"   # skipping this folder would lose samples too
            folders[f] = label

    def add_folder(f, label):
        """Add folder f itself (not its top-level parent), unless it's a base folder or would re-pack a packed item."""
        if f is None or f == h or f in bases or f.parent == f or any(f in c.parents for c in covered):
            return False
        if f.is_dir():
            if folders.get(f, label) != label:
                label = "Samples and projects"   # skipping this folder would lose samples too
            folders[f] = label
        return True

    usable = {}   # project -> the existing samples it uses that need moving (outside packed folders, not programs)
    for s, users in refs.items():
        if in_program_place(s) or inside_left_out(Path(s)) or not os.path.isfile(long_path(s)):
            continue
        if needed is not None:
            needed.add(s)        # every existing sample the move must carry, checked again after measuring (#64)
        if not inside_covered(Path(s)):
            for u in users:
                usable.setdefault(u, []).append(Path(s))

    # Projects outside the packed folders move with their own folder: an Ableton "... Project" folder, or the folder
    # an FL project sits in (a project loose on the Desktop comes alone). FL autosave backups aren't moved on
    # their own. A project that only uses samples from inside the folder tree it sits in, like a sample pack's demo
    # kit, moves with just that part of the tree: it no longer drags a whole sample library along (issue #61).
    handled = set()
    for p in flps + alss:
        if inside_covered(p) or p.parent.name.lower() == "backup":
            continue
        own = p.parent
        if own == h or own in bases or own.parent == own:
            loose.setdefault(str(own), set()).add(p.name)
            continue
        used = usable.get(p, [])
        top = folder_to_move(p, h, bases, covered)
        if used and top is not None and all(folder_to_move(r, h, bases, covered) == top for r in used):
            anchor = Path(os.path.commonpath([str(own)] + [str(r.parent) for r in used]))
            if add_folder(anchor, "A project and the samples it uses"):
                handled.add(p)
                continue
        if not add_folder(own, "Projects"):
            loose.setdefault(str(own), set()).add(p.name)

    missing = {}
    for s, users in refs.items():
        if inside_left_out(Path(s)):
            continue
        if in_program_place(s):
            if installed is not None:
                installed.add(s)
            continue
        if not os.path.isfile(long_path(s)):
            for p in users:
                missing.setdefault(str(p), []).append(s)
        elif not inside_covered(Path(s)) and not all(u in handled for u in users):
            take(Path(s), "Samples your projects use")
    keep = {f: label for f, label in folders.items() if not any(o != f and o in f.parents for o in folders)}
    missing = {p: sorted(v) for p, v in sorted(missing.items(), key=lambda kv: kv[0].lower())}
    return dict(sorted(keep.items(), key=lambda kv: str(kv[0]).lower())), loose, missing


def missing_lines(missing):
    """One line per sample that was already missing, grouped by project."""
    return [f"{Path(p).name}: {s}" for p, samples in missing.items() for s in samples]


def say_missing(missing, where=None):
    lines = missing_lines(missing)
    if not lines:
        return
    n, k = len(lines), len(missing)
    print(f"\n{n} sample{'s' if n != 1 else ''} used by {k} project{'s' if k != 1 else ''} "
          f"{'are' if n != 1 else 'is'} already missing on this computer, so {'they' if n != 1 else 'it'} "
          "can't be moved.\nIf they're on a drive that isn't plugged in, plug it in and run this again.")
    for line in lines[:8]:
        print(f"  {line}")
    if len(lines) > 8:
        print(f"  ... and {len(lines) - 8} more" + (f" (all listed in {where})" if where else ""))


def folder_to_move(path, h, bases, covered):
    """The folder to move so that `path` (a sample or project) comes along, or None to take the file alone.
    Starts at the top folder inside Desktop, Documents or Music (wherever they really are), or uses
    sample_folder_for elsewhere, then steps down while that folder would also re-pack a packed item
    such as the Ableton User Library (issue #9)."""
    base = next((u for u in bases if u in path.parents), None)
    if base is not None:
        if path.parent == base:
            return None
        f = base / path.relative_to(base).parts[0]
    elif path.parent == h or path.parent.parent == h:
        return None      # a file sitting in the home folder itself, or straight in ~/Downloads etc.
    else:
        f = sample_folder_for(str(path), h)
        if f is None:
            return None  # at the top of a drive
    while any(f in c.parents for c in covered):
        rest = path.relative_to(f).parts
        if len(rest) == 1:
            return None
        f = f / rest[0]
    return f


def measure_item(it, left_behind):
    if "only" in it:
        files = []
        for name in it["only"]:
            ap = Path(it["source_path"]) / name
            size = file_size(ap)
            if size is not None:
                files.append((name, ap, size))
    else:
        # DAW user-data folders move whole; sample folders leave programs and installers behind
        files = list(walk_files(it["source_path"], left_behind if it["kind"] != "known" else None))
    it["_files"] = files
    it["files"] = len(files)
    it["bytes"] = sum(f[2] for f in files)


def measure_items(items, include_programs=False):
    """List every file each item would pack (it["_files"], it["files"], it["bytes"]). Fills CLOUD_ONLY.
    Returns (total bytes, programs left behind, or None when they're kept)."""
    del CLOUD_ONLY[:]
    left_behind = None if include_programs else []
    for i, it in enumerate(items):
        report_progress(f"Measuring {it['label']}", i, len(items))
        measure_item(it, left_behind)
    return sum(it["bytes"] for it in items), left_behind


def cover_stragglers(items, needed):
    """The safety net (issue #64): every existing sample a project uses must be in some item's file list. Any that
    isn't (a symlink inside a packed folder, an odd location, or a future gap in the folder rules) is added as a
    loose file. Returns how many were added."""
    fold = THIS_OS == "windows"
    packed = {norm(str(ap), fold) for it in items for _rel, ap, _size in it["_files"]}
    stray = sorted(s for s in needed if norm(s, fold) not in packed)
    by_folder = {}
    for s in stray:
        p = Path(s)
        by_folder.setdefault(str(p.parent), []).append(p.name)
    for folder, names in by_folder.items():
        iid = item_id("loose", folder)
        it = next((i for i in items if i["id"] == iid), None)
        if it is None:
            it = {"id": iid, "label": f"Loose files your projects use from {landing_name(folder)}",
                  "source_path": folder, "kind": "extra", "only": []}
            items.append(it)
        it["only"] = sorted(set(it["only"]) | set(names))
        measure_item(it, None)
    return len(stray)


def plan_move(includes=(), skips=(), include_programs=False):
    """Everything a pack (or the app's move list) needs to know, decided once. Returns a dict with items (measured),
    total, left_behind, missing, installed, cloud_only and stragglers (files the safety net added)."""
    missing, installed, needed = {}, set(), set()
    items = pack_items(list(includes), set(skips), missing, installed, needed)
    total, left_behind = measure_items(items, include_programs) if items else (0, None)
    added = cover_stragglers(items, needed) if items else 0
    return {"items": items, "total": sum(it["bytes"] for it in items), "left_behind": left_behind,
            "missing": missing, "installed": installed, "cloud_only": list(CLOUD_ONLY), "stragglers": added}


def say_installed(installed):
    if installed:
        n = len(installed)
        print(f"\n{n} sample{'s' if n != 1 else ''} your projects use come{'s' if n == 1 else ''} with installed programs or "
              "plugins (for example Ableton's Core Library or FL Studio's own sounds). They aren't moved: installing "
              "those programs on the new computer brings them back.")


def cmd_pack(args):
    print("Looking through your music folders and projects...", flush=True)
    plan = plan_move(args.include, args.skip, getattr(args, "include_programs", False))
    items, missing, installed = plan["items"], plan["missing"], plan["installed"]
    if not items:
        msg = "Nothing to pack: no standard music folders found. Add folders with --include FOLDER."
        if args.list or not args.to:
            print(msg)
            return 0
        sys.exit(msg)

    total, left_behind = plan["total"], plan["left_behind"]
    if plan["stragglers"]:
        print(f"Added {plan['stragglers']} sample{'s' if plan['stragglers'] != 1 else ''} your projects use that the "
              "folders above didn't cover (as loose files).")
    if CLOUD_ONLY:
        size = sum(n for _, n in CLOUD_ONLY)
        print(f"\nNote: {len(CLOUD_ONLY)} files ({rs.human_size(size)}) are only stored in OneDrive's cloud, not on this "
              f"computer.\nPacking downloads them first, so stay online. To speed it up, right-click the folder in "
              f"File Explorer and choose \"Always keep on this device\" before packing.\nFirst one: {CLOUD_ONLY[0][0]}")

    if args.list or not args.to:
        print(f"\n{'ID':<22} {'SIZE':>10} {'FILES':>8}  WHAT")
        for it in items:
            print(f"{it['id']:<22} {rs.human_size(it['bytes']):>10} {it['files']:>8}  {it['label']}  ({it['source_path']})")
        print(f"\nTotal {rs.human_size(total)}. Pack with: --to DRIVE   (leave one out with --skip ID)")
        if left_behind:
            print(f"{len(left_behind)} programs or installers will be left behind (reinstall software from its "
                  "maker). Keep them with --include-programs.")
        say_missing(missing)
        say_installed(installed)
        if not args.to:
            return 0
        if args.list:
            return 0

    host = rs.host_name()
    drive = Path(args.to).expanduser()
    bundle = find_bundle(drive, host) if drive.is_dir() else drive / f"Freighter-{host}-{dt.date.today().isoformat()}"
    if filesystem(drive) in FAT_NAMES:
        big = [(ap, size) for it in items for _rel, ap, size in it["_files"] if size > FAT_MAX]
        if big:
            sys.exit(f"This drive is formatted as FAT32, which can't hold files over 4 GB, and {len(big)} of yours "
                     "are bigger:\n" + "\n".join(f"  {ap} ({rs.human_size(n)})" for ap, n in big[:5])
                     + "\nReformat the drive as exFAT (this erases it), or leave those items out with --skip ID.")
    try:
        bundle.mkdir(parents=True, exist_ok=True)
        (bundle / "index").mkdir(exist_ok=True)
    except OSError as e:
        sys.exit(f"Can't write to {drive}: {e.strerror or e}.\nIf it's a Windows (NTFS) drive plugged into a Mac, "
                 "macOS can only read it. Use a drive formatted as exFAT, which both Mac and Windows can write "
                 "(formatting erases the drive).")

    already = 0
    for it in items:
        already += sum(r["size"] for r in resume_records(bundle, it).values())
    free = shutil.disk_usage(bundle).free
    if total - already > free:
        sys.exit(f"Not enough space on the drive: need {rs.human_size(total - already)}, "
                 f"have {rs.human_size(free)}. Leave something out with --skip ID.")

    print("\nSaving the plugin list...", flush=True)
    rs.run_scan(bundle / "manifest-report", quick=True, safety=False)  # the list is what a move needs
    shutil.copy2(bundle / "manifest-report" / "plugins.csv", bundle / "plugins.csv")

    manifest = {
        "bundle_version": BUNDLE_VERSION, "freighter_version": __version__, "created": dt.datetime.now().isoformat(timespec="seconds"),
        "source_host": host, "source_os": THIS_OS, "source_home": str(home()),
        "items": [{k: v for k, v in it.items() if not k.startswith("_")} for it in items],
    }
    (bundle / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    (bundle / COMPLETE).unlink(missing_ok=True)   # changing it: not finished until the end
    prog = Progress(total, "Copying")
    prog.add(0)
    errors = []
    for it in items:
        index_path = bundle / "index" / f"{it['id']}.jsonl"
        done = resume_records(bundle, it)
        # (Re)write the index: a header naming this item's folder, then its complete records, so a line cut off
        # by an interruption can't swallow the next record appended after it.
        header = {"item": it["id"], "source_path": it["source_path"]}
        index_path.write_text("".join(json.dumps(r) + "\n" for r in [header, *done.values()]), encoding="utf-8")
        data_root = bundle / "data" / it["id"]
        taken = {r["stored"].lower() for r in done.values()}
        with open(index_path, "a", encoding="utf-8") as index:
            for rel, ap, size in it["_files"]:
                stored = done[rel]["stored"] if rel in done else unique_stored(rel, taken)
                dst = data_root / stored
                rec = done.get(rel)
                try:
                    mtime = int(os.stat(long_path(ap)).st_mtime)
                except OSError:
                    mtime = None
                # skip what's already there, unless the original changed since (same size isn't enough: a
                # re-rendered sample often keeps its length)
                if rec and rec["size"] == size and file_size(dst) == size and rec.get("mtime") in (None, mtime):
                    prog.add(size)
                    continue
                try:
                    digest = copy_and_hash(ap, dst)
                except OSError as e:
                    errors.append(f"{ap}: {e}")
                    continue
                index.write(json.dumps({"rel": rel, "stored": stored, "size": size, "sha256": digest,
                                        "mtime": mtime}) + "\n")
                index.flush()
                prog.add(size)

    for name, *_ in PACK_REPORTS:   # reports from an earlier run of this bundle no longer apply
        (bundle / name).unlink(missing_ok=True)
    if left_behind:
        (bundle / "left-behind.txt").write_text(
            f"Freighter {__version__}\n\n"
            "Programs, installers and plugin files left out of this move. Reinstall software from its maker;\n"
            "installers found in sample folders are a common way malware spreads.\n\n" + "\n".join(left_behind),
            encoding="utf-8")
    if missing:
        (bundle / "already-missing.txt").write_text(
            f"Freighter {__version__}\n\nSamples your projects use that were already missing on {host} when this "
            "bundle was packed.\nThey couldn't be moved. If they were on a drive that wasn't plugged in, plug it in "
            "and pack again.\n\n" + "\n".join(missing_lines(missing)), encoding="utf-8")
    print(f"\nBundle ready: {bundle}")
    say_missing(missing, "already-missing.txt")
    if left_behind:
        print(f"{len(left_behind)} programs or installers were left behind; see left-behind.txt.")
    if errors:
        (bundle / "pack-errors.txt").write_text("\n".join([f"Freighter {__version__}", "", "Files that couldn't be "
                                                            "copied:", ""] + errors), encoding="utf-8")
        print(f"{len(errors)} files couldn't be copied; see pack-errors.txt. Run the same command again to retry.")
    (bundle / COMPLETE).write_text(json.dumps({
        "finished": dt.datetime.now().isoformat(timespec="seconds"), "freighter_version": __version__,
        "errors": len(errors),
        "items": {it["id"]: {"files": it["files"], "packed": len(read_jsonl(bundle / "index" / f"{it['id']}.jsonl"))}
                  for it in items},
    }, indent=1), encoding="utf-8")
    print("Next, on the new computer:  " + tool_cmd("freighter", f"unpack \"{bundle}\""))
    return 0


# --------------------------------------------------------------------------- unpack

def existing_parent(path):
    path = Path(path)
    while not path.exists() and path.parent != path:
        path = path.parent
    return path


def space_shortfalls(plan, margin=200 * 1024 * 1024):
    """For an unpack plan [(item, dest, index)]: the disks without room for the files still to copy.
    Returns [(disk, bytes needed, bytes free)]. Keeps a small margin so the disk isn't filled to the brim."""
    need, free, where = {}, {}, {}
    for it, dest, idx in plan:
        anchor = existing_parent(dest)
        disk = os.stat(anchor).st_dev
        where.setdefault(disk, anchor)
        free.setdefault(disk, shutil.disk_usage(anchor).free)
        for rec in idx.values():
            target = dest / (rec["rel"] if THIS_OS != "windows" else rec["stored"])
            if file_size(target) == rec["size"]:
                continue   # most likely already unpacked
            need[disk] = need.get(disk, 0) + rec["size"]
    return [(where[d], n, free[d]) for d, n in need.items() if n + margin > free[d]]


FAT_NAMES = {"fat", "fat12", "fat16", "fat32", "msdos", "vfat"}
FAT_MAX = 4 * 1024 ** 3 - 1   # the biggest file a FAT32 drive can hold


def mount_fs(mount_output, path):
    """File system of `path` from the output of `mount` (macOS/Linux), or ''."""
    path, best = str(path), ("", "")
    for line in mount_output.splitlines():
        m = re.match(r"^.+? on (.+?) (?:\((\w+)|type (\w+))", line)
        if not m:
            continue
        point, fs = m.group(1), (m.group(2) or m.group(3)).lower()
        if (path == point or path.startswith(point.rstrip("/") + "/")) and len(point) >= len(best[0]):
            best = (point, fs)
    return best[1]


def filesystem(path):
    """The drive's file system ('ntfs', 'exfat', 'fat32', 'msdos', 'apfs'...), lowercased, or '' if unknown."""
    path = existing_parent(Path(path).expanduser().resolve())
    if rs.IS_WIN:
        try:
            import ctypes
            vol, fs = ctypes.create_unicode_buffer(300), ctypes.create_unicode_buffer(64)
            k32 = ctypes.windll.kernel32
            if k32.GetVolumePathNameW(str(path), vol, 300) and \
                    k32.GetVolumeInformationW(vol.value, None, 0, None, None, None, fs, 64):
                return fs.value.lower()
        except Exception:
            pass
        return ""
    try:
        return mount_fs(subprocess.run(["mount"], capture_output=True, text=True, timeout=10).stdout, path)
    except Exception:
        return ""


def unique_stored(rel, taken):
    """The name a file is stored under on the drive. safe_rel can turn two names into one (kick?.wav and
    kick_.wav), and exFAT ignores case (Kick.wav, kick.wav): add " (2)", " (3)"... so nothing overwrites.
    `taken` holds lowercased names already used and is updated."""
    stored = safe_rel(rel)
    head, _, last = stored.rpartition("/")
    stem, dot, ext = last.rpartition(".") if "." in last else (last, "", "")
    n = 2
    while stored.lower() in taken:
        stored = (head + "/" if head else "") + f"{stem} ({n}){dot}{ext}"
        n += 1
    taken.add(stored.lower())
    return stored


def previous_entries(landing):
    """Path-map entries from earlier unpacks from the same computer (for top-up bundles)."""
    try:
        return json.loads((landing / "pathmap.json").read_text(encoding="utf-8")).get("entries", [])
    except (OSError, ValueError):
        return []


def landing_folders(items, landing, previous, fold_case):
    """A landing folder for each moved item, never shared with another item (issue #58: C:\\...\\Desktop\\Samples
    and D:\\Samples used to land in the same folder, so repair could point at the wrong file). A folder an earlier
    unpack already gave to the same source is reused, so top-up bundles land in the same places."""
    before = {norm(e["old"], fold_case): Path(e["new"]) for e in previous if Path(e["new"]).parent == landing}
    taken = {p.name.lower() for p in before.values()}
    out = {}
    for it in sorted(items, key=lambda i: norm(i["source_path"], True)):
        key = norm(it["source_path"], fold_case)
        if key in before:
            out[it["id"]] = before[key]
            continue
        base = landing_name(it["source_path"])
        name = base
        if name.lower() in taken:
            src = it["source_path"].replace("\\", "/").rstrip("/")
            drive = re.match(r"^([A-Za-z]):", src)
            parent = Path(src).parent.name
            hint = f"{drive.group(1).upper()} drive" if drive and not src[2:].strip("/").count("/") else \
                (safe_rel(parent) if parent and not re.fullmatch(r"[A-Za-z]:", parent) else "")
            name = f"{base} ({hint})" if hint else base
        n = 2
        while name.lower() in taken:
            name = f"{base} ({n})"
            n += 1
        taken.add(name.lower())
        out[it["id"]] = landing / name
    return out


def cmd_unpack(args):
    bundle = Path(args.bundle).expanduser()
    try:
        manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        sys.exit(f"Not a Freighter bundle (no readable manifest.json): {bundle}")
    if manifest.get("bundle_version", 1) > BUNDLE_VERSION:
        sys.exit(f"This bundle was made by a newer Freighter ({manifest.get('freighter_version', 'unknown version')}) "
                 f"than this one ({__version__}). Update Freighter on this computer, then unpack again.")
    problems = bundle_problems(bundle, manifest)
    if problems and not getattr(args, "anyway", False):
        sys.exit("This bundle isn't complete, so nothing was unpacked:\n" + "\n".join(f"  - {p}" for p in problems)
                 + "\nOn the old computer, pack onto the drive again: it picks up where it stopped.\n"
                 "If the old computer is gone, unpack what's there with --anyway (or answer y in the menu).")
    src_host = manifest["source_host"]
    dest_known = known_roots(THIS_OS, home())
    landing = moved_root(src_host)
    landing.mkdir(parents=True, exist_ok=True)

    previous = previous_entries(landing)
    moved = [it for it in manifest["items"] if not (it["kind"] == "known" and it["id"] in dest_known)]
    places = landing_folders(moved, landing, previous, manifest["source_os"] == "windows")
    plan = []
    for it in manifest["items"]:
        if it["kind"] == "known" and it["id"] in dest_known:
            dest = dest_known[it["id"]][1]
        else:
            dest = places[it["id"]]
        if args.dest_for:
            for spec in args.dest_for:
                iid, _, folder = spec.partition("=")
                if iid == it["id"] and folder:
                    dest = Path(folder).expanduser()
        index = read_jsonl(bundle / "index" / f"{it['id']}.jsonl")
        plan.append((it, dest, index))

    total = sum(r["size"] for _, _, idx in plan for r in idx.values())
    print("Unpacking:")
    for it, dest, idx in plan:
        print(f"  {it['label']}: {len(idx)} files -> {dest}")
    short = space_shortfalls(plan)
    if short:
        sys.exit("Not enough free space, so nothing was unpacked:\n" + "\n".join(
            f"  {where}: needs {rs.human_size(need)}, has {rs.human_size(free)} free" for where, need, free in short)
            + "\nFree up space, or send big items elsewhere with --dest-for ID=FOLDER.")

    prog = Progress(total, "Copying")
    prog.add(0)
    conflicts, bad, entries = [], [], []
    for it, dest, idx in plan:
        data_root = bundle / "data" / it["id"]
        for rel, rec in idx.items():
            src = data_root / rec["stored"]
            target_rel = rec["rel"] if THIS_OS != "windows" else rec["stored"]
            dst = dest / target_rel
            if file_size(dst) is not None:
                if file_size(dst) == rec["size"] and hash_file(dst) == rec["sha256"]:
                    prog.add(rec["size"])
                    continue  # already there, identical
                dst = dst.with_name(f"{dst.stem} (from {src_host}){dst.suffix}")
                if file_size(dst) == rec["size"]:
                    prog.add(rec["size"])
                    continue
                conflicts.append(str(dst))
            try:
                digest = copy_and_hash(src, dst)
            except OSError as e:
                bad.append(f"{src}: {e}")
                continue
            if digest != rec["sha256"]:
                bad.append(f"{dst}: checksum mismatch (the copy on the drive may be damaged)")
            prog.add(rec["size"])
        entry = {"id": it["id"], "label": it["label"], "old": it["source_path"], "new": str(dest)}
        if THIS_OS == "windows":
            # files unpacked under a different name (Windows can't hold the original, or two names clashed):
            # repair must find them by this record, never by guessing (issue #63)
            renamed = {rec["rel"]: rec["stored"] for rec in index.values() if rec["stored"] != rec["rel"]}
            if renamed:
                entry["renamed"] = renamed
        entries.append(entry)

    # keep what earlier unpacks from this computer mapped, unless this one maps the same folder again
    fold = manifest["source_os"] == "windows"
    mine = {norm(e["old"], fold) for e in entries}
    entries += [e for e in previous if norm(e["old"], fold) not in mine]
    entries.sort(key=lambda e: len(e["old"]), reverse=True)
    pathmap = {
        "source_host": src_host, "source_os": manifest["source_os"], "dest_os": THIS_OS,
        "created": dt.datetime.now().isoformat(timespec="seconds"),
        "entries": entries,
        "home": {"old": manifest["source_home"], "new": str(home())},
    }
    pm_path = landing / "pathmap.json"
    pm_path.write_text(json.dumps(pathmap, indent=2), encoding="utf-8")
    if (bundle / "plugins.csv").is_file():
        shutil.copy2(bundle / "plugins.csv", landing / f"plugins-{src_host}.csv")

    notes = []
    for name, one, many in PACK_REPORTS:
        n = report_count(bundle / name)
        if n:
            shutil.copy2(bundle / name, landing / name)
            notes.append(f"{n} {one if n == 1 else many} (see {landing / name})")
    lines = [f"Freighter {__version__} unpack from {src_host} ({manifest['source_os']}) on {pathmap['created']}", ""]
    if problems:
        lines += ["THIS BUNDLE WASN'T COMPLETE. Unpacked what was there:"] + [f"  - {p}" for p in problems] + [""]
    if notes:
        lines += ["From the old computer:"] + [f"  - {n}" for n in notes] + [""]
    lines += [f"{e['label']}: {e['old']} -> {e['new']}" for e in entries]
    if conflicts:
        lines += ["", "Files that already existed here with different contents. The moved copy was saved",
                  f"next to them with '(from {src_host})' in the name:"] + conflicts
    if bad:
        lines += ["", "Problems:"] + bad
    (landing / "unpack-report.txt").write_text("\n".join(lines), encoding="utf-8")

    for note in notes:
        print(f"From the old computer: {note}")
    if problems or bad:
        print(f"\nUnpacked, with problems: see {landing / 'unpack-report.txt'}. Path map: {pm_path}")
    else:
        print(f"\nDone. Every file is in place and checked. Path map: {pm_path}")
    if conflicts:
        print(f"{len(conflicts)} files already existed with different contents; see unpack-report.txt.")
    if bad:
        print(f"{len(bad)} problems; see unpack-report.txt.")
    print("\nNext:")
    print("  Repair projects: menu option 6, or " + tool_cmd("freighter", "fix-ableton") + " and "
          + tool_cmd("freighter", "check-fl"))
    print(f"  Plugins to reinstall: {tool_cmd('manifest', '--compare')} \"{landing / f'plugins-{src_host}.csv'}\"")
    return 0


def tool_cmd(tool, args=""):
    """How to run one of Freighter's tools from a terminal: the built program next to this one, or the script."""
    if getattr(sys, "frozen", False):
        cmd = f"{tool}.exe" if rs.IS_WIN else f"./{tool}"
    else:
        cmd = f"{'py' if rs.IS_WIN else 'python3'} {tool}.py"
    return f"{cmd} {args}".strip()


# --------------------------------------------------------------------------- path mapping

def norm(p, fold_case):
    # NFC: Macs often store "é" as e + accent (two characters), Windows as one; compare them as equal
    s = unicodedata.normalize("NFC", re.sub(r"/+", "/", p.replace("\\", "/")).rstrip("/"))
    return s.lower() if fold_case else s


def load_pathmaps(explicit):
    paths = [Path(explicit)] if explicit else sorted((home() / "Music" / "Freighter").glob("*/pathmap.json"))
    maps = []
    for p in paths:
        try:
            maps.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            print(f"Couldn't read {p}")
    return maps


def candidates(old, maps):
    """Where a file from the old computer may be now, most specific first."""
    out = []
    for pm in maps:
        fold = pm["source_os"] == "windows"
        o_raw = norm(old, False)
        o = o_raw.lower() if fold else o_raw
        pairs = [(e["old"], e["new"], e.get("renamed") or {}) for e in pm["entries"]] \
            + [(pm["home"]["old"], pm["home"]["new"], {})]
        for old_root, new_root, renamed in pairs:
            k = norm(old_root, fold)
            if o == k or o.startswith(k + "/"):
                rest = o_raw[len(k):].lstrip("/")
                if renamed and rest:
                    lookup = {norm(r, fold): v for r, v in renamed.items()}
                    hit = lookup.get(norm(rest, fold))
                    if hit:
                        out.append(Path(new_root).joinpath(*hit.split("/")))
                forms = (rest, unicodedata.normalize("NFC", rest), unicodedata.normalize("NFD", rest))
                for r in dict.fromkeys(f for x in forms for f in (x, safe_rel(x))) if rest else ("",):
                    # safe_rel: names Windows can't hold (e.g. "kick: hard.wav") were stored renamed.
                    # NFC/NFD: the moved file may spell accented letters either way (see norm)
                    out.append(Path(new_root).joinpath(*r.split("/")) if r else Path(new_root))
    return out


HIDE = None  # during a rehearsal: (folders that count as gone, or None for everything, rehearsal folder)


def _norm_abs(p):
    return os.path.normcase(os.path.abspath(str(p)))


def exists(p):
    """os.path.exists, except that a rehearsal pretends the old computer's files are gone."""
    if not p:
        return False
    if HIDE:
        s, (gone, keep) = _norm_abs(p), HIDE
        under = lambda root: s == root or s.startswith(root.rstrip(os.sep) + os.sep)
        if (gone is None or any(under(g) for g in gone)) and not under(keep):
            return False
    return os.path.exists(long_path(p))


def locate(old, maps):
    """(status, new path or None). status: ok | moved | missing."""
    # A Windows path ("C:/...") never exists on a Mac, and vice versa, so this is safe across OSes.
    if old and exists(old):
        return "ok", Path(old)
    for c in candidates(old, maps):
        if exists(c):
            return "moved", c
    return "missing", None


def project_search_roots(maps, folders):
    if folders:
        return [Path(f).expanduser() for f in folders]
    h = home()
    roots = all_user_folders(h)
    for pm in maps:
        roots += [Path(e["new"]) for e in pm["entries"]]
    seen, out = set(), []
    for r in roots:
        key = str(r)
        if r.is_dir() and not any(key.startswith(s + os.sep) or key == s for s in seen):
            seen.add(key)
            out.append(r)
    return out


def find_projects(roots, ext, include_backups=False):
    """Project files under roots. Autosave copies (in a project's own Backup folder) only with include_backups;
    a folder that merely has \"backup\" in its name (an archive of old projects) is searched like any other."""
    found = set()
    for root in roots:
        for cur, dirs, files in os.walk(long_path(root), onerror=lambda e: None):
            dirs[:] = [d for d in dirs if not skip_dir(cur, d) and not d.startswith(".")]
            autosave = os.path.basename(cur).lower() == "backup"
            if autosave and not include_backups:
                continue
            for fn in files:
                if fn.lower().endswith(ext) and not fn.startswith("._"):
                    found.add(Path(short_path(os.path.join(cur, fn))))
    return sorted(found, key=lambda p: str(p).lower())


# --------------------------------------------------------------------------- Ableton

FILEREF_RE = re.compile(r"<FileRef(?:\s[^>]*)?>(.*?)</FileRef>", re.S)
PATH_RE = re.compile(r'(<Path Value=")([^"]*)(")')
# Samples sit in <SampleRef><FileRef>...; other paths in a set are plugin binaries (PluginDesc) or the presets a
# device came from, which aren't samples. LivePackName marks Core Library / installed Pack content: Live finds it.
SAMPLEREF_RE = re.compile(r"<SampleRef>(.*?)</SampleRef>", re.S)
PACK_NAME_RE = re.compile(r'<LivePackName Value="([^"]*)"')
CREATOR_VERSION_RE = re.compile(r'Creator="Ableton Live (\d+)')


def als_sample_refs(text):
    """Paths of the samples a Live 11+ set uses, as stored (issue #60: only samples, never plugins or Pack content)."""
    out = []
    for sm in SAMPLEREF_RE.finditer(text):
        for fm in FILEREF_RE.finditer(sm.group(1)):
            block = fm.group(1)
            pack = PACK_NAME_RE.search(block)
            if pack and pack.group(1):
                continue
            pm = PATH_RE.search(block)
            if pm and pm.group(2):
                out.append(html.unescape(pm.group(2)))
    return list(dict.fromkeys(out))
REL_RE = re.compile(r'<RelativePath Value="([^"]*)"')
CREATOR_RE = re.compile(r'Creator="([^"]*)"')


def xml_attr_escape(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def read_als(path):
    with open(long_path(path), "rb") as f:
        raw = f.read()
    try:
        return gzip.decompress(raw).decode("utf-8"), True
    except OSError:
        return raw.decode("utf-8", errors="replace"), False


def als_new_format(text):
    """Live 11+ stores plain <Path Value=...> in each FileRef; Live 10 and older use binary aliases. (A Live 10 set
    can still contain <Path Value> for a VST2 plugin, so check the version that saved it first.)"""
    v = CREATOR_VERSION_RE.search(text[:2000])
    if v:
        return int(v.group(1)) >= 11
    return any(PATH_RE.search(m.group(1)) for m in SAMPLEREF_RE.finditer(text))


def write_replace(path, data):
    """Write a file so that a crash or power cut leaves either the old file or the new one, never half of one:
    write a temporary file next to it, then swap it in."""
    tmp = path.with_name(path.name + ".freighter-part")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def fix_als(path, maps, dry_run=False):
    text, gz = read_als(path)
    creator = (CREATOR_RE.search(text[:2000]) or [None, "unknown Live version"])[1]
    result = {"project": str(path), "creator": creator, "ok": 0, "fixed": 0, "missing": [], "old_format": False}
    if not als_new_format(text):
        result["old_format"] = True
        result["refs"] = len(FILEREF_RE.findall(text))
        return result
    proj_dir = Path(path).parent

    def fix_ref(m):
        block = m.group(0)
        pm = PATH_RE.search(block)
        if not pm:
            return block
        old = html.unescape(pm.group(2))
        if not old:
            return block
        pack = PACK_NAME_RE.search(block)
        if pack and pack.group(1):
            result["ok"] += 1   # Core Library / Pack content: Live finds it by itself
            return block
        rel = REL_RE.search(block)
        if rel and rel.group(1) and exists(proj_dir / html.unescape(rel.group(1))):
            result["ok"] += 1  # Live finds it relative to the project folder
            return block
        status, new = locate(old, maps)
        if status == "ok":
            result["ok"] += 1
            return block
        if status == "missing":
            result["missing"].append(old)
            return block
        result["fixed"] += 1
        new_s = new.as_posix() if THIS_OS != "windows" else str(new).replace("\\", "/")
        return block[:pm.start(2)] + xml_attr_escape(new_s) + block[pm.end(2):]

    new_text = SAMPLEREF_RE.sub(lambda sm: FILEREF_RE.sub(fix_ref, sm.group(0)), text)   # samples only
    if result["fixed"] and not dry_run:
        backup_dir = proj_dir / "Freighter backup"
        backup_dir.mkdir(exist_ok=True)
        backup = backup_dir / Path(path).name
        if not backup.exists():
            shutil.copy2(path, backup)
        data = new_text.encode("utf-8")
        write_replace(Path(path), gzip.compress(data) if gz else data)
    return result


def cmd_fix_ableton(args):
    maps = load_pathmaps(args.pathmap)
    if not maps:
        print("No path map found yet (it's created by 'unpack'). Checking projects for missing samples only.")
    roots = project_search_roots(maps, args.folder)
    print("Looking for Ableton projects in:\n  " + "\n  ".join(map(str, roots)), flush=True)
    projects = find_projects(roots, ".als")
    print(f"Found {len(projects)} projects.\n")
    totals = {"fixed": 0, "ok": 0, "missing": 0, "old": 0, "changed": 0}
    report = [f"Freighter {__version__} Ableton repair, {dt.datetime.now():%Y-%m-%d %H:%M}", ""]
    for p in projects:
        try:
            r = fix_als(p, maps, args.dry_run)
        except Exception as e:  # one bad project shouldn't stop the run
            print(f"  ! {p.name}: couldn't read ({e.__class__.__name__})")
            continue
        if r["old_format"]:
            totals["old"] += 1
            report.append(f"OLDER FORMAT  {p}  ({r['creator']})")
            continue
        totals["fixed"] += r["fixed"]
        totals["ok"] += r["ok"]
        totals["missing"] += len(r["missing"])
        totals["changed"] += bool(r["fixed"])
        if r["fixed"] or r["missing"]:
            print(f"  {p.name}: {r['fixed']} repointed, {r['ok']} fine, {len(r['missing'])} missing")
        report.append(f"{'FIXED' if r['fixed'] else 'OK' if not r['missing'] else 'MISSING'}  {p}  "
                      f"repointed={r['fixed']} fine={r['ok']} missing={len(r['missing'])}")
        report += [f"    missing: {m}" for m in r["missing"]]

    verb = "would repoint" if args.dry_run else "repointed"
    print(f"\n{totals['changed']} projects changed: {verb} {totals['fixed']} samples, "
          f"{totals['ok']} already fine, {totals['missing']} still missing.")
    if totals["changed"] and not args.dry_run:
        print("Originals are saved in a 'Freighter backup' folder next to each changed project.")
    if totals["old"]:
        print(f"{totals['old']} projects are from Live 10 or older, which this can't rewrite. Open each in Live, "
              "then File > Manage Files > Locate, and point Live at the moved sample folder.")
    out = (home() / "Music" / "Freighter")
    out.mkdir(parents=True, exist_ok=True)
    (out / "ableton-fix-report.txt").write_text("\n".join(report), encoding="utf-8")
    print(f"Full list: {out / 'ableton-fix-report.txt'}")
    return 0


# --------------------------------------------------------------------------- FL Studio

FLP_TEXT_SAMPLE = 196   # sample file name event
FLP_TEXT_VERSION = 199  # FL version string
FL_PLACEHOLDER = re.compile(r"^%FL[^%]*%", re.I)


def flp_samples(path):
    """Read the sample paths an .flp project references. Read-only.
    Returns (FL version string, [paths]). Raises ValueError for unreadable files."""
    with open(long_path(path), "rb") as f:
        data = f.read()
    if data[:4] != b"FLhd":
        raise ValueError("not an FL Studio project")
    hlen = struct.unpack("<I", data[4:8])[0]
    pos = 8 + hlen
    if data[pos:pos + 4] != b"FLdt":
        raise ValueError("no data chunk")
    dlen = struct.unpack("<I", data[pos + 4:pos + 8])[0]
    pos += 8
    end = min(len(data), pos + dlen)
    unicode, version, samples = False, "", []
    while pos < end:
        eid = data[pos]
        pos += 1
        if eid < 64:
            pos += 1
        elif eid < 128:
            pos += 2
        elif eid < 192:
            pos += 4
        else:
            n = shift = 0
            while True:
                if pos >= end:
                    raise ValueError("truncated")
                b = data[pos]
                pos += 1
                n |= (b & 0x7F) << shift
                shift += 7
                if not b & 0x80:
                    break
            payload = data[pos:pos + n]
            pos += n
            if eid == FLP_TEXT_VERSION:
                version = payload.rstrip(b"\0").decode("ascii", "replace")
                nums = [int(x) for x in re.findall(r"\d+", version)[:2]] + [0, 0]
                unicode = nums[0] > 11 or (nums[0] == 11 and nums[1] >= 5)
            elif eid == FLP_TEXT_SAMPLE and payload:
                s = payload.decode("utf-16-le" if unicode else "latin-1", "replace").rstrip("\0")
                if s:
                    samples.append(s)
    return version, samples


def cmd_check_fl(args):
    maps = load_pathmaps(args.pathmap)
    roots = project_search_roots(maps, args.folder)
    print("Looking for FL Studio projects in:\n  " + "\n  ".join(map(str, roots)), flush=True)
    projects = find_projects(roots, ".flp", include_backups=args.include_backups)
    print(f"Found {len(projects)} projects.\n")
    counts = {"ok": 0, "builtin": 0, "moved": 0, "missing": 0}
    unreadable, add_folders = [], set()
    report = [f"Freighter {__version__} FL Studio check, {dt.datetime.now():%Y-%m-%d %H:%M}", ""]
    for p in projects:
        try:
            version, samples = flp_samples(p)
        except (ValueError, OSError, struct.error) as e:
            unreadable.append(f"{p} ({e})")
            continue
        miss, moved = [], 0
        for s in dict.fromkeys(samples):
            if FL_PLACEHOLDER.match(s):
                counts["builtin"] += 1
                continue
            status, new = locate(s, maps)
            counts[status] += 1
            if status == "moved":
                moved += 1
                for pm in maps:
                    for e in pm["entries"]:
                        if str(new).startswith(e["new"]):
                            add_folders.add(e["new"])
            elif status == "missing":
                miss.append(s)
        if miss or moved:
            print(f"  {p.name} (FL {version}): {moved} moved, {len(miss)} missing")
        report.append(f"{p}  FL {version}  samples={len(set(samples))} moved={moved} missing={len(miss)}")
        report += [f"    missing: {m}" for m in miss]

    print(f"\nSamples: {counts['ok']} found in place, {counts['builtin']} FL built-in, "
          f"{counts['moved']} found in their new location, {counts['missing']} missing.")
    if add_folders:
        print("\nFL Studio can't be pointed at moved files automatically yet. In FL Studio, open "
              "Options > File settings, and add these under 'Browser extra search folders':")
        for f in sorted(add_folders):
            print(f"  {f}")
        print("FL then finds moved samples when it opens a project. Save each project afterwards.")
    if unreadable:
        print(f"\n{len(unreadable)} projects couldn't be read (listed in the report).")
    out = home() / "Music" / "Freighter"
    out.mkdir(parents=True, exist_ok=True)
    (out / "fl-check-report.txt").write_text("\n".join(report + ["", "Unreadable:"] + unreadable),
                                             encoding="utf-8")
    print(f"Full list: {out / 'fl-check-report.txt'}")
    return 0


# --------------------------------------------------------------------------- scan

def cmd_scan(args):
    h = home()
    roots = all_user_folders(h)
    rows = []
    print("Looking for projects...", flush=True)
    for p in find_projects(roots, ".flp"):
        try:
            _v, samples = flp_samples(p)
            refs = sum(1 for s in set(samples) if not FL_PLACEHOLDER.match(s))
        except (ValueError, OSError, struct.error):
            refs = None
        rows.append(("FL Studio", p, refs))
    for p in find_projects(roots, ".als"):
        try:
            text, _ = read_als(p)
            refs = len(als_sample_refs(text)) if als_new_format(text) else None
        except Exception:
            refs = None
        rows.append(("Ableton", p, refs))

    def row_html(daw, p, refs):
        try:
            st = p.stat()
            size, mod = rs.human_size(st.st_size), dt.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d")
        except OSError:
            size = mod = ""
        return (f"<tr><td>{rs.e(p.stem)}</td><td>{daw}</td><td>{'' if refs is None else refs}</td>"
                f"<td>{size}</td><td>{mod}</td><td class=path>{rs.e(p.parent)}</td></tr>")

    by = {d: sum(1 for r in rows if r[0] == d) for d in ("FL Studio", "Ableton")}
    extra = (f"<h2>Projects ({len(rows)})</h2><p class=sub>{by['FL Studio']} FL Studio, {by['Ableton']} Ableton "
             "(autosave/backup folders skipped). 'Files used' counts the samples and files each project "
             "points to outside FL's built-in content.</p><div class=tablewrap><table><thead><tr><th>Project</th>"
             "<th>DAW</th><th>Files used</th><th>Size</th><th>Modified</th><th>Folder</th></tr></thead><tbody>"
             + "".join(row_html(*r) for r in sorted(rows, key=lambda r: str(r[1]).lower()))
             + "</tbody></table></div>")
    html_path, _ = rs.run_scan(Path(args.out) if args.out else None, args.extra_dir, args.quick, None, extra)
    print(f"Projects: {by['FL Studio']} FL Studio, {by['Ableton']} Ableton.")
    if not args.no_open:
        import webbrowser
        webbrowser.open(html_path.as_uri())
    return 0


# --------------------------------------------------------------------------- rehearse

FL_USER_DATA = re.compile(r"^%FLStudioUserData%", re.I)


def fl_user_data(h):
    """What FL's %FLStudioUserData% means for home folder h: <FL user folder>/FL Studio."""
    return known_roots(THIS_OS, h)["fl_user"][1] / "FL Studio"


def same_contents(a, b):
    """True when two files hold the same bytes (size first, then a hash). A rehearsal uses this so a sample
    moved to the wrong place, or the wrong sample, can't pass."""
    try:
        if os.stat(long_path(a)).st_size != os.stat(long_path(b)).st_size:
            return False
        return hash_file(a) == hash_file(b)
    except OSError:
        return False


def project_refs(p):
    """(kind, [referenced file paths as the project stored them]) or (kind, None) if unreadable."""
    if p.suffix.lower() == ".flp":
        try:
            # FL's own sounds (%FLStudioFactoryData%) come with FL; the user's data (%FLStudioUserData%) is checked
            return "FL Studio", [s for s in dict.fromkeys(flp_samples(p)[1])
                                 if FL_USER_DATA.match(s) or not FL_PLACEHOLDER.match(s)]
        except (ValueError, OSError, struct.error):
            return "FL Studio", None
    src = p.parent / "Freighter backup" / p.name  # the original, if fix-ableton rewrote it
    try:
        text, _ = read_als(src if src.is_file() else p)
    except Exception:
        return "Ableton", None
    if not als_new_format(text):
        return "Ableton (Live 10 or older)", None
    return "Ableton", als_sample_refs(text)


def cmd_rehearse(args):
    """A full move on one computer: pack, unpack into a stand-in 'new computer' folder, repair,
    then check every project's samples. Your real folders are only read, never changed."""
    global HIDE
    # always a folder of its own: --to E:\\ must not turn the whole drive into "a rehearsal" (issue #62)
    base = (Path(args.to).expanduser() / "Freighter Rehearsal" if args.to
            else home() / "Music" / "Freighter" / "Rehearsal").resolve()
    drive, new_home = base / "drive", base / "new-computer"
    real_home = home()
    items = pack_items(args.include, set(args.skip))
    need = sum(size for it in items
               for _r, _a, size in ([(n, None, (Path(it["source_path"]) / n).stat().st_size)
                                     for n in it["only"]] if "only" in it else walk_files(it["source_path"], [])))
    base.mkdir(parents=True, exist_ok=True)
    (base / REHEARSAL_MARKER).write_text("A Freighter rehearsal. Freighter never packs this folder.\n", encoding="utf-8")
    free = shutil.disk_usage(base).free
    print(f"Rehearsal needs about {rs.human_size(need * 2)} (a bundle plus an unpacked copy); "
          f"{rs.human_size(free)} free here.")
    if need * 2 > free:
        sys.exit("Not enough space. Leave big items out with --skip ID, or use --to on a bigger drive.")

    print("\n== 1/4  Pack, as the old computer")
    cmd_pack(argparse.Namespace(to=str(drive), list=False, include=args.include, skip=args.skip,
                                include_programs=False))
    bundles = sorted(drive.glob("Freighter-*"), key=lambda p: p.stat().st_mtime)
    if not bundles:
        sys.exit("Packing produced no bundle.")

    # act as the new computer until the rehearsal ends, however it ends: later menu choices must use the
    # real folders again (issue #16)
    prev_home = os.environ.get("FREIGHTER_HOME")
    try:
        os.environ["FREIGHTER_HOME"] = str(new_home)
        new_home.mkdir(parents=True, exist_ok=True)
        # the old computer is gone: everything outside the rehearsal counts as missing, not just the home folder,
        # so a sample on D:\\ or an external drive passes only if its moved copy works (issue #65)
        HIDE = (None, _norm_abs(base))
        print("\n== 2/4  Unpack, as the new computer (the old computer's files now count as gone)")
        cmd_unpack(argparse.Namespace(bundle=str(bundles[-1]), dest_for=[]))
        print("\n== 3/4  Repair projects")
        cmd_fix_ableton(argparse.Namespace(pathmap=None, folder=[], dry_run=False))
        cmd_check_fl(argparse.Namespace(pathmap=None, folder=[], include_backups=False))

        print("\n== 4/4  Check every project on the new computer")
        maps = load_pathmaps(None)
        roots = project_search_roots(maps, [])
        totals = {"kept": 0, "lost": 0, "already": 0, "projects": 0, "unchecked": 0}
        lost, unchecked, already = [], [], []
        for p in find_projects(roots, ".flp") + find_projects(roots, ".als"):
            kind, refs = project_refs(p)
            if refs is None:
                totals["unchecked"] += 1
                unchecked.append(f"{p}  ({kind})")
                continue
            totals["projects"] += 1
            for r in refs:
                if in_program_place(r):
                    continue                     # comes with an installed program, not part of the move
                if FL_USER_DATA.match(r):        # FL's own user-data folder: compare old home with new home
                    rest = FL_USER_DATA.sub("", r).replace("\\", "/").lstrip("/")
                    original = fl_user_data(real_home).joinpath(*rest.split("/"))
                    moved = fl_user_data(new_home).joinpath(*rest.split("/"))
                    moved = moved if moved.exists() else None
                else:
                    original = Path(r)
                    status, moved = locate(r, maps)
                    moved = moved if status != "missing" else None
                before = os.path.exists(long_path(original))     # on the old computer (it's still here)
                if not before:
                    totals["already"] += 1
                    already.append(f"{p.name}: {r}")
                elif moved is not None and same_contents(original, moved):
                    totals["kept"] += 1
                else:
                    totals["lost"] += 1
                    lost.append(f"{p.name}: {r}")

        checked = totals["kept"] + totals["lost"]
        lines = [
            f"Freighter {__version__} rehearsal, {dt.datetime.now():%Y-%m-%d %H:%M}",
            f"Projects checked: {totals['projects']} (+{totals['unchecked']} that can't be checked automatically)",
            f"Samples that worked before the move: {checked}",
            f"  still found after the move: {totals['kept']}",
            f"  LOST in the move: {totals['lost']}",
            f"Samples already missing before the move (not Freighter's fault): {totals['already']}",
        ]
        if lost:
            lines += ["", "Lost samples (please report these):"] + lost
        if already:
            lines += ["", "Already missing before the move (fix these on the old computer, e.g. plug in the "
                      "drive they were on):"] + already
        if unchecked:
            lines += ["", "Not checked automatically:"] + unchecked
        report = base / "rehearsal-report.txt"
        report.write_text("\n".join(lines), encoding="utf-8")
        print("\n" + "\n".join(lines[:6]))
        verdict = "PASS: every working sample survived the move." if not totals["lost"] else \
            f"FAIL: {totals['lost']} samples were lost. See {report}"
        print("\n" + verdict)
        if already:
            print(f"Note: {len(already)} samples were already missing before the move (listed in {report}).")
        print(f"Rehearsal files: {base}  (delete this folder when you're done; your real files weren't touched)")
        return 0 if not totals["lost"] else 2
    finally:
        HIDE = None
        if prev_home is None:
            os.environ.pop("FREIGHTER_HOME", None)
        else:
            os.environ["FREIGHTER_HOME"] = prev_home


# --------------------------------------------------------------------------- menu (double-click)

MENU = """
  _Freighter {version}  move your studio to a new computer

  On the OLD computer
    1  Scan my plugins (_Manifest report)
    2  See what a move would take
    3  Rehearse the move here, safely (nothing is changed)
    4  Pack for the move onto a drive

  On the NEW computer
    5  Unpack a bundle from the drive
    6  Repair projects after unpacking

  Tidy up
    7  Sort my sample folders into one library (_Crate)

    q  Quit
"""


def ask_path(prompt):
    """Ask for a folder. Dragging a folder into the window types its path, sometimes quoted or escaped."""
    raw = input(prompt).strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "'\"":
        raw = raw[1:-1]
    if not rs.IS_WIN:
        raw = re.sub(r"\\(.)", r"\1", raw)  # Terminal escapes spaces when you drag a folder in
    return raw


def write_crash_report(choice):
    """Save what went wrong, so a tester can send it in. Returns the file's path."""
    import platform
    import tempfile
    import traceback
    text = "\n".join([
        f"Freighter {__version__} crash report, {dt.datetime.now():%Y-%m-%d %H:%M:%S}",
        f"System: {platform.platform()}, Python {platform.python_version()}"
        + (", built program" if getattr(sys, "frozen", False) else ""),
        f"Menu choice: {choice}",
        "",
        traceback.format_exc(),
    ])
    for folder in (home() / "Music" / "Freighter", Path(tempfile.gettempdir())):
        try:
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / "freighter-crash.txt"
            path.write_text(text, encoding="utf-8")
            return path
        except OSError:
            continue
    return None


def interactive_menu():
    while True:
        print(MENU.format(version=__version__))
        try:
            choice = input("  Choose 1-7 (or q): ").strip().lower()
        except EOFError:   # nothing more to read (input closed): same as quitting
            return 0
        try:
            if choice == "1":
                html_path, _ = rs.run_scan()
                import webbrowser
                webbrowser.open(html_path.as_uri())
            elif choice == "2":
                cmd_pack(argparse.Namespace(to=None, list=True, include=[], skip=[], include_programs=False))
            elif choice == "3":
                cmd_rehearse(argparse.Namespace(to=None, include=[], skip=[]))
            elif choice == "4":
                drive = ask_path("  Drag the drive (or a folder on it) into this window, then press Enter: ")
                if drive:
                    cmd_pack(argparse.Namespace(to=drive, list=False, include=[], skip=[], include_programs=False))
            elif choice == "5":
                bundle = ask_path("  Drag the Freighter-... folder from the drive into this window, then press Enter: ")
                if bundle:
                    anyway = False
                    try:
                        manifest = json.loads((Path(bundle) / "manifest.json").read_text(encoding="utf-8"))
                        problems = bundle_problems(Path(bundle), manifest)
                    except (OSError, ValueError, KeyError):
                        problems = []
                    if problems:
                        print("\n  This bundle isn't complete:\n" + "\n".join(f"    - {p}" for p in problems))
                        print("  Best: on the old computer, choose 4 again to finish packing.")
                        anyway = input("  Unpack what's there anyway? (y/n): ").strip().lower().startswith("y")
                        if not anyway:
                            continue
                    cmd_unpack(argparse.Namespace(bundle=bundle, dest_for=[], anyway=anyway))
            elif choice == "6":
                cmd_fix_ableton(argparse.Namespace(pathmap=None, folder=[], dry_run=False))
                cmd_check_fl(argparse.Namespace(pathmap=None, folder=[], include_backups=False))
            elif choice == "7":
                import crate
                folder = ask_path("  Drag a sample folder (or drum kit) into this window, then press Enter: ")
                if folder:
                    items = crate.plan([folder])
                    if items:
                        lib = crate.default_library()
                        page = crate.write_report(items, lib.parent / "Freighter Crate preview", "_Crate preview")
                        import webbrowser
                        webbrowser.open(page.as_uri())
                        if input(f"  The preview is open. Build the library in {lib}? (y/n): ").strip().lower().startswith("y"):
                            made, links, copies, skipped = crate.build(items, lib, False)
                            print(f"  {made} files added to {lib} ({skipped} duplicates skipped). Originals untouched.")
            elif choice in ("q", "quit", "exit", ""):
                return 0
            else:
                print("  Type a number from 1 to 7, or q to quit.")
                continue
        except SystemExit as e:  # commands exit with a message on problems; show it and keep the menu
            if e.code not in (None, 0):
                print(f"\n  {e.code}")
        except KeyboardInterrupt:
            print("\n  Stopped.")
        except Exception as e:  # never let an error close a double-clicked window before it can be read
            report = write_crash_report(choice)
            print(f"\n  Something went wrong: {type(e).__name__}: {e}")
            if report:
                print(f"  The details are saved in {report}")
                print("  Please send that file with a bug report: https://github.com/chasubowl-creator/freighter/issues/new")
        try:
            input("\n  Press Enter to go back to the menu...")
        except EOFError:
            return 0


# --------------------------------------------------------------------------- main

def cmd_app(args):
    import app   # a plain import, so the built program includes it
    return app.run()


def main():
    ap = argparse.ArgumentParser(description="Move a music setup between computers.",
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("--version", action="version", version=f"Freighter {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="plugin report plus a list of your projects")
    s.add_argument("--extra-dir", action="append", default=[], metavar="FOLDER")
    s.add_argument("--out", metavar="FOLDER")
    s.add_argument("--quick", action="store_true")
    s.add_argument("--no-open", action="store_true")
    s.set_defaults(fn=cmd_scan)

    s = sub.add_parser("pack", help="copy your files into a bundle on a drive")
    s.add_argument("--to", metavar="DRIVE", help="drive or folder to create the bundle on")
    s.add_argument("--list", action="store_true", help="only show what would be packed")
    s.add_argument("--include", action="append", default=[], metavar="FOLDER", help="also pack this folder")
    s.add_argument("--skip", action="append", default=[], metavar="ID", help="leave out this item")
    s.add_argument("--include-programs", action="store_true",
                   help="also pack programs and installers found in your folders (left out by default)")
    s.set_defaults(fn=cmd_pack)

    s = sub.add_parser("unpack", help="put a bundle's files in place on this computer")
    s.add_argument("bundle")
    s.add_argument("--anyway", action="store_true",
                   help="unpack what's there even if packing the bundle wasn't finished")
    s.add_argument("--dest-for", action="append", default=[], metavar="ID=FOLDER",
                   help="put one item somewhere else, e.g. used-3fa2b1c9=/Volumes/Samples (IDs are shown by pack --list)")
    s.set_defaults(fn=cmd_unpack)

    s = sub.add_parser("fix-ableton", help="repoint samples in Ableton projects (Live 11+)")
    s.add_argument("--pathmap", metavar="FILE")
    s.add_argument("--folder", action="append", default=[], metavar="FOLDER", help="only look here")
    s.add_argument("--dry-run", action="store_true", help="report only, change nothing")
    s.set_defaults(fn=cmd_fix_ableton)

    s = sub.add_parser("check-fl", help="find moved and missing samples in FL Studio projects")
    s.add_argument("--pathmap", metavar="FILE")
    s.add_argument("--folder", action="append", default=[], metavar="FOLDER", help="only look here")
    s.add_argument("--include-backups", action="store_true", help="also check autosave/backup projects")
    s.set_defaults(fn=cmd_check_fl)

    s = sub.add_parser("rehearse", help="practice the whole move on this computer, safely")
    s.add_argument("--to", metavar="FOLDER", help="a drive or folder to rehearse on; Freighter makes a 'Freighter Rehearsal' folder there (default: Music/Freighter/Rehearsal)")
    s.add_argument("--include", action="append", default=[], metavar="FOLDER")
    s.add_argument("--skip", action="append", default=[], metavar="ID")
    s.set_defaults(fn=cmd_rehearse)

    s = sub.add_parser("app", help="open Freighter in your web browser (preview)")
    s.set_defaults(fn=cmd_app)
    s = sub.add_parser("menu", help="the numbered menu (what double-clicking opens)")
    s.set_defaults(fn=lambda a: interactive_menu())

    if len(sys.argv) == 1:  # double-clicked, or run with no options
        return interactive_menu()
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
