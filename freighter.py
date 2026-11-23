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
    python3 freighter.py fix-fl                       repoint samples in FL Studio projects (check-fl: report only)
    python3 manifest.py --compare BUNDLE/plugins.csv   plugins still to reinstall

Needs only Python 3.8+. Nothing leaves your computer. Use "py" instead of "python3" on Windows.
"""

import argparse
import contextlib
import csv
import datetime as dt
import functools
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
import crate  # noqa: E402  (its sound-type words tell kit parts from kits, issue #76)
import manifest as rs  # noqa: E402
from freighter_version import __version__  # noqa: E402
try:
    from freighter_build import BUILD  # noqa: E402  written by the release build: v0.9.0-test.2, or main@6e6a9bd
except ImportError:
    BUILD = None   # running from the source code

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


def fl_clean_path(value):
    """FL stores folder values like '"/Users/me/Desktop/Kits/",' : drop the quotes and the trailing comma."""
    v = (value or "").strip().rstrip(",").strip().strip('"').strip()
    return v or None


def fl_settings_from_tree(root):
    """FL's folder settings from its reg.xml (Mac). Same keys as the Windows registry."""
    out = {"user_data": None, "search_paths": [], "recent": []}
    versions = []
    for il in root.iter("Key"):
        if il.get("Name") != "Image-Line":
            continue
        for k in il.findall("Key"):
            name = k.get("Name") or ""
            if name == "Shared":
                for paths in k.findall("Key[@Name='Paths']"):
                    for v in paths.findall("Value[@Name='Shared data']"):
                        out["user_data"] = fl_clean_path(v.text)
            m = re.fullmatch(r"FL Studio (\d+)", name)
            if m:
                versions.append((int(m.group(1)), k))
    for _ver, k in sorted(versions, key=lambda x: -x[0]):   # newest FL first
        for sp in k.findall("Key[@Name='Search paths']"):
            for v in sp.findall("Value"):
                path = fl_clean_path(v.text)
                if path and path not in out["search_paths"]:
                    out["search_paths"].append(path)
        if not out["recent"]:
            for mru in k.findall("Key[@Name='MRU']"):
                out["recent"] = [x for x in (fl_clean_path(v.text) for v in mru.findall("Value")) if x]
    return out


def fl_settings_from_registry():
    out = {"user_data": None, "search_paths": [], "recent": []}
    import winreg
    base = r"Software\Image-Line"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, base + r"\Shared\Paths") as k:
            out["user_data"] = fl_clean_path(winreg.QueryValueEx(k, "Shared data")[0])
    except OSError:
        pass
    versions = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, base) as k:
            i = 0
            while True:
                try:
                    name = winreg.EnumKey(k, i)
                except OSError:
                    break
                m = re.fullmatch(r"FL Studio (\d+)", name)
                if m:
                    versions.append((int(m.group(1)), name))
                i += 1
    except OSError:
        return out

    def values(path):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
                found, i = [], 0
                while True:
                    try:
                        _n, v, _t = winreg.EnumValue(k, i)
                    except OSError:
                        return found
                    if isinstance(v, str) and fl_clean_path(v):
                        found.append(fl_clean_path(v))
                    i += 1
        except OSError:
            return []
    for _ver, name in sorted(versions, key=lambda x: -x[0]):
        for path in values(f"{base}\\{name}\\Search paths"):
            if path not in out["search_paths"]:
                out["search_paths"].append(path)
        if not out["recent"]:
            out["recent"] = values(f"{base}\\{name}\\MRU")
    return out


def fl_settings():
    """What FL Studio's own settings say about folders on this computer (issue #69): its user-data folder
    ("Shared data"), the browser's extra search folders (usually the user's sample libraries) and recent projects.
    Only for the real home folder: a rehearsal's stand-in or a test has no FL settings of its own."""
    empty = {"user_data": None, "search_paths": [], "recent": []}
    if os.environ.get("FREIGHTER_HOME"):
        return empty
    try:
        if rs.IS_WIN:
            return fl_settings_from_registry()
        if rs.IS_MAC:
            import xml.etree.ElementTree as ET
            f = real_home() / "Library" / "Preferences" / "Image-Line" / "reg.xml"
            if f.is_file():
                return fl_settings_from_tree(ET.parse(f).getroot())
    except Exception:            # unreadable settings mean "nothing extra", never a failed move
        pass
    return empty


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
    if os_name == THIS_OS and _norm_abs(h) == _norm_abs(home()):
        fl = fl_settings().get("user_data")
        if fl and Path(fl).is_dir():
            roots["fl_user"] = (roots["fl_user"][0], Path(fl))   # FL says where its user-data folder really is
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


def file_stat(p):
    """os.stat of the file at p, or None if there isn't one (a folder doesn't count)."""
    try:
        st = os.stat(long_path(p))
    except OSError:
        return None
    return None if stat_is_dir(st) else st


def stat_is_dir(st):
    return (st.st_mode & 0o170000) == 0o040000


def file_size(p):
    """Size of the file at p, or None if there isn't one (works for long Windows paths)."""
    try:
        st = os.stat(long_path(p))
    except OSError:
        return None
    return st.st_size if not os.path.isdir(long_path(p)) else None


def copy_and_hash(src, dst, direct=False):
    """Copy src to dst; return the SHA-256 of what was copied. Unpack copies through a temporary name, so a copy
    cut off part-way never sits in someone's folders under the real name. Pack writes straight into the bundle
    (direct): a file only counts as packed once its index line is written after the copy, so a cut-off copy is
    simply copied again, and that saves a rename per file. Only the file's times are copied, not its permissions
    or flags: two system calls fewer per file (issue #74)."""
    dst = Path(dst)
    os.makedirs(long_path(dst.parent), exist_ok=True)
    final = long_path(dst)
    target = final if direct else long_path(dst.with_name(dst.name + ".freighter-part"))
    h = hashlib.sha256()
    with open(long_path(src), "rb") as f, open(target, "wb") as out:
        st = os.fstat(f.fileno())
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
            out.write(chunk)
    if not direct:
        os.replace(target, final)
    try:
        os.utime(final, ns=(st.st_atime_ns, st.st_mtime_ns))   # keep "date modified" for sorting by date
    except OSError:
        pass
    return h.hexdigest()


def hash_file(p, from_drive=False):
    """SHA-256 of a file. from_drive asks a Mac to read the drive itself rather than a copy still in memory
    (the drive check, issue #77); elsewhere, unplugging and replugging the drive first does that."""
    h = hashlib.sha256()
    with open(long_path(p), "rb") as f:
        if from_drive and rs.IS_MAC:
            try:
                import fcntl
                fcntl.fcntl(f.fileno(), 48, 1)   # F_NOCACHE
            except Exception:
                pass
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


def walk_files(root, left_behind=None, keep=None):
    """Yield (relative posix path, absolute path, size) for every real file under root, folder by folder in the
    same order os.walk would. With a left_behind list, programs and installers are skipped and recorded there
    instead. Uses scandir's directory entries, so links are spotted without asking the disk again, and on Windows
    sizes come free with the listing (issue #74). keep(path, name, is_dir, stat) can leave out a file or a whole
    folder (a time-range move leaves old projects out, issue #76)."""
    root = Path(root)
    stack = [(long_path(root), "")]   # walk the long form so deep folders on Windows aren't skipped
    while stack:
        cur, prefix = stack.pop()
        try:
            with os.scandir(cur) as listing:
                entries = list(listing)
        except OSError:
            continue
        subdirs = []
        for e in entries:
            try:
                if e.is_symlink():
                    continue
                is_dir = e.is_dir()
            except OSError:
                continue
            if is_dir:
                if skip_dir(cur, e.name) or (keep is not None and not keep(e.path, e.name, True, None)):
                    continue
                if left_behind is not None and is_program(e.path):   # app and plugin bundles are folders
                    left_behind.append(short_path(e.path))
                    continue
                subdirs.append((e.path, prefix + e.name + "/"))
                continue
            fn = e.name
            if fn.lower() in JUNK_FILES or fn.startswith("._") or fn.endswith(".freighter-part"):
                continue
            if left_behind is not None and is_program(e.path):
                left_behind.append(short_path(e.path))
                continue
            try:
                st = e.stat()
            except OSError:
                continue
            if keep is not None and not keep(e.path, fn, False, st):
                continue
            rel = prefix + fn
            ap = root.joinpath(*rel.split("/"))
            if is_cloud_only(st):
                CLOUD_ONLY.append((ap, st.st_size))
            yield rel, ap, st.st_size
        stack.extend(reversed(subdirs))   # depth first, in listing order, like os.walk


# What the last pack / unpack did, for the app to show (one job runs at a time).
LAST = {}

# The app watches long jobs through this: set PROGRESS.listener(label, done, total) on the job's thread.
# A listener may raise Cancelled to stop the job; an interrupted pack resumes where it stopped.
PROGRESS = threading.local()


class Cancelled(BaseException):
    """Raised to stop a job. A BaseException, so code that catches Exception to carry on can't swallow it."""


def report_progress(label, done, total, extra=None):
    """extra: for copies, {"files", "files_total", "eta"} (eta in seconds, None until it can be guessed)."""
    listener = getattr(PROGRESS, "listener", None)
    if listener:
        listener(label, done, total, extra)


def human_time(seconds):
    """'about 40 seconds', 'about 12 minutes', 'about 2 hours 10 minutes'."""
    s = int(seconds)
    if s < 60:
        return f"about {max(5, round(s / 5) * 5)} seconds"
    m = round(s / 60)
    if m < 60:
        return f"about {m} minute{'s' if m != 1 else ''}"
    h, m = divmod(m, 60)
    return f"about {h} hour{'s' if h != 1 else ''}" + (f" {m} minute{'s' if m != 1 else ''}" if m else "")


@contextlib.contextmanager
def keep_awake():
    """Keep the computer from going to sleep while files copy: a sleeping laptop can drop its USB drive halfway
    through a long pack. The screen may still turn off. Does nothing where that isn't possible."""
    undo = None
    try:
        if rs.IS_WIN:
            import ctypes
            k32 = ctypes.windll.kernel32
            if k32.SetThreadExecutionState(0x80000000 | 0x00000001):   # ES_CONTINUOUS | ES_SYSTEM_REQUIRED
                undo = lambda: k32.SetThreadExecutionState(0x80000000)
        elif rs.IS_MAC:
            proc = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            def undo():
                proc.terminate()
                proc.wait(timeout=5)   # and collect it, so no finished process lingers
    except Exception:
        undo = None
    try:
        yield
    finally:
        if undo:
            try:
                undo()
            except Exception:
                pass


def staying_awake(fn):
    @functools.wraps(fn)
    def run(*args, **kwargs):
        with keep_awake():
            return fn(*args, **kwargs)
    return run


class Progress:
    """Bytes and files done, and a guess at the time left. The speed counts only files actually copied: files
    skipped because they're already in place take no time and would make the guess far too hopeful."""

    def __init__(self, total_bytes, label, total_files=None):
        self.total, self.done, self.label, self.last, self.start = total_bytes, 0, label, 0.0, time.time()
        self.files, self.total_files = 0, total_files
        self.copied, self.copy_time, self.prev = 0, 0.0, time.time()

    def eta(self):
        if self.copy_time < 3 or not self.copied:
            return None
        return max(0.0, (self.total - self.done) / (self.copied / self.copy_time))

    def add(self, n, files=1, copied=True):
        now = time.time()
        if copied and files:
            self.copied += n
            self.copy_time += now - self.prev
        self.prev = now
        self.done += n
        self.files += files
        report_progress(self.label, self.done, self.total,
                        {"files": self.files, "files_total": self.total_files, "eta": self.eta()})
        if now - self.last > 2 or self.done >= self.total:
            self.last = now
            pct = 100 * self.done / self.total if self.total else 100
            left = self.eta()
            print(f"  {self.label}: {pct:5.1f}%  {rs.human_size(self.done)} of {rs.human_size(self.total)}"
                  + (f", {self.files:,} of {self.total_files:,} files" if self.total_files else "")
                  + (f", {human_time(left)} left" if left is not None and self.done < self.total else ""),
                  flush=True)


COMPLETE = "complete.json"   # written last by pack; unpack checks for it (issue #59)
PACK_REPORTS = (("pack-errors.txt", "file couldn't be copied", "files couldn't be copied"),
                ("already-missing.txt", "sample was already missing on the old computer",
                 "samples were already missing on the old computer"),
                ("left-behind.txt", "program or installer was left behind on purpose",
                 "programs or installers were left behind on purpose"),
                ("projects-left-out.txt", "project stayed on the old computer (saved outside the time range)",
                 "projects stayed on the old computer (saved outside the time range)"),
                ("plugins-not-copied.txt", "plugin wasn't copied, for safety: install a fresh copy from its maker",
                 "plugins weren't copied, for safety: install fresh copies from their makers"))


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


def pack_items(includes, skips, missing=None, installed=None, needed=None, big=None, rng=None, offered=None,
               stats=None, libraries=None, plugins=None):
    """What a move packs. If `missing` is a dict, it is filled with {project: [samples it uses that are
    already missing on this computer]}; if `installed` is a set, with samples that come with installed programs;
    if `big` is a list, with plugin-data folders too big to pack unless asked for (probably sound libraries).
    With a time range (rng, issue #76), FL's browser folders (sample libraries) aren't packed whole: only the kits
    in-range projects use come, and the libraries go into `offered` (a list) so they can be ticked.
    Plugins that are only files come too (issue #80); `plugins`, if a dict, gets what comes and what doesn't."""
    items = []
    for rid, (label, path) in known_roots(THIS_OS, home()).items():
        if path.is_dir() and rid not in skips:
            items.append({"id": rid, "label": label, "source_path": str(path), "kind": "known"})
    libs = find_libraries()   # sound libraries (issue #71): offered, packed only when ticked or --include'd
    by_path = {_key(os.path.realpath(lib["path"])): lib for lib in libs}
    for inc in includes:
        p = Path(inc).expanduser().resolve()
        if not p.is_dir():
            sys.exit(f"Not a folder: {inc}")
        iid = item_id("extra", p)
        lib = by_path.get(_key(os.path.realpath(p)))
        if lib is not None:
            lib["item"] = iid
        if iid not in skips:
            items.append({"id": iid, "label": f"Sound library: {lib['name']}" if lib else p.name,
                          "source_path": str(p), "kind": "extra",
                          **({"library": {"name": lib["name"], "maker": lib["maker"]}} if lib else {})})
    if libraries is not None:
        libraries.extend(libs)
    fl = fl_settings()
    fold = THIS_OS == "windows"
    inside = lambda p, roots: any(norm(str(p), fold) == norm(str(r), fold) or
                                  norm(str(p), fold).startswith(norm(str(r), fold) + "/") for r in roots)
    libraries = []
    for sp in fl["search_paths"]:
        # FL's browser folders are the user's sample libraries: they move on purpose, used by a project or not
        sp = Path(sp)
        if not sp.is_dir() or in_program_place(sp) or inside(sp, [it["source_path"] for it in items]):
            continue
        if rng is not None:   # a time-range move takes only the kits it needs from them (issue #76)
            libraries.append(sp)
            if offered is not None:
                offered.append({"path": str(sp), "label": f"FL Studio browser folder: {sp.name}",
                                "bytes": folder_bytes(sp)})
            continue
        iid = item_id("search", sp)
        if iid not in skips:
            items.append({"id": iid, "label": f"FL Studio browser folder: {sp.name}", "source_path": str(sp),
                          "kind": "extra", "fl_search_path": True})
    # plugin presets and plugin data: "presets come with you" (issue #31)
    # every DAW folder counts as handled, skipped or not: leaving FL's folder out must keep it out
    daw_folders = [str(path) for _l, path in known_roots(THIS_OS, home()).values()]
    found, too_big = plugin_data_folders(home(), [it["source_path"] for it in items] + daw_folders)
    for folder, _size in found:
        iid = item_id("plugin", folder)
        if iid not in skips:
            items.append({"id": iid, "label": f"Plugin presets and data: {folder.name}", "source_path": str(folder),
                          "kind": "extra", "plugin_data": True})
    if big is not None:
        big.extend({"path": str(f), "label": f.name, "bytes": n} for f, n in too_big)
    # plugins that are only files: they go back in their plugin folders, no download needed (issue #80)
    items += plugin_items(skips, [it["source_path"] for it in items], plugins)
    left_out = [str(path) for rid, (_l, path) in known_roots(THIS_OS, home()).items() if rid in skips and path.is_dir()]
    recent = [Path(r) for r in fl["recent"] if r.lower().endswith(".flp") and Path(r).is_file()
              and not in_program_place(r)]
    folders, loose, gone = project_sample_folders(items, installed, left_out, needed, recent, rng, libraries, stats)
    if missing is not None:
        missing.update(gone)
    for folder, label in folders.items():
        iid = item_id("used", folder)
        if iid not in skips:
            items.append({"id": iid, "label": f"{label}: {folder.name}",
                          "source_path": str(folder), "kind": "extra", "kit": label == "Kit"})
    for folder, files in sorted(loose.items()):
        iid = item_id("loose", folder)
        if iid not in skips:
            items.append({"id": iid, "label": f"Loose files your projects use from {landing_name(folder)}",
                          "source_path": folder, "kind": "extra", "only": sorted(files)})
    return items


# --------------------------------------------------------------------------- a time range of projects (issue #76)

MONTH_NAMES = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
               "november", "december"]


def parse_month(text, end=False):
    """'2024-03', '2024-03-15', 'Mar 2024', 'March 2024' or '03/2024' -> (datetime, normalized text). The start of
    that month (or day), or with end=True its last moment. Raises ValueError with a message for people."""
    s = text.strip().lower().rstrip(".")
    day = None
    m = re.fullmatch(r"(\d{4})-(\d{1,2})(?:-(\d{1,2}))?", s)
    if m:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3)) if m.group(3) else None
    elif re.fullmatch(r"(\d{1,2})/(\d{4})", s):
        month, year = (int(x) for x in s.split("/"))
    else:
        m = re.fullmatch(r"([a-z]+)\.?,?\s*(\d{4})", s)
        names = [n for n in MONTH_NAMES if m and len(m.group(1)) >= 3 and n.startswith(m.group(1))]
        if not names:
            raise ValueError(f"\"{text.strip()}\" isn't a month Freighter understands. Try 2024-03 or Mar 2024.")
        month, year = MONTH_NAMES.index(names[0]) + 1, int(m.group(2))
    if not (1 <= month <= 12 and 1970 <= year <= 2100):
        raise ValueError(f"\"{text.strip()}\" isn't a real month.")
    try:
        start = dt.datetime(year, month, day or 1)
    except ValueError:
        raise ValueError(f"\"{text.strip()}\" isn't a real date.") from None
    norm_text = f"{year:04d}-{month:02d}" + (f"-{day:02d}" if day else "")
    if not end:
        return start, norm_text
    after = start + dt.timedelta(days=1) if day else dt.datetime(year + month // 12, month % 12 + 1, 1)
    return after - dt.timedelta(microseconds=1), norm_text


class ProjectRange:
    """Projects last saved from `since` to `until` (either may be open)."""

    def __init__(self, since=None, until=None):
        self.since, self.since_text = parse_month(since) if since else (None, None)
        self.until, self.until_text = parse_month(until, end=True) if until else (None, None)
        if not (self.since or self.until):
            raise ValueError("A time range needs a start, an end, or both.")
        if self.since and self.until and self.until < self.since:
            raise ValueError("The end of the time range comes before its start.")
        self._lo = self.since.timestamp() if self.since else float("-inf")
        self._hi = self.until.timestamp() if self.until else float("inf")

    def contains(self, mtime):
        return self._lo <= mtime <= self._hi

    def label(self):
        def show(d, text):
            return d.strftime("%-d %b %Y" if len(text) > 7 and os.name != "nt" else
                              "%#d %b %Y" if len(text) > 7 else "%b %Y")
        if self.since and self.until:
            return f"{show(self.since, self.since_text)} – {show(self.until, self.until_text)}"
        return f"since {show(self.since, self.since_text)}" if self.since else f"until {show(self.until, self.until_text)}"

    def as_dict(self):
        return {"since": self.since_text, "until": self.until_text, "label": self.label()}


def parse_range(text):
    """'2024-03 to 2026-03', 'Mar 2024 - Mar 2026', 'since 2024-03', 'until 2026-03' -> ProjectRange."""
    s = text.strip()
    m = re.fullmatch(r"(?i)(since|from|after)\s+(.+)", s)
    if m:
        return ProjectRange(m.group(2), None)
    m = re.fullmatch(r"(?i)(until|to|before|up to)\s+(.+)", s)
    if m:
        return ProjectRange(None, m.group(2))
    parts = re.split(r"(?i)\s+(?:to|-|until)\s+|\s*[–—]\s*", s, maxsplit=1)
    if len(parts) != 2:
        raise ValueError("Type a range like 2024-03 to 2026-03 (or since 2024-03, or until 2026-03).")
    return ProjectRange(parts[0] or None, parts[1] or None)


def add_range_args(parser):
    parser.add_argument("--since", metavar="MONTH", help="only projects last saved from this month on, e.g. 2024-03")
    parser.add_argument("--until", metavar="MONTH", help="only projects last saved up to this month, e.g. 2026-03")


def ask_range():
    """The menu's question: (since, until) as text, or (None, None) for all projects."""
    while True:
        text = input("  Which projects? Press Enter for all of them, or type a time range like  2024-03 to 2026-03 : ")
        if not text.strip():
            return None, None
        try:
            r = parse_range(text)
            print(f"  Projects saved {r.label()}.")
            return r.since_text, r.until_text
        except ValueError as e:
            print(f"  {e}")


def range_from_args(args):
    """The ProjectRange that --since/--until ask for, or None. Stops with a message when they're not months."""
    since, until = getattr(args, "since", None), getattr(args, "until", None)
    if not (since or until):
        return None
    try:
        return ProjectRange(since, until)
    except ValueError as e:
        sys.exit(str(e))


def project_in_range(p, rng):
    """Racks and clips are presets, and templates are templates: they always come. Other projects come when they
    were last saved in the range."""
    if rng is None:
        return True
    p = Path(p)
    if p.suffix.lower() in RACK_EXTS or "templates" in (x.lower() for x in p.parts[:-1]):
        return True
    try:
        return rng.contains(os.stat(long_path(p)).st_mtime)
    except OSError:
        return False


def range_keep(rng):
    """A walk_files filter for a time-range move: project files saved outside the range stay behind, and so do
    Ableton project folders whose sets are all outside it (with their audio)."""
    def keep(path, name, is_dir, st):
        low = name.lower()
        if is_dir:
            if not low.endswith(" project"):
                return True
            try:
                with os.scandir(path) as listing:
                    sets = [e for e in listing if e.name.lower().endswith(".als") and e.is_file()]
                return not sets or any(rng.contains(e.stat().st_mtime) for e in sets)
            except OSError:
                return True
        if not low.endswith((".flp", ".als")):
            return True
        if re.search(r"[\\/]templates[\\/]", path.lower()):
            return True
        return rng.contains(st.st_mtime)
    return keep


# Kits (issue #76): with a time range, a used sample brings the whole kit it belongs to, and nothing above it.
# A folder whose name is only sound-type words ("US_DG_One_Shots", "Kicks", "MIDI") is part of a kit; a folder
# with two or more subfolders that aren't is a container of kits (a library, a genre folder, a maker's folder).
KIT_PART_WORDS = (set().union(*(w for _c, w, _p in crate.ONE_SHOT_RULES), *(w for _c, w in crate.LOOP_RULES),
                              crate.LOOP_WORDS, crate.ONE_SHOT_WORDS)
                  | {"one", "shot", "shots", "loop", "loops", "drum", "drums", "hit", "hits", "open",
                     "closed", "construction", "midi", "preset", "presets", "patch", "patches", "format", "formats",
                     "ableton", "live", "fl", "flstudio", "logic", "serum", "vital", "massive", "wav", "wavs", "aif",
                     "aiff", "rex", "rx2", "sample", "samples", "sound", "sounds", "stem", "stems", "bonus", "extra",
                     "extras", "processed", "dry", "wet", "fill", "fills", "chop", "chops", "phrase", "phrases",
                     "melodies", "instrument", "instruments", "instrumental", "multisample", "multisamples", "slice",
                     "slices", "sliced", "bit", "khz", "demo", "demos", "misc", "various", "other", "full", "mix",
                     "song", "songs", "starter", "starters", "idea", "ideas", "progression", "progressions",
                     "tonal", "atonal", "textures", "one-shots", "oneshot", "oneshots", "hihats", "hi", "audio"})
KIT_CONTAINER_CACHE = {}
# a folder named with one of these is a kit (or a set of them), whatever else its name says: "Bass Kit", "Drum Kit 2"
KIT_WORDS = {"kit", "kits", "pack", "packs", "vol", "volume", "collection", "bundle", "edition", "library", "series"}


def _name_words(name):
    t = re.sub(r"([a-z])([A-Z])", r"\1 \2", name)
    t = re.sub(r"([A-Za-z])(\d)", r"\1 \2", t)
    t = re.sub(r"(\d)([A-Za-z])", r"\1 \2", t)
    return [w for w in re.split(r"[^a-z0-9]+", t.lower()) if w]


def _pack_code(word):
    """Pack codes like "US", "DG", "CNTRL" or "UNDRGRND": short, or no vowels after the first letter."""
    return len(word) <= 2 or word.isdigit() or not re.search(r"[aeiouy]", word[1:])


def kit_part(name):
    """True for a folder named only with sound-type words: a part of a kit, never a kit itself
    ("LC_CNTRL_Bass Loops", "US_DG_One_Shots", "Kicks"). Pack codes and numbers don't count either way, so
    "UNDRGRND Sounds - Deep Grooves - Wav" is a kit ("deep" is a real word)."""
    vocab, other = 0, 0
    for w in _name_words(name):
        if w in KIT_WORDS:
            return False
        if w in KIT_PART_WORDS or (w.endswith("s") and w[:-1] in KIT_PART_WORDS):
            vocab += 1
        elif not _pack_code(w):
            other += 1
    return vocab > 0 and other == 0


def is_kit_container(folder):
    """True for a folder above kits, not a kit itself: one holding two or more subfolders that aren't kit parts
    (a library, a genre or maker folder), or a wrapper whose only content is one such subfolder and no sounds of
    its own (Kits/House/<kits>: Kits only wraps House)."""
    key = _key(folder)
    if key not in KIT_CONTAINER_CACHE:
        kits, subfolders, sounds = 0, 0, 0
        try:
            with os.scandir(long_path(folder)) as listing:
                for e in listing:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            if e.name.startswith(".") or skip_dir(str(folder), e.name):
                                continue
                            subfolders += 1
                            kits += not kit_part(e.name)
                        elif os.path.splitext(e.name)[1].lower() in crate.AUDIO_EXTS:
                            sounds += 1
                    except OSError:
                        continue
        except OSError:
            pass
        KIT_CONTAINER_CACHE[key] = kits >= 2 or (subfolders == 1 and kits == 1 and not sounds)
    return KIT_CONTAINER_CACHE[key]


def kit_folder(sample, ceiling):
    """The kit a sample belongs to: walking down from ceiling (a library, or the top folder a move would take) to
    the sample's folder, the first folder that isn't a container of kits. None when the sample sits directly in
    such a container: then it comes on its own."""
    sample, ceiling = Path(sample), Path(ceiling)
    try:
        rel = sample.parent.relative_to(ceiling)
    except ValueError:
        return None
    chain = [ceiling] + [ceiling.joinpath(*rel.parts[:i + 1]) for i in range(len(rel.parts))]
    for d in chain:
        if not is_kit_container(d):
            return d
    return None


# --------------------------------------------------------------------------- sound libraries (issue #71)

# Big sample libraries that plugins play (Kontakt libraries, Omnisphere's STEAM folder, Spitfire, UVI, EastWest,
# Toontrack). They're found wherever they live, offered (unticked) with their size, and after the move each maker's
# own way to reconnect a moved library is shown. Found by what's on disk, not by guessing names:
#   a .nicnt file marks a Kontakt / Native Instruments library's top folder
#   a STEAM folder holding Omnisphere, Keyscape, Trilian or Stylus RMX is Spectrasonics' library
#   .ufs files are UVI soundbanks; .ewi files (in an Instruments folder) are EastWest libraries
#   a "Spitfire Audio" folder holds Spitfire libraries; Superior3 / EZdrummer folders hold Toontrack libraries
SPECTRASONICS = {"omnisphere", "keyscape", "trilian", "stylus rmx", "sage"}
LIBRARY_SCAN_LIMIT = 40000   # folder entries looked at per place, so a huge drive can't stall the move list


def hfs_path(value):
    """NI's Mac settings write 'Macintosh HD:Users:Shared:Lib:'. -> /Users/Shared/Lib, or /Volumes/SSD/Lib."""
    parts = [x for x in value.split(":") if x]
    if not parts:
        return None
    volume, rest = parts[0], parts[1:]
    on_volume = Path("/Volumes", volume, *rest)
    if on_volume.exists():
        return Path(os.path.realpath(on_volume))
    on_boot = Path("/", *rest)
    return on_boot if on_boot.exists() else on_volume


def ni_content_dirs():
    """{product: folder} from Native Instruments' own records: the registry on Windows, plists on a Mac."""
    found = {}
    if os.environ.get("FREIGHTER_HOME"):
        return found   # tests stand in for the home folder: never read this computer's real records
    if rs.IS_MAC:
        for plist in sorted(Path("/Library/Preferences").glob("com.native-instruments.*.plist")):
            try:
                import plistlib
                with open(plist, "rb") as f:
                    value = plistlib.load(f).get("ContentDir")
            except Exception:
                continue
            path = hfs_path(value) if isinstance(value, str) and ":" in value and "/" not in value else \
                (Path(value) if isinstance(value, str) else None)
            if path:
                found[plist.stem.replace("com.native-instruments.", "")] = path
    elif rs.IS_WIN:
        import winreg
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Native Instruments", 0,
                                      winreg.KEY_READ | view)
            except OSError:
                continue
            i = 0
            while True:
                try:
                    name = winreg.EnumKey(root, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg.OpenKey(root, name) as k:
                        value = winreg.QueryValueEx(k, "ContentDir")[0]
                    if isinstance(value, str) and value.strip():
                        found.setdefault(name, Path(value.strip().rstrip("\\")))
                except OSError:
                    continue
    return found


def library_at(folder, names):
    """(maker, kind) if folder is a sound library's top folder, judging by the names inside it."""
    low = {n.lower() for n in names}
    if any(n.endswith(".nicnt") for n in low):
        return "Native Instruments", "Kontakt library"
    if Path(folder).name.lower() == "steam" and low & SPECTRASONICS:
        return "Spectrasonics", "STEAM library"
    if Path(folder).name.lower() in ("superior3", "superior drummer 3", "ezdrummer", "ezdrummer 3", "ezdrummer3"):
        return "Toontrack", "drum library"
    if any(n.endswith(".ufs") for n in low):
        return "UVI", "soundbanks"
    return None


def find_libraries(h=None, ni=None):
    """Sound libraries on this computer: [{path, name, maker, kind}], biggest places first. Looks in the music
    folders, the shared folders and the top of other drives (a few levels down), plus where NI says its libraries
    are."""
    h = h or home()
    uf = user_folders(h)
    places = [(uf["Desktop"], 3), (uf["Documents"], 3), (uf["Music"], 3)]
    if not os.environ.get("FREIGHTER_HOME"):   # tests stand in for the home folder: shared places stay out
        if rs.IS_MAC:
            places += [(Path("/Users/Shared"), 3), (Path("/Library/Application Support/Spectrasonics"), 1)]
            vols = Path("/Volumes")
            for v in (sorted(vols.iterdir()) if vols.is_dir() else []):
                if v.is_dir() and not (v.is_symlink() and v.resolve() == Path("/")):
                    places.append((v, 3))
        elif rs.IS_WIN:
            public = os.environ.get("PUBLIC")
            if public:
                places.append((Path(public) / "Documents", 3))
            places.append((Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "Spectrasonics", 1))
            system = (os.environ.get("SystemDrive") or "C:")[:1].upper()
            import string
            for letter in string.ascii_uppercase:
                if letter != system and os.path.isdir(f"{letter}:\\"):
                    places.append((Path(f"{letter}:\\"), 3))
    found, seen = [], set()

    def add(folder, maker, kind):
        real = os.path.realpath(long_path(folder))
        if real in seen or any(real.startswith(s + os.sep) for s in seen):
            return
        seen.add(real)
        found.append({"path": str(folder), "name": Path(folder).name if Path(folder).name.lower() != "steam"
                      else "Spectrasonics STEAM folder", "maker": maker, "kind": kind})

    for product, folder in sorted((ni if ni is not None else ni_content_dirs()).items()):
        if folder.is_dir() and not in_program_place(folder):
            add(folder, "Native Instruments", "library")
    for place, depth in places:
        budget = [LIBRARY_SCAN_LIMIT]
        stack = [(place, 0)]
        while stack:
            cur, level = stack.pop()
            try:
                with os.scandir(long_path(cur)) as listing:
                    entries = list(listing)
            except OSError:
                continue
            budget[0] -= len(entries)
            names = [e.name for e in entries]
            hit = library_at(cur, names)
            if hit and cur != place:
                add(cur, *hit)
                continue
            if any(n.lower().endswith(".ewi") for n in names) and Path(cur).name.lower() == "instruments":
                add(Path(cur).parent, "EastWest", "library")
                continue
            if Path(cur).name.lower() == "spitfire audio" and cur != place:
                for e in entries:
                    if e.is_dir() and not e.name.startswith("."):
                        add(Path(e.path), "Spitfire Audio", "library")
                continue
            if level >= depth or budget[0] <= 0:
                continue
            for e in entries:
                try:
                    if e.is_dir(follow_symlinks=False) and not e.name.startswith(".") and not skip_dir(str(cur), e.name):
                        stack.append((Path(cur) / e.name, level + 1))
                except OSError:
                    continue
    return found


LIBRARY_STEPS = {
    # each maker's own way to reconnect a library that moved (from their support pages)
    "Native Instruments": ["Open Native Access and click the Refresh icon (top right).",
                           "Go to Library > All, find {name} and click Repair, then Repair again.",
                           "In the Locate window, click the folder symbol and choose {path}, then click Done."],
    "Spitfire Audio": ["Close your DAW, open the Spitfire Audio app and log in.",
                       "Click {name}'s artwork, open the cog menu and choose Repair (on a new computer) or Locate, "
                       "then choose {path}."],
    "Spectrasonics": ["Open Omnisphere (or Keyscape, Trilian or Stylus RMX) on its own or in your DAW.",
                      "When it asks where the STEAM folder is, choose {path}."],
    "Toontrack": ["In Superior Drummer 3, open Settings > Libraries/Paths and point it at {path}; in EZdrummer, "
                  "use Locate Folder and choose {path}."],
    "EastWest": ["Open EW Installation Center and set the library directory path to {path}."],
    "UVI": ["Open UVI Workstation or Falcon and add {path} to its soundbank folders."],
}


def library_steps(lib, path):
    steps = LIBRARY_STEPS.get(lib["maker"], ["Open {maker}'s app or plugin and point it at {path}."])
    return [s.format(name=lib["name"], path=path, maker=lib["maker"]) for s in steps]


def reinstall_step(lib):
    return {"Native Instruments": "Install it again from Native Access.",
            "Spitfire Audio": "Install it again from the Spitfire Audio app.",
            "Spectrasonics": "Install it again with Spectrasonics' installer (the STEAM folder).",
            "Toontrack": "Install it again from Toontrack's Product Manager.",
            "EastWest": "Install it again from EW Installation Center.",
            "UVI": "Install it again from UVI Portal."}.get(lib["maker"], f"Install it again from {lib['maker']}.")


# --------------------------------------------------------------------------- plugin presets and data (issue #31)

# Files only plugins make: a folder holding one is plugin presets or plugin data. (Kontakt's .nki is left out on
# purpose: it marks a sound library, which is issue #71's job.)
PRESET_EXTS = (".fxp", ".fxb", ".vstpreset", ".aupreset", ".nksf", ".nksfx", ".nmsv", ".h2p", ".vital",
               ".vitalbank", ".vitaltable", ".serumpreset", ".serumfx", ".ffp", ".phaseplant", ".prt_omn",
               ".mlt_omn", ".wcpreset")
PLUGIN_DATA_BIG = 10 * 1024 ** 3   # bigger is probably a sound library: offered, not packed unless asked for
MAKER_WORDS_IGNORED = {"audio", "music", "sound", "sounds", "samples", "plugin", "plugins", "presets", "studio",
                       "studios", "records", "software", "digital", "labs", "the", "inc", "ltd", "gmbh", "llc"}


def _plain(name):
    return re.sub(r"[^a-z0-9]", "", name.lower())


def plugin_makers():
    """Names a plugin-data folder may have, from the plugins installed here: each maker's name, and its first word
    when that's distinctive ("Xfer Records" -> xferrecords, xfer). Empty if the scan fails, and when FREIGHTER_HOME
    stands in for the home folder (tests), like fl_settings()."""
    if os.environ.get("FREIGHTER_HOME"):
        return set()
    rows = installed_plugins()
    names = set()
    for r in rows:
        vendor = (r.get("vendor") or "").strip()
        if not vendor or vendor.lower().startswith("unknown"):
            continue
        names.add(_plain(vendor))
        first = _plain(vendor.split()[0])
        if len(first) >= 4 and first not in MAKER_WORDS_IGNORED:
            names.add(first)
    return {n for n in names if len(n) >= 3}


def system_preset_places():
    """Preset folders shared by everyone on this computer. Left out when FREIGHTER_HOME stands in for the home
    folder (tests), so a test never picks up this computer's real presets."""
    if os.environ.get("FREIGHTER_HOME"):
        return []
    if THIS_OS == "mac":
        return [Path("/Library/Audio/Presets")]
    public = os.environ.get("PUBLIC")
    return [Path(public) / "Documents"] if public else []


def has_preset_files(folder, max_entries=5000, max_depth=5):
    """Whether a preset file sits in folder (looking at no more than max_entries entries, max_depth levels down)."""
    seen, stack = 0, [(long_path(folder), 0)]
    while stack:
        cur, depth = stack.pop()
        try:
            with os.scandir(cur) as listing:
                for e in listing:
                    seen += 1
                    if seen > max_entries:
                        return False
                    try:
                        if e.is_dir(follow_symlinks=False):
                            if depth < max_depth and not e.name.startswith("."):
                                stack.append((e.path, depth + 1))
                        elif e.name.lower().endswith(PRESET_EXTS):
                            return True
                    except OSError:
                        continue
        except OSError:
            continue
    return False


def folder_bytes(folder, stop_at=None):
    """Total size of the files in folder; stops counting once past stop_at."""
    total = 0
    for _rel, _ap, size in walk_files(folder):
        total += size
        if stop_at is not None and total > stop_at:
            break
    return total


def plugin_data_folders(h, covered, makers=None):
    """Plugin presets and plugin data to move (issue #31): folders in Documents and Music (and the Mac's Audio
    Presets folders, or Windows' Public Documents) named after an installed plugin's maker, or holding preset
    files. Returns ([(folder, bytes)], [(folder, bytes)] too big to pack without asking)."""
    makers = plugin_makers() if makers is None else makers
    uf = user_folders(h)
    places = [uf["Documents"], uf["Music"]]
    if THIS_OS == "mac":
        places.append(h / "Library" / "Audio" / "Presets")
    places += system_preset_places()
    # compare real paths too: folders added with --include are resolved (/var is /private/var on a Mac)
    cov = Folders(list(covered) + [os.path.realpath(c) for c in covered])
    found, big, seen = [], [], set()
    for place in places:
        try:
            entries = sorted(os.scandir(long_path(place)), key=lambda e: e.name.lower())
        except OSError:
            continue
        for e in entries:
            try:
                if not e.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            d = place / e.name
            key = _key(d)
            if key in seen or e.name.startswith(".") or e.name.lower().startswith("freighter") \
                    or skip_dir(str(place), e.name) or in_program_place(d) or cov.holds(d) or cov.has_inside(d) \
                    or cov.holds(os.path.realpath(d)):
                continue
            seen.add(key)
            if _plain(e.name) not in makers and not has_preset_files(d):
                continue
            size = folder_bytes(d, stop_at=PLUGIN_DATA_BIG)
            if size > PLUGIN_DATA_BIG:
                big.append((d, folder_bytes(d)))   # its real size, for the choice
            elif size:
                found.append((d, size))
    return found, big


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
    ProgramData, the Mac's system folders, or Live's Factory Packs folder. They come back when the program is installed, so Freighter never
    packs them as samples (issue #60: a Core Library reference used to pack the Live app itself)."""
    s = str(path).replace("\\", "/")
    parts = [x.lower() for x in s.split("/") if x]
    if any(x.endswith(PROGRAM_BUNDLES) for x in parts[:-1]):
        return True
    if len(parts) > 1 and re.fullmatch(r"[a-z]:", parts[0]) and parts[1] in WIN_PROGRAM_DIRS:
        return True
    if "factory packs" in parts[:-1]:
        return True   # Live's installed Packs: they reinstall from Ableton, like the Core Library
    return (s.lower() + ("/" if not s.endswith("/") else "")).startswith(MAC_PROGRAM_DIRS)


def project_sample_folders(items, installed=None, excluded=(), needed=None, extra_projects=(), rng=None,
                           libraries=(), stats=None):
    """What a move needs beyond the packed items: projects found in Desktop, Documents and Music (even ones
    that use no outside samples), and every sample folder FL/Ableton projects use.
    With a time range (rng, issue #76), only projects last saved in it count, and a sample brings the kit it
    belongs to (never the library or genre folder above it); libraries are FL's browser folders left out.
    stats, if a dict, gets {total, in_range, left_out, years} for the projects found.
    Returns ({folder: label}, {folder: set of file names to take loose},
             {project path: [samples it uses that are already missing]})."""
    h = home()
    # excluded: DAW folders the user chose to leave out (--skip). They count as handled, so nothing re-adds them.
    covered = [Path(it["source_path"]) for it in items] + [Path(e) for e in excluded]
    roots = all_user_folders(h) + [c for c in covered if c.is_dir()]
    inside_left_out = Folders(excluded).holds
    both = [p for p in find_projects(roots, (".flp",) + ABLETON_EXTS, include_backups=True)   # one walk (issue #74)
            if not in_program_place(p)]   # e.g. demo sets in Live's installed Packs: they come with Live
    flps = [p for p in both if p.suffix.lower() == ".flp" and not inside_left_out(p)]
    # recent projects FL knows about, wherever they are (e.g. D:\\Projects), are found too
    flps += [p for p in extra_projects if p not in flps and not inside_left_out(p)]
    alss = [p for p in both if p.suffix.lower() in ABLETON_EXTS and p.parent.name.lower() != "backup"
            and not inside_left_out(p)]
    # demo projects inside sample libraries (FL's browser folders) are the packs' own, not yours
    in_library = Folders(list(libraries)).holds if libraries else (lambda p: False)
    if rng is not None:
        flps = [p for p in flps if not in_library(p)]
        alss = [p for p in alss if not in_library(p)]
    if stats is not None:
        real = [p for p in flps + alss if p.suffix.lower() in (".flp", ".als") and p.parent.name.lower() != "backup"
                and "templates" not in (x.lower() for x in p.parts[:-1])]   # people's projects, not templates
        saved = {}
        for p in real:
            try:
                saved[p] = os.stat(long_path(p)).st_mtime
            except OSError:
                pass
        inside = [p for p in real if project_in_range(p, rng)]
        years = sorted(dt.datetime.fromtimestamp(t).year for t in saved.values())
        stats.update(total=len(real), in_range=len(inside), years=[years[0], years[-1]] if years else [],
                     left_out=sorted((str(p) for p in real if p not in set(inside)), key=str.lower))
    if rng is not None:   # projects saved outside the range stay behind, and so do the samples only they use
        flps = [p for p in flps if project_in_range(p, rng)]
        alss = [p for p in alss if project_in_range(p, rng)]
    KIT_CONTAINER_CACHE.clear()
    libs = sorted((Path(x) for x in libraries), key=lambda x: -len(str(x)))
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

    cov = Folders(covered)
    inside_covered = cov.holds

    def take(path, label):
        """Add the folder that should carry this file, or the file alone when it sits loose."""
        f = folder_to_move(path, h, bases, cov)
        if rng is not None:
            # a time-range move takes the kit the sample belongs to, under its library or top folder (issue #76)
            ceiling = next((lib for lib in libs if Folders([lib]).holds(path)), None) or f
            kit = kit_folder(path, ceiling) if ceiling is not None else None
            if kit is None or kit == h:
                loose.setdefault(str(path.parent), set()).add(path.name)
            elif kit.is_dir():
                folders.setdefault(kit, "Kit")
            return
        if f is None:
            loose.setdefault(str(path.parent), set()).add(path.name)
        elif f.is_dir() and f != h:
            if folders.get(f, label) != label:
                label = "Samples and projects"   # skipping this folder would lose samples too
            folders[f] = label

    def add_folder(f, label):
        """Add folder f itself (not its top-level parent), unless it's a base folder or would re-pack a packed item."""
        if f is None or f == h or f in bases or f.parent == f or cov.has_inside(f):
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
        top = folder_to_move(p, h, bases, cov)
        if used and top is not None and all(folder_to_move(r, h, bases, cov) == top for r in used):
            anchor = Path(os.path.commonpath([str(own)] + [str(r.parent) for r in used]))
            if add_folder(anchor, "A project and the samples it uses"):
                handled.add(p)
                continue
        if not add_folder(own, "Ableton racks and clips" if p.suffix.lower() in RACK_EXTS else "Projects"):
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
    chosen = Folders(folders)
    keep = {f: label for f, label in folders.items() if not chosen.holds(f.parent)}   # drop folders inside others
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


def _key(p):
    return os.path.normcase(str(p))   # how the OS compares paths: case-insensitively on Windows


def _ancestors(path):
    """The keys of every folder above path."""
    out, k = set(), _key(path)
    parent = os.path.dirname(k)
    while parent != k:
        out.add(parent)
        k, parent = parent, os.path.dirname(parent)
    return out


class Folders:
    """A set of folders that answers "is this path one of them, or inside one?" and "is one of them inside this
    folder?" in time that grows with the path's depth, not with how many folders there are: planning a move asks
    tens of thousands of times (issue #74)."""

    def __init__(self, folders):
        self.keys = {_key(f) for f in folders}
        self.above = set()   # every folder that has one of them somewhere inside it
        for k in self.keys:
            self.above |= _ancestors(k)

    def holds(self, path):
        k = _key(path)
        while True:
            if k in self.keys:
                return True
            parent = os.path.dirname(k)
            if parent == k:
                return False
            k = parent

    def has_inside(self, folder):
        return _key(folder) in self.above


def folder_to_move(path, h, bases, covered):
    """The folder to move so that `path` (a sample or project) comes along, or None to take the file alone.
    Starts at the top folder inside Desktop, Documents or Music (wherever they really are), or uses
    sample_folder_for elsewhere, then steps down while that folder would also re-pack a packed item
    such as the Ableton User Library (issue #9)."""
    covered = covered if isinstance(covered, Folders) else Folders(covered)
    above = _ancestors(path)
    base = next((u for u in bases if _key(u) in above), None)
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
    while covered.has_inside(f):
        rest = path.relative_to(f).parts
        if len(rest) == 1:
            return None
        f = f / rest[0]
    return f


def measure_item(it, left_behind, keep=None):
    if "only" in it:
        files = []
        for name in it["only"]:
            ap = Path(it["source_path"]) / name
            size = file_size(ap)
            if size is not None:
                files.append((name, ap, size))
    else:
        # DAW user-data folders move whole; sample folders leave programs and installers behind
        # a kit comes complete, demo projects and all: the time range is about your projects (issue #76)
        # copied plugins (issue #80) are programs on purpose, and move whole too
        whole = it["kind"] == "known" or it.get("plugin_files")
        files = list(walk_files(it["source_path"], None if whole else left_behind,
                                None if it.get("kit") or it.get("plugin_files") else keep))
    it["_files"] = files
    it["files"] = len(files)
    it["bytes"] = sum(f[2] for f in files)


def measure_items(items, include_programs=False, keep=None):
    """List every file each item would pack (it["_files"], it["files"], it["bytes"]). Fills CLOUD_ONLY.
    Returns (total bytes, programs left behind, or None when they're kept)."""
    del CLOUD_ONLY[:]
    left_behind = None if include_programs else []
    for i, it in enumerate(items):
        report_progress(f"Measuring {it['label']}", i, len(items))
        measure_item(it, left_behind, keep)
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


def plan_move(includes=(), skips=(), include_programs=False, rng=None):
    """Everything a pack (or the app's move list) needs to know, decided once. Returns a dict with items (measured),
    total, left_behind, missing, installed, cloud_only and stragglers (files the safety net added)."""
    missing, installed, needed, big, offered, stats, libs, plugins = {}, set(), set(), [], [], {}, [], {}
    items = pack_items(list(includes), set(skips), missing, installed, needed, big, rng, offered, stats, libs,
                       plugins)
    # a sound library never comes inside another folder that's packed (issue #71): it's offered, and when it's
    # ticked it comes as its own item (whose walk starts at the library itself, so this doesn't stop it)
    left_libs = {_key(os.path.realpath(lib["path"])) for lib in libs}
    date_keep = range_keep(rng) if rng is not None else None

    def keep(path, name, is_dir, st):
        if is_dir and left_libs and _key(os.path.realpath(short_path(path))) in left_libs:
            return False
        return date_keep is None or date_keep(path, name, is_dir, st)
    total, left_behind = measure_items(items, include_programs, keep) if items else (0, None)
    added = cover_stragglers(items, needed) if items else 0
    items = [it for it in items if it["files"]]   # e.g. a DAW folder that only held a library left out
    for lib in libs:
        lib["bytes"] = folder_bytes(lib["path"]) if "item" not in lib else \
            next((it["bytes"] for it in items if it["id"] == lib["item"]), 0)
    lib_keys = [Path(os.path.realpath(lib["path"])) for lib in libs]
    big = [b for b in big if not any(k == Path(os.path.realpath(b["path"])) or Path(os.path.realpath(b["path"])) in k.parents
                                     for k in lib_keys)]   # shown as libraries instead, with how to reconnect them
    return {"items": items, "total": sum(it["bytes"] for it in items), "left_behind": left_behind,
            "missing": missing, "installed": installed, "cloud_only": list(CLOUD_ONLY), "stragglers": added,
            "big_plugin_data": big, "offered_libraries": offered, "projects": stats,
            "libraries": [dict(lib, moved="item" in lib) for lib in libs],
            "plugin_files": {"copied": [p for it in items if it.get("plugin_files") for p in it["plugins"]],
                             "install": [p for p in plugins.get("install", []) if not p["safety"]],
                             "safety": [p for p in plugins.get("install", []) if p["safety"]]},
            "range": rng.as_dict() if rng is not None else None}


def say_range(plan):
    st = plan["projects"]
    print(f"\nProjects saved {plan['range']['label']}: {st['in_range']} of {st['total']}. The others stay on this "
          "computer. Each sample they use brings its whole kit, never the folder of kits above it.")


def say_sound_libraries(libs):
    left = [lib for lib in libs if not lib.get("moved")]
    if left:
        print("\nSound libraries found, left out unless you add them (they can also be installed again from the "
              "maker's app):")
        for lib in left:
            print(f"  {rs.human_size(lib['bytes'])}  {lib['name']} ({lib['maker']})  {lib['path']}")
        print("Take one with --include FOLDER.")


def say_offered_libraries(offered):
    if offered:
        print("\nSample libraries left out (only the kits your projects use come from them):")
        for o in offered:
            print(f"  {rs.human_size(o['bytes'])}  {o['path']}")
        print("Take a whole one with --include FOLDER.")


def say_big_plugin_data(big):
    if big:
        print("\nNot packed, because they're big (probably sound libraries that reinstall from the maker's app):")
        for b in big:
            print(f"  {rs.human_size(b['bytes'])}+  {b['path']}")
        print("Take one anyway with --include FOLDER.")


def say_plugin_files(pf):
    if pf["copied"]:
        names = sorted({f"{p['name']} ({p['format']})" for p in pf["copied"]}, key=str.lower)
        print(f"\nPlugins that come along, so the new computer needs no download for them ({len(names)}): "
              + ", ".join(names))
    if pf["install"]:
        print(f"{len(pf['install'])} other plugin{'s' if len(pf['install']) != 1 else ''} install from their maker on "
              "the new computer (their app sets up licenses and sounds): " +
              ", ".join(sorted({p["name"] for p in pf["install"]}, key=str.lower)))
    if pf.get("safety"):
        print("Not copied, for safety (install a fresh copy from the maker): " +
              "; ".join(f"{p['name']}: {safety_reason(p['why'])}" for p in pf["safety"]))


def scan_plugin_items(items, clean=False):
    """Scan plugin items with this computer's virus scanner (issue #81). Returns ([{id, label, names, threat}]
    for the flagged items, a line for people, or "" when there was nothing to scan)."""
    if not items:
        return [], ""
    paths = {}
    for it in items:
        files = [str(Path(it["source_path"]) / n) for n in it["only"]] if "only" in it else [it["source_path"]]
        for f in files:
            paths[f] = it
    if rs.find_virus_scanner()[0] is None and THIS_OS == "mac":
        return [], ("macOS checks each copied plugin Apple hasn't checked the first time your DAW loads it." if clean
                    else "Plugins: a Mac has no virus scan to run now. On the new Mac, macOS checks each plugin Apple "
                    "hasn't checked the first time your DAW loads it.")
    print("\nChecking the copied plugins with this computer's virus scanner..." if clean else
          "\nChecking the plugins that come along with your virus scanner...", flush=True)
    r = rs.scan_files(list(paths), clean=clean)
    fold = THIS_OS == "windows"
    flagged = {}
    for hit, threat in r["hits"].items():
        h = norm(hit, fold)
        it = next((it for f, it in paths.items() if h == norm(f, fold) or h.startswith(norm(f, fold) + "/")), None)
        if it is not None and it["id"] not in flagged:
            flagged[it["id"]] = {"id": it["id"], "label": it["label"], "threat": threat,
                                 "names": [f"{x['name']} ({x['format']})" for x in it.get("plugins", [])]}
    if r["scanner"]:
        note = f"Plugins checked by {r['scanner']}: {len(flagged)} flagged" + \
            (f", {len(r['errors'])} couldn't be checked" + ("" if clean else " (the new computer checks them again)")
             if r["errors"] else "") + "."
    else:
        note = r["note"]
    return list(flagged.values()), note


def safety_reason(why):
    """The short, calm reason a plugin wasn't copied for safety (issue #81)."""
    if why.startswith("Changed after"):
        return "its files changed after its maker signed them"
    m = re.search(r"\((.+?)\)", why)
    return f"your virus scanner flagged it ({m.group(1)})" if m else "your virus scanner flagged it"


def say_installed(installed):
    if installed:
        n = len(installed)
        print(f"\n{n} sample{'s' if n != 1 else ''} your projects use come{'s' if n == 1 else ''} with installed programs or "
              "plugins (for example Ableton's Core Library or FL Studio's own sounds). They aren't moved: installing "
              "those programs on the new computer brings them back.")


@staying_awake
def cmd_pack(args):
    rng = range_from_args(args)
    print("Looking through your music folders and projects...", flush=True)
    plan = plan_move(args.include, args.skip, getattr(args, "include_programs", False), rng)
    items, missing, installed = plan["items"], plan["missing"], plan["installed"]
    if not items:
        msg = "Nothing to pack: no standard music folders found. Add folders with --include FOLDER."
        if args.list or not args.to:
            print(msg)
            return 0
        sys.exit(msg)

    total, left_behind = plan["total"], plan["left_behind"]
    if rng is not None:
        say_range(plan)
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
        say_big_plugin_data(plan["big_plugin_data"])
        say_plugin_files(plan["plugin_files"])
        say_sound_libraries(plan["libraries"])
        say_offered_libraries(plan["offered_libraries"])
        say_missing(missing)
        say_installed(installed)
        if not args.to:
            return 0
        if args.list:
            return 0

    # checkpoint 2 (issue #81): the plugins that come along are scanned before they're copied
    flagged, scan_note = scan_plugin_items([it for it in items if it.get("plugin_files")])
    if flagged:
        gone = {f["id"] for f in flagged}
        items = [it for it in items if it["id"] not in gone]
        total = sum(it["bytes"] for it in items)
    if scan_note:
        print("\n" + scan_note)
    for f in flagged:
        print(f"Not copied, for safety: {', '.join(f['names'])} (your virus scanner flagged it: {f['threat']}). "
              "Install a fresh copy from its maker on the new computer.")

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

    for f in flagged:   # an earlier, interrupted pack may have copied it already
        shutil.rmtree(bundle / "data" / f["id"], ignore_errors=True)
        (bundle / "index" / f"{f['id']}.jsonl").unlink(missing_ok=True)
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
        "user_folders": {name: str(path) for name, path in user_folders(home()).items()},
        "fl_search_paths": fl_settings()["search_paths"],
        "projects_range": dict(plan["range"], in_range=plan["projects"]["in_range"],
                               total=plan["projects"]["total"]) if rng is not None else None,
        "libraries": [{k: lib.get(k) for k in ("name", "maker", "kind", "path", "bytes", "item")}
                      for lib in plan["libraries"]],
        "items": [{k: v for k, v in it.items() if not k.startswith("_")} for it in items],
    }
    (bundle / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    (bundle / COMPLETE).unlink(missing_ok=True)   # changing it: not finished until the end
    prog = Progress(total, "Copying", sum(len(it["_files"]) for it in items))
    prog.add(0, files=0)
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
                    st = os.stat(long_path(ap))
                    mtime = int(st.st_mtime)
                except OSError:
                    st, mtime = None, None
                # a Mac plugin's program file must stay runnable (issue #80)
                runnable = bool(it.get("plugin_files") and THIS_OS != "windows" and st and st.st_mode & 0o111)
                # skip what's already there, unless the original changed since (same size isn't enough: a
                # re-rendered sample often keeps its length)
                if rec and rec["size"] == size and file_size(dst) == size and rec.get("mtime") in (None, mtime):
                    prog.add(size, copied=False)
                    continue
                try:
                    digest = copy_and_hash(ap, dst, direct=True)
                except OSError as e:
                    errors.append(f"{ap}: {e}")
                    prog.add(size, copied=False)
                    continue
                index.write(json.dumps({"rel": rel, "stored": stored, "size": size, "sha256": digest,
                                        "mtime": mtime, **({"x": True} if runnable else {})}) + "\n")
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
    unsafe = [f"{', '.join(f['names'])}: your virus scanner flagged it ({f['threat']})" for f in flagged] + \
        [f"{x['name']} ({x['format']}): {safety_reason(x['why'])}" for x in plan["plugin_files"]["safety"]]
    if unsafe:
        (bundle / "plugins-not-copied.txt").write_text(
            f"Freighter {__version__}\n\nPlugins on {host} that weren't copied, for safety. Install a fresh copy of "
            "each from its maker on the new computer.\n\n" + "\n".join(unsafe), encoding="utf-8")
    if rng is not None and plan["projects"]["left_out"]:
        (bundle / "projects-left-out.txt").write_text(
            f"Freighter {__version__}\n\nProjects on {host} last saved outside {plan['range']['label']}, so they "
            "weren't moved. They're still on the old computer.\n\n" + "\n".join(plan["projects"]["left_out"]),
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
    LAST["pack"] = {"bundle": str(bundle), "items": len(items), "files": sum(it["files"] for it in items),
                    "plugins_copied": sum(len(it["plugins"]) for it in items if it.get("plugin_files")),
                    "plugins_not_copied": unsafe, "plugin_scan": scan_note,
                    "bytes": total, "errors": len(errors), "left_behind": len(left_behind or []),
                    "missing": len(missing_lines(missing)),
                    "range": plan["range"]["label"] if rng is not None else None,
                    "projects": {k: v for k, v in plan["projects"].items() if k != "left_out"}}
    print("Next, on the new computer:  " + tool_cmd("freighter", f"unpack \"{bundle}\""))
    return 0


# --------------------------------------------------------------------------- check the drive (issue #77)

def bundle_for(path):
    """A bundle folder, or the newest bundle on a drive or folder. None if there isn't one."""
    p = Path(path).expanduser()
    if (p / "manifest.json").is_file():
        return p
    found = find_bundles(p)
    return max(found, key=lambda b: b.stat().st_mtime) if found else None


def verify_bundle(bundle, repair=True):
    """Read every file on the drive back and check it against the checksum taken when it was copied. Damaged or
    missing copies are deleted (they're Freighter's copies, never originals) and the bundle is marked unfinished,
    so packing again re-copies just those. Returns a summary; also written to drive-check.txt on the drive."""
    bundle = Path(bundle)
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    records = [(it, rec) for it in manifest["items"]
               for rec in read_jsonl(bundle / "index" / f"{it['id']}.jsonl").values()]
    prog = Progress(sum(rec["size"] for _it, rec in records), "Checking", len(records))
    prog.add(0, files=0)
    bad = []
    for it, rec in records:
        p = bundle / "data" / it["id"] / rec["stored"]
        try:
            ok = file_size(p) == rec["size"] and hash_file(p, from_drive=True) == rec["sha256"]
            problem = None if ok else ("missing" if file_size(p) is None else "damaged")
        except OSError as e:
            problem = f"unreadable: {e.strerror or e}"
        if problem:
            bad.append({"file": rec["rel"], "item": it["label"], "problem": problem, "path": str(p)})
        prog.add(rec["size"])
    if bad and repair:
        for b in bad:
            try:
                Path(long_path(b["path"])).unlink(missing_ok=True)
            except OSError:
                pass
        (bundle / COMPLETE).unlink(missing_ok=True)   # not finished until those are copied again
    lines = [f"Freighter {__version__} drive check, {dt.datetime.now():%Y-%m-%d %H:%M}",
             f"{len(records) - len(bad)} of {len(records)} files read back and match the originals."]
    if bad:
        lines += ["", "These copies were damaged or missing. They were removed from the drive: pack again and "
                  "Freighter copies just these again.", ""] + [f"{b['item']}: {b['file']} ({b['problem']})" for b in bad]
    report = bundle / "drive-check.txt"
    try:
        report.write_text("\n".join(lines), encoding="utf-8")
    except OSError:
        report = None
    out = {"bundle": str(bundle), "files": len(records), "bytes": prog.total, "bad": bad[:200],
           "bad_count": len(bad), "report": str(report) if report else None}
    LAST["verify"] = out
    return out


def cmd_verify(args):
    bundle = bundle_for(args.bundle)
    if bundle is None:
        sys.exit(f"No Freighter bundle in {args.bundle}.")
    print(f"Checking {bundle}: reading every file back from the drive...")
    print("(For the best check, unplug the drive and plug it back in first, so nothing comes from memory.)", flush=True)
    r = verify_bundle(bundle)
    if r["bad_count"]:
        print(f"\n{r['bad_count']} of {r['files']} copies on the drive were damaged or missing:")
        for b in r["bad"][:10]:
            print(f"  {b['item']}: {b['file']} ({b['problem']})")
        print("They were removed. Pack again onto this drive and Freighter copies just those again; then check again.")
        print("If it keeps happening, try another USB port or cable, or another drive.")
        return 2
    print(f"\nThe drive is good: all {r['files']:,} files read back and match the originals.")
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


UNPACK_JOURNAL = "unpack-journal.jsonl"
REPAIR_JOURNAL = "repair-journal.jsonl"


def read_journal(path):
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass     # a line cut off by an interruption
    except OSError:
        pass
    return out


def missing_folders(folder):
    """The folders that don't exist yet on the way to `folder`, outermost first (they're about to be created)."""
    out, f = [], Path(folder)
    while not os.path.exists(long_path(f)) and f.parent != f:
        out.append(f)
        f = f.parent
    return list(reversed(out))


def journal_repair(project, backup, made_backup):
    """Record a project a repair rewrote, so undo can put the original back if nobody changed it since."""
    j = home() / "Music" / "Freighter" / REPAIR_JOURNAL
    j.parent.mkdir(parents=True, exist_ok=True)
    with open(j, "a", encoding="utf-8") as f:
        f.write(json.dumps({"project": str(project), "sha256": hash_file(project), "backup": str(backup),
                            "made_backup": made_backup}) + "\n")


def earlier_unpacks():
    """Unpacks on this computer that can still be undone, newest first: [{host, when, files}]."""
    base = home() / "Music" / "Freighter"
    out = []
    for d in (sorted(base.iterdir()) if base.is_dir() else []):
        j = d / UNPACK_JOURNAL
        if d.is_dir() and j.is_file():
            out.append({"host": d.name, "files": sum(1 for e in read_journal(j) if e.get("kind") == "file"),
                        "when": dt.datetime.fromtimestamp(j.stat().st_mtime).isoformat(timespec="seconds")})
    return sorted(out, key=lambda u: u["when"], reverse=True)


def undo_unpack(host=None):
    """Undo unpacks (all, or the one from `host`): remove what they created and put repaired projects back.
    Anything changed since is kept and listed. Returns a summary."""
    base = home() / "Music" / "Freighter"
    landings = [d for d in sorted(base.iterdir()) if d.is_dir() and (d / UNPACK_JOURNAL).is_file()] \
        if base.is_dir() else []
    if host:
        landings = [d for d in landings if d.name == host]
    fold = THIS_OS == "windows"
    repairs = {norm(e["project"], fold): e for e in read_journal(base / REPAIR_JOURNAL)}
    done_repairs, made_dirs, need_admin = set(), [], []
    out = {"hosts": [d.name for d in landings], "removed": 0, "restored": 0, "kept": [], "need_admin": []}

    def current(p):
        try:
            return hash_file(p)
        except OSError:
            return None

    for landing in landings:
        entries = read_journal(landing / UNPACK_JOURNAL)
        for e in reversed([e for e in entries if e.get("kind") == "file"]):
            p, key = Path(e["path"]), norm(e["path"], fold)
            if not os.path.exists(long_path(p)):
                continue
            now, rep = current(p), repairs.get(key)
            if now == e["sha256"] or (rep and now == rep["sha256"]):
                try:
                    os.remove(long_path(p))
                except PermissionError:   # a plugin put in Program Files (issue #80)
                    need_admin.append((landing, e))
                    continue
                out["removed"] += 1
                if rep:
                    done_repairs.add(key)
            else:
                out["kept"].append(str(p))   # changed since the unpack: it's someone's work now
        for name in (UNPACK_JOURNAL, "pathmap.json", "unpack-report.txt", "pack-errors.txt", "already-missing.txt",
                     "left-behind.txt", "projects-left-out.txt", "plugins-copied.txt", "plugins-copied.json",
                     "plugins-not-copied.txt"):
            (landing / name).unlink(missing_ok=True)
        for f in landing.glob("plugins-*.csv"):
            f.unlink()
        made_dirs += [e["path"] for e in entries if e.get("kind") == "dir"] + [str(landing)]
    # projects a repair rewrote that the unpack didn't create (they were already here): put the original back
    for key, rep in repairs.items():
        if key in done_repairs:
            if rep.get("made_backup"):
                Path(rep["backup"]).unlink(missing_ok=True)
            continue
        p, b = Path(rep["project"]), Path(rep["backup"])
        if p.is_file() and b.is_file() and current(p) == rep["sha256"]:
            shutil.copy2(b, p)
            if rep.get("made_backup"):
                b.unlink()
            out["restored"] += 1
            done_repairs.add(key)
        elif p.is_file():
            out["kept"].append(str(p))
    for key in done_repairs:
        try:
            Path(repairs[key]["backup"]).parent.rmdir()   # the Freighter backup folder, if now empty
        except OSError:
            pass
    left = [e for k, e in repairs.items() if k not in done_repairs]
    if not left:   # the repair reports describe what was just undone
        for name in ("fl-repair-report.txt", "ableton-fix-report.txt", "fl-check-report.txt"):
            (base / name).unlink(missing_ok=True)
    if left:
        (base / REPAIR_JOURNAL).write_text("".join(json.dumps(e) + "\n" for e in left), encoding="utf-8")
    else:
        (base / REPAIR_JOURNAL).unlink(missing_ok=True)
    if need_admin and THIS_OS == "windows":   # plugins in Program Files: Windows asks for permission once
        print(f"Removing {len(need_admin)} plugin files from Program Files. Windows asks for permission: choose Yes.",
              flush=True)
        as_admin({"remove": [[e["path"], e["sha256"]] for _l, e in need_admin],
                  "folders": [d for d in set(made_dirs) if os.path.isdir(long_path(d))]}, base)
    left = {}
    for landing, e in need_admin:
        if os.path.exists(long_path(e["path"])):
            out["need_admin"].append(e["path"])
            left.setdefault(landing, []).append(e)
        else:
            out["removed"] += 1
    for landing, kept in left.items():   # so undoing again still knows them
        (landing / UNPACK_JOURNAL).write_text("".join(json.dumps(e) + "\n" for e in kept), encoding="utf-8")
    # last, the folders the unpacks created, deepest first, each only if it's empty now
    for d in sorted(set(made_dirs), key=len, reverse=True):
        try:
            os.rmdir(long_path(d))
        except OSError:
            pass
    return out


def cmd_undo_unpack(args):
    r = undo_unpack(args.source)
    if not r["hosts"]:
        print("Nothing to undo: no unpack journal found in Music/Freighter.")
        return 0
    print(f"Undid the unpack from {', '.join(r['hosts'])}: removed {r['removed']} files and put back "
          f"{r['restored']} repaired projects.")
    if r["kept"]:
        print(f"Kept {len(r['kept'])} files that changed since (they're your work now):")
        for k in r["kept"][:20]:
            print(f"  {k}")
    if r["need_admin"]:
        print(f"{len(r['need_admin'])} plugin files are still in Program Files (Windows didn't get permission to "
              "remove them). Run undo again and choose Yes, or delete them yourself:")
        for k in r["need_admin"][:20]:
            print(f"  {k}")
    return 0


def old_search_paths(manifest):
    return manifest.get("fl_search_paths") or []


def same_place(manifest, src, rehearsal_root=None):
    """Where an item goes back to on a same-OS move: the same place it came from (issue #67). Folders inside the
    old home folder go to the same spot in this one, following Desktop, Documents and Music to wherever they are
    here (OneDrive or not). Folders elsewhere (D:\\Samples, /Volumes/SSD) go to the same path if that drive exists
    here. Returns None when there's no such place (cross-OS moves, or a drive this computer doesn't have).
    In a rehearsal, other drives are recreated under rehearsal_root, so it never writes to real drives."""
    if manifest.get("source_os") != THIS_OS:
        return None
    fold = THIS_OS == "windows"
    key = norm(src, fold)

    def under(root):
        r = norm(root, fold)
        return key == r or key.startswith(r + "/")

    def rel_to(root):
        return src.replace("\\", "/")[len(norm(root, False)):].lstrip("/")

    here = user_folders(home())
    for name, old in (manifest.get("user_folders") or {}).items():
        if name in here and under(old):
            return here[name].joinpath(*[x for x in rel_to(old).split("/") if x])
    old_home = manifest.get("source_home", "")
    if old_home and under(old_home):
        return home().joinpath(*[x for x in rel_to(old_home).split("/") if x])
    p = Path(src)
    if fold:
        drive, rest = p.drive, [x for x in p.parts[1:]]
        anchor = Path(drive + "\\") if drive else None
        label = drive.rstrip(":") + " drive" if drive else "Other"
    else:
        parts = p.parts
        if len(parts) > 2 and parts[1] == "Volumes":
            anchor, label, rest = Path(*parts[:3]), parts[2], list(parts[3:])
        else:
            anchor, label, rest = (Path(*parts[:2]) if len(parts) > 1 else None), "Computer", list(parts[1:])
    if rehearsal_root is not None:
        return Path(rehearsal_root, "Other drives", safe_rel(label), *rest)
    return p if anchor is not None and anchor.exists() else None


def bundle_summary(path):
    """What the app shows about a bundle on a drive: whose, when, how big, whether it's finished."""
    path = Path(path)
    try:
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    try:
        problems = bundle_problems(path, manifest)
    except (OSError, ValueError, KeyError):
        problems = ["This bundle couldn't be checked."]
    return {"path": str(path), "host": manifest.get("source_host", "?"), "os": manifest.get("source_os", "?"),
            "created": manifest.get("created", ""), "items": len(manifest.get("items", [])),
            "bytes": sum(it.get("bytes", 0) or 0 for it in manifest.get("items", [])),
            "same_os": manifest.get("source_os") == THIS_OS, "problems": problems,
            "version": manifest.get("freighter_version", ""),
            "range": (manifest.get("projects_range") or {}).get("label")}


def find_bundles(folder, depth=1):
    """Freighter bundles in a folder and (depth) the folders inside it."""
    out, folder = [], Path(folder)
    try:
        children = sorted(folder.iterdir())
    except OSError:
        return out
    for child in children:
        if child.name.startswith("Freighter-") and (child / "manifest.json").is_file():
            out.append(child)
        elif depth and child.is_dir() and not child.name.startswith(".") and not skip_dir(str(folder), child.name):
            out += find_bundles(child, depth - 1)
    return out


def blocked_hint(os_name=None):
    """What to do when the system refused to let Freighter write some files."""
    if (os_name or THIS_OS) == "windows":
        return ("Windows refused to let Freighter write some files. If Ransomware protection is on, allow Freighter: "
                "Windows Security > Virus & threat protection > Ransomware protection > Allow an app through "
                "Controlled folder access. Then unpack again; it skips what's already in place.")
    return ("macOS refused to let Freighter write some files. Allow it in System Settings > Privacy & Security > "
            "Files and Folders (or Full Disk Access), and for folders in /Library use an administrator account. "
            "Then unpack again; it skips what's already in place.")


def windows_drive(kind, letter, system):
    """(list it?, removable?) for a Windows drive letter, from GetDriveTypeW's answer. USB hard drives and SSDs
    call themselves "fixed", so only the drive Windows runs from counts as the computer's own disk; other fixed
    drives are unknown (None), so they get no warning. Network drives aren't listed: a disconnected one can take
    half a minute to answer (they can still be typed in). CD drives and unused letters aren't listed either."""
    if kind == 2:
        return True, True
    if kind == 3:
        return True, False if letter.upper() == system.upper() else None
    return False, None


def list_drives():
    """Drives and volumes to pack onto or unpack from: [{name, path, free, total, fs, removable, bundles}]."""
    found = []
    if rs.IS_WIN:
        import ctypes
        import string
        k32 = ctypes.windll.kernel32
        k32.SetErrorMode(1)   # an empty card-reader slot answers quietly instead of popping up "no disk"
        system = (os.environ.get("SystemDrive") or "C:")[:1].upper()
        for letter in string.ascii_uppercase:
            root = f"{letter}:\\"
            listed, removable = windows_drive(k32.GetDriveTypeW(root), letter, system)
            if not listed:
                continue
            label = ctypes.create_unicode_buffer(256)
            k32.GetVolumeInformationW(root, label, 256, None, None, None, None, 0)
            found.append((f"{label.value or 'Local Disk'} ({letter}:)", Path(root), removable))
    else:
        vols = Path("/Volumes")
        for v in sorted(vols.iterdir()) if vols.is_dir() else []:
            try:
                if v.is_symlink() and v.resolve() == Path("/"):
                    continue                     # the Mac's own disk, listed below as "This Mac"
            except OSError:
                continue
            if v.is_dir():
                found.append((v.name, v, True))
        found.append(("This Mac (your user folder)", real_home(), False))
    out = []
    for name, path, removable in found:
        try:
            du = shutil.disk_usage(path)
        except OSError:
            continue
        out.append({"name": name, "path": str(path), "free": du.free, "total": du.total, "fs": filesystem(path),
                    "removable": removable,
                    "bundles": [b for b in (bundle_summary(x) for x in find_bundles(path)) if b]})
    return out


def put_plugins_in_place(plan, moves, landing, journal, entries, rehearsal_root=None):
    """After unpacking, copied plugins wait in the holding folder (Music/Freighter/<old computer>/Plugins).
    Checkpoint 3 (issue #81): this computer's virus scanner checks them there first, and anything it flags never
    reaches a plugin folder. Then each goes into its plugin folder: directly when Freighter may write there, or
    with Windows' permission for Program Files (one prompt for all, issue #80). On a Mac, plugins Apple hasn't
    checked are marked as coming from elsewhere, so macOS checks them the first time a DAW loads them.
    Every file is checked against the bundle and journaled, so undo can remove it. Returns what happened:
    {placed, already, waiting, flagged, asks, asked, declined, scan}, plugin items as {label, names, where}."""
    out = {"placed": [], "already": [], "waiting": [], "flagged": [], "asks": [], "asked": False, "declined": False,
           "scan": ""}
    fold = THIS_OS == "windows"
    mine = [(it, dest, idx) for it, dest, idx in plan if it["id"] in moves]
    tr = lambda rec: rec["rel"] if THIS_OS != "windows" else rec["stored"]

    # checkpoint 3: scan what arrived before anything goes into a plugin folder
    arrived = [(it, dest, idx) for it, dest, idx in mine if idx and os.path.exists(long_path(dest))]
    flagged = {}
    if arrived:
        found, out["scan"] = scan_plugin_items(
            [{"id": it["id"], "label": it["label"], "plugins": it.get("plugins", []), "source_path": str(dest)}
             for it, dest, _idx in arrived], clean=True)
        flagged = {f["id"]: f for f in found}
        holding = norm(str(landing / "Plugins"), fold)
        for it, dest, _idx in arrived:
            if it["id"] in flagged and norm(str(dest), fold).startswith(holding + "/"):
                shutil.rmtree(long_path(dest), ignore_errors=True)   # Freighter's own copy; the scanner may have taken it

    # straight into plugin folders Freighter may write to
    done_items = set()
    for it, dest, idx in mine:
        m = moves[it["id"]]
        if it["id"] in flagged or m["how"] not in ("in place", "user") or not idx:
            continue
        ok = True
        for rec in idx.values():
            src, dst = dest / tr(rec), m["final"] / tr(rec)
            if file_stat(src) is None:
                ok = False
                continue
            if file_stat(dst) is None:
                for d in missing_folders(dst.parent):
                    journal.write(json.dumps({"kind": "dir", "path": str(d)}) + "\n")
                try:
                    digest = copy_and_hash(src, dst)
                except OSError:
                    ok = False
                    continue
                if rec.get("x") and THIS_OS != "windows":
                    os.chmod(long_path(dst), os.stat(long_path(dst)).st_mode | 0o111)
                st = file_stat(dst)
                journal.write(json.dumps({"kind": "file", "path": str(dst), "sha256": digest, "size": st.st_size,
                                          "mtime_ns": st.st_mtime_ns}) + "\n")
                if digest != rec["sha256"]:
                    ok = False
                    continue
            try:
                os.remove(long_path(src))   # the waiting copy isn't needed any more
            except OSError:
                pass
        if ok:
            done_items.add(it["id"])
    journal.flush()

    # Program Files: Windows asks for permission once
    admin = [(it, dest, idx) for it, dest, idx in mine if moves[it["id"]]["how"] == "admin" and it["id"] not in flagged]
    pairs, sums, new_dirs, wants = [], {}, set(), {}
    for it, dest, idx in admin:
        final = moves[it["id"]]["final"]
        for rec in idx.values():
            src, dst = dest / tr(rec), final / tr(rec)
            if file_stat(src) is not None and file_stat(dst) is None:
                pairs.append([str(src), str(dst)])
                sums[str(dst)] = (rec["sha256"], str(src))
                wants.setdefault(it["id"], []).append(str(dst))
                new_dirs.update(str(d) for d in missing_folders(dst.parent))
    done = set()
    if pairs and rehearsal_root is None:
        out["asked"] = True
        print(f"\nPutting {len(pairs)} plugin files in Program Files. Windows asks for permission once: choose Yes.",
              flush=True)
        out["declined"] = not as_admin({"files": pairs}, landing)
        for dst, (sha, src) in sums.items():
            st = file_stat(dst)
            if st is not None and hash_file(dst) == sha:
                journal.write(json.dumps({"kind": "file", "path": dst, "sha256": sha, "size": st.st_size,
                                          "mtime_ns": st.st_mtime_ns}) + "\n")
                done.add(dst)
                try:
                    os.remove(long_path(src))
                except OSError:
                    pass
        for d in sorted(new_dirs, key=len):
            if os.path.isdir(long_path(d)):
                journal.write(json.dumps({"kind": "dir", "path": d}) + "\n")
        journal.flush()
    for it, _dest, idx in admin:
        if idx and all(dst in done for dst in wants.get(it["id"], [])) and wants.get(it["id"]):
            done_items.add(it["id"])

    # tidy the emptied waiting folders
    for d in [dest for _it, dest, _idx in mine] + [landing / "Plugins"]:
        for cur, _dirs, _files in sorted(os.walk(long_path(d)), key=lambda w: -len(w[0])):
            try:
                os.rmdir(cur)
            except OSError:
                pass

    for it, dest, idx in mine:
        m = moves[it["id"]]
        row = {"id": it["id"], "label": m["label"], "names": m["names"], "where": str(m["final"] or dest)}
        if it["id"] in flagged:
            out["flagged"].append(dict(row, where=None, threat=flagged[it["id"]]["threat"]))
        elif it["id"] in done_items or (not idx and m["before"]):
            out["placed"].append(row)
            for e in entries:
                if e.get("id") == it["id"]:
                    e["new"] = str(m["final"])
            if THIS_OS == "mac" and it["id"] in done_items:
                asks = [p for p in plugin_bundles(m["final"], it) if not rs.mac_notarized(p)]
                for p in asks:
                    if mark_from_elsewhere(p):
                        out["asks"].append(p.name)
        elif not idx and m["already"]:
            out["already"].append(row)
        elif idx or m["before"]:
            out["waiting"].append(dict(row, where=str(dest), final=str(m["final"]) if m["final"] else None))
    return out


PLUGIN_BUNDLE_EXTS = (".vst3", ".vst", ".component", ".clap", ".dll")


def plugin_bundles(final, it):
    """The plugins inside a placed item: the item itself (a bundle or file), the files of a loose-files item, or
    the plugin bundles inside a maker's folder."""
    final = Path(final)
    if "only" in it:
        return [final / n for n in it["only"]]
    if final.name.lower().endswith(PLUGIN_BUNDLE_EXTS):
        return [final]
    return sorted(p for p in final.iterdir() if p.name.lower().endswith(PLUGIN_BUNDLE_EXTS)) \
        if final.is_dir() else []


def mark_from_elsewhere(path):
    """Mac: mark a copied plugin as coming from elsewhere, the way a download is marked, so macOS checks it for
    malware (XProtect, with Gatekeeper) the first time a DAW loads it (issue #81). True if the mark is on."""
    value = f"0083;{int(time.time()):x};Freighter;"
    try:
        subprocess.run(["xattr", "-w", "-r", "com.apple.quarantine", value, str(path)],
                       capture_output=True, timeout=120)
        r = subprocess.run(["xattr", "-p", "com.apple.quarantine", str(path)], capture_output=True, text=True,
                           timeout=60)
        return r.returncode == 0 and r.stdout.startswith("0083")
    except (OSError, subprocess.SubprocessError):
        return False


def copied_plugin_lines(p, source_os):
    rescan = ("In FL Studio: Options > Manage plugins > Find installed plugins. In Ableton Live: Settings > "
              "Plug-Ins > Rescan.")
    lines = []
    if p["placed"]:
        lines += ["Plugins copied from the old computer, back in their plugin folders (no download needed):"]
        lines += [f"  {', '.join(r['names'])} -> {r['where']}" for r in p["placed"]]
        lines += [f"Let your DAW find them. {rescan}",
                  "If one won't load, or asks for its serial or to sign in, enter it again (deactivate it on the old "
                  "computer first if its maker counts activations), or install it from its maker.", ""]
    if p.get("asks"):
        lines += ["macOS checks these for malware the first time your DAW loads them, because Apple hasn't checked "
                  "them before: " + ", ".join(p["asks"]) + ". When macOS says it can't open one, go to System Settings "
                  "> Privacy & Security and click Open Anyway (once for each).", ""]
    if p.get("flagged"):
        lines += ["Not copied, for safety: this computer's virus scanner flagged them, so they never reached a plugin "
                  "folder. Install a fresh copy of each from its maker:"]
        lines += [f"  {', '.join(r['names'])} ({r['threat']})" for r in p["flagged"]]
        lines += [""]
    if p.get("scan") and not (p.get("asks") and p["scan"].startswith("macOS")):   # don't say it twice
        lines += [p["scan"], ""]
    if p["waiting"]:
        lines += ["Plugins waiting in the landing folder, not in a plugin folder yet:"]
        lines += [f"  {', '.join(r['names'])}: {r['where']}" + (f" (goes in {r['final']})" if r.get("final") else "")
                  for r in p["waiting"]]
        if p.get("declined"):
            lines += ["Windows didn't get permission to put them in Program Files. Unpack again and choose Yes when "
                      "Windows asks, or copy each one into the folder named after it."]
        elif any(r.get("final") for r in p["waiting"]):
            lines += ["Freighter couldn't put them in their plugin folders. Copy each one into the folder named after "
                      "it, or install them from each maker."]
        else:
            lines += ["This computer has no such plugin folder. Add the waiting folder to your DAW's plugin folders "
                      "(FL Studio: Options > Manage plugins > Plugin search paths), or install them from each maker."]
        lines += [""]
    if p["already"]:
        lines += ["Already installed here, so they weren't copied (yours were kept): "
                  + ", ".join(n for r in p["already"] for n in r["names"]), ""]
    if p["other_os"]:
        other = "Windows" if source_os == "windows" else "Mac"
        lines += [f"Plugins from the old computer that only run on {other}, so they weren't copied (install their "
                  "versions for this computer from each maker): " + ", ".join(p["other_os"]), ""]
    return lines


def say_copied_plugins(p, source_os):
    lines = copied_plugin_lines(p, source_os)
    if lines:
        print("\n" + "\n".join(lines).rstrip())


@staying_awake
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
    if problems and not getattr(args, "anyway", False) and not getattr(args, "preview", False):
        sys.exit("This bundle isn't complete, so nothing was unpacked:\n" + "\n".join(f"  - {p}" for p in problems)
                 + "\nOn the old computer, pack onto the drive again: it picks up where it stopped.\n"
                 "If the old computer is gone, unpack what's there with --anyway (or answer y in the menu).")
    src_host = manifest["source_host"]
    dest_known = known_roots(THIS_OS, home())
    landing = moved_root(src_host)

    previous = previous_entries(landing)
    moved = [it for it in manifest["items"] if not (it["kind"] == "known" and it["id"] in dest_known)]
    places = landing_folders(moved, landing, previous, manifest["source_os"] == "windows")
    together = getattr(args, "keep_together", False)
    rehearsal_root = getattr(args, "rehearsal_root", None)
    plan, back_in_place = [], 0
    cross_os = manifest["source_os"] != THIS_OS
    fold_here = THIS_OS == "windows"
    earlier = {norm(e["path"], fold_here): e for e in read_journal(landing / UNPACK_JOURNAL) if e.get("kind") == "file"}
    plugin_moves, plugins_other_os = {}, []   # copied plugins (issue #80)
    for it in manifest["items"]:
        index = read_jsonl(bundle / "index" / f"{it['id']}.jsonl")
        pm = None
        if it.get("plugin_files"):
            names = [f"{x['name']} ({x['format']})" for x in it.get("plugins", [])]
            if cross_os:   # a Windows plugin can't run on a Mac, or the other way round
                plugins_other_os += names
                continue
            # every copied plugin waits in the holding folder until it's been checked (issue #81)
            staging = landing / "Plugins" / places[it["id"]].name
            dest, final, how, check = plugin_destination(manifest, it, rehearsal_root, staging)
            pm = plugin_moves[it["id"]] = {"label": it["label"], "names": names, "final": final, "how": how,
                                          "dest": staging, "already": 0, "before": 0}
            ours = lambda x: any(k == norm(str(x), fold_here) or k.startswith(norm(str(x), fold_here) + "/")
                                 for k in earlier)
            if "only" in it:   # never over a plugin that's installed here already, even an older one
                keep = {}
                for rel, rec in index.items():
                    tr = rec["rel"] if THIS_OS != "windows" else rec["stored"]
                    there = [x / tr for x in check if file_stat(x / tr) is not None]
                    if there:
                        pm["before" if ours(there[0]) else "already"] += 1
                    else:
                        keep[rel] = rec
                index = keep
            else:
                there = [x for x in check if os.path.exists(long_path(x))]
                if there:
                    pm["before" if ours(there[0]) else "already"] = len(index)
                    index = {}
            dest = staging
        elif it["kind"] == "known" and it["id"] in dest_known:
            dest = dest_known[it["id"]][1]
        else:
            again = None if together else same_place(manifest, it["source_path"], rehearsal_root)
            dest = again or places[it["id"]]
            back_in_place += again is not None
        if args.dest_for:
            for spec in args.dest_for:
                iid, _, folder = spec.partition("=")
                if iid == it["id"] and folder:
                    dest = Path(folder).expanduser()
                    if pm:
                        pm.update(dest=dest, final=None, how="waiting")
        plan.append((it, dest, index))

    total = sum(r["size"] for _, _, idx in plan for r in idx.values())
    if back_in_place:
        print(f"Same kind of computer, so {back_in_place} folder{'s go' if back_in_place != 1 else ' goes'} back to the "
              "same place as before (use --keep-together to put them all in Music/Freighter instead).")
    print("Unpacking:")
    for it, dest, idx in plan:
        print(f"  {it['label']}: {len(idx)} files -> {dest}")
    short = space_shortfalls(plan)
    if short:
        sys.exit("Not enough free space, so nothing was unpacked:\n" + "\n".join(
            f"  {where}: needs {rs.human_size(need)}, has {rs.human_size(free)} free" for where, need, free in short)
            + "\nFree up space, or send big items elsewhere with --dest-for ID=FOLDER.")

    if getattr(args, "preview", False):
        def plugin_note(it, idx):
            m = plugin_moves.get(it["id"])
            if m is None:
                return None
            if not idx:
                return "Already installed here: yours is kept" if m["already"] else "Already in place"
            return {"admin": f"Into {m['final']}: Windows asks for permission",
                    "user": "Into your own Plug-Ins folder (the shared one needs an administrator)",
                    "waiting": "Waits here: this computer has no such plugin folder"}.get(m["how"])
        LAST["unpack_preview"] = [{"label": it["label"], "files": len(idx), "bytes": sum(r["size"] for r in idx.values()),
                                   "dest": str(dest), "known": it["kind"] == "known" and it["id"] in dest_known,
                                   "note": plugin_note(it, idx)}
                                  for it, dest, idx in plan]
        return 0
    new_landing = missing_folders(landing)
    landing.mkdir(parents=True, exist_ok=True)
    # The journal: every file and folder this unpack creates, so it can be undone exactly (issue #73), and so a
    # later top-up can tell a file an earlier unpack put there (and nobody changed since) from the user's own.
    journal_path = landing / UNPACK_JOURNAL

    def contents(p, st):
        """The SHA-256 of the file at p. Taken from the journal when an earlier unpack copied it there and its size
        and time haven't changed since, so starting again doesn't read every file twice (issue #74)."""
        e = earlier.get(norm(str(p), fold_here))
        if e and e.get("size") == st.st_size and e.get("mtime_ns") == st.st_mtime_ns:
            return e["sha256"]
        return hash_file(p)
    journal = open(journal_path, "a", encoding="utf-8")
    for d in new_landing:       # e.g. Music/Freighter itself, on a computer that never had it
        journal.write(json.dumps({"kind": "dir", "path": str(d)}) + "\n")
    prog = Progress(total, "Copying", sum(len(idx) for _, _, idx in plan))
    prog.add(0, files=0)
    conflicts, bad, entries, replaced, blocked = [], [], [], 0, False
    for it, dest, idx in plan:
        data_root = bundle / "data" / it["id"]
        for rel, rec in idx.items():
            src = data_root / rec["stored"]
            target_rel = rec["rel"] if THIS_OS != "windows" else rec["stored"]
            dst = dest / target_rel
            st = file_stat(dst)
            if st is not None:
                now = contents(dst, st)
                if st.st_size == rec["size"] and now == rec["sha256"]:
                    prog.add(rec["size"], copied=False)
                    continue  # already there, identical
                e = earlier.get(norm(str(dst), fold_here))
                if e and e["sha256"] == now or it.get("plugin_files"):
                    # an earlier unpack put it there and nobody changed it since: the newer one wins. (A copied
                    # plugin only lands where no plugin was, or in its waiting folder: a renamed file would break it.)
                    replaced += 1
                else:
                    dst = dst.with_name(f"{dst.stem} (from {src_host}){dst.suffix}")
                    if file_size(dst) == rec["size"]:
                        prog.add(rec["size"], copied=False)
                        continue
                    conflicts.append(str(dst))
            for d in missing_folders(dst.parent):
                journal.write(json.dumps({"kind": "dir", "path": str(d)}) + "\n")
            try:
                digest = copy_and_hash(src, dst)
            except OSError as e:
                bad.append(f"{src}: {e}")
                blocked = blocked or isinstance(e, PermissionError)
                prog.add(rec["size"], copied=False)
                continue
            if rec.get("x") and THIS_OS != "windows":   # a Mac plugin's program file (issue #80)
                os.chmod(long_path(dst), os.stat(long_path(dst)).st_mode | 0o111)
            st = file_stat(dst)
            journal.write(json.dumps({"kind": "file", "path": str(dst), "sha256": digest, "size": st and st.st_size,
                                      "mtime_ns": st and st.st_mtime_ns}) + "\n")
            journal.flush()
            if digest != rec["sha256"]:
                bad.append(f"{dst}: checksum mismatch (the copy on the drive may be damaged)")
            prog.add(rec["size"])
        entry = {"id": it["id"], "label": it["label"], "old": it["source_path"], "new": str(dest)}
        if THIS_OS == "windows":
            # files unpacked under a different name (Windows can't hold the original, or two names clashed):
            # repair must find them by this record, never by guessing (issue #63)
            renamed = {rec["rel"]: rec["stored"] for rec in idx.values() if rec["stored"] != rec["rel"]}
            if renamed:
                entry["renamed"] = renamed
        entries.append(entry)

    plugins = put_plugins_in_place(plan, plugin_moves, landing, journal, entries, rehearsal_root)
    plugins["other_os"] = plugins_other_os

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
    hint = blocked_hint() if blocked else None
    if bad:
        lines += ["", "Problems:"] + bad + ([hint] if hint else [])
    (landing / "unpack-report.txt").write_text("\n".join(lines), encoding="utf-8")
    if hint:
        print("\n" + hint)

    journal.close()
    for note in notes:
        print(f"From the old computer: {note}")
    libraries = []   # sound libraries: how to reconnect the moved ones, where to get the rest (issue #71)
    for lib in manifest.get("libraries") or []:
        entry = next((e for e in entries if lib.get("item") and e.get("id") == lib["item"]), None)
        if entry:
            libraries.append(dict(lib, moved=True, new=entry["new"], steps=library_steps(lib, entry["new"])))
        else:
            libraries.append(dict(lib, moved=False, steps=[reinstall_step(lib)]))
    if libraries:
        print("\nSound libraries:")
        for lib in libraries:
            print(f"  {lib['name']} ({lib['maker']}): " + ("moved to " + lib["new"] if lib["moved"] else "not moved"))
            for step in lib["steps"]:
                print(f"    - {step}")
    say_copied_plugins(plugins, manifest["source_os"])
    if plugins["placed"] or plugins["waiting"] or plugins["other_os"] or plugins["flagged"]:
        (landing / "plugins-copied.txt").write_text("\n".join(copied_plugin_lines(plugins, manifest["source_os"])),
                                                     encoding="utf-8")
    # for the Plugins to install page (issue #82): what came along, and what was left out for safety
    by_id = {it["id"]: it for it in manifest["items"]}
    (landing / "plugins-copied.json").write_text(json.dumps({
        "placed": [x for r in plugins["placed"] for x in by_id.get(r.get("id"), {}).get("plugins", [])],
        "flagged": [dict(x, threat=r["threat"]) for r in plugins.get("flagged", [])
                    for x in by_id.get(r.get("id"), {}).get("plugins", [])]}, indent=1), encoding="utf-8")
    plugin_ids = {it["id"] for it in manifest["items"] if it.get("plugin_data")}
    presets = [{"label": e["label"], "old": e["old"], "new": e["new"]} for e in entries if e.get("id") in plugin_ids]
    cross_os = manifest["source_os"] != THIS_OS
    if presets:
        if cross_os:
            print("\nPlugin presets and data from the old computer are in these folders. Plugins look in their own "
                  "folders on this computer, so import them from each plugin's preset browser (or copy them into "
                  "its preset folder):")
        else:
            print("\nPlugin presets and data went back where they were, so your plugins find them:")
        for e in presets:
            print(f"  {e['new']}")
    old_search = old_search_paths(manifest)
    if old_search:
        print("\nFL Studio's browser folders on the old computer are now here. FL keeps its settings per computer, so")
        print("add them in FL Studio under Options > File settings > Browser extra search folders:")
        for sp in old_search:
            status, where = locate(sp, [pathmap])
            print(f"  {where if status != 'missing' else sp}")
    LAST["unpack"] = {
        "source_host": src_host, "source_os": manifest["source_os"], "landing": str(landing),
        "report": str(landing / "unpack-report.txt"), "problems": problems, "notes": notes,
        "conflicts": len(conflicts), "bad": bad[:50], "back_in_place": back_in_place, "replaced": replaced,
        "plugin_data": presets, "cross_os": cross_os, "blocked_hint": hint, "libraries": libraries,
        "plugins": plugins,
        "range": (manifest.get("projects_range") or {}).get("label"),
        "entries": [{"label": e["label"], "old": e["old"], "new": e["new"]} for e in entries
                    if e.get("id") in {it["id"] for it in manifest["items"]}],
        "fl_search": [str(locate(sp, [pathmap])[1] or sp) for sp in old_search_paths(manifest)],
    }
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
          + tool_cmd("freighter", "fix-fl"))
    print("  Plugins your projects still need, most useful first: menu option 7, or " + tool_cmd("freighter", "plugins"))
    print(f"  Everything the old computer had: {tool_cmd('manifest', '--compare')} \"{landing / f'plugins-{src_host}.csv'}\"")
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
    """Project files under roots (ext: ".flp", or a tuple of them). Autosave copies (in a project's own Backup folder)
    only with include_backups; a folder that merely has \"backup\" in its name (an archive of old projects) is
    searched like any other."""
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


def all_projects(roots, racks=False):
    """FL Studio then Ableton projects under roots, from one walk of the folders instead of two (issue #74).
    With racks, Ableton's racks and clips (.adg, .alc) too: they point at samples like sets do (issue #32)."""
    ableton = ABLETON_EXTS if racks else (".als",)
    found = [p for p in find_projects(roots, (".flp",) + ableton) if not in_program_place(p)]
    return [p for p in found if p.suffix.lower() == ".flp"] + [p for p in found if p.suffix.lower() in ableton]


# --------------------------------------------------------------------------- Ableton

FILEREF_RE = re.compile(r"<FileRef(?:\s[^>]*)?>(.*?)</FileRef>", re.S)
PATH_RE = re.compile(r'(<Path Value=")([^"]*)(")')
# Samples sit in <SampleRef><FileRef>...; other paths in a set are plugin binaries (PluginDesc) or the presets a
# device came from, which aren't samples. LivePackName marks Core Library / installed Pack content: Live finds it.
SAMPLEREF_RE = re.compile(r"<SampleRef>(.*?)</SampleRef>", re.S)
PACK_NAME_RE = re.compile(r'<LivePackName Value="([^"]*)"')
CREATOR_VERSION_RE = re.compile(r'Creator="Ableton Live (\d+)')


def als_sample_refs(text, unique=True):
    """Paths of the samples a Live 11+ set uses, as stored (issue #60: only samples, never plugins or Pack content).
    unique=False keeps every reference in order (to pair a repaired set with its original)."""
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
    return list(dict.fromkeys(out)) if unique else out
REL_RE = re.compile(r'<RelativePath Value="([^"]*)"')
CREATOR_RE = re.compile(r'Creator="([^"]*)"')


def xml_attr_escape(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


ABLETON_EXTS = (".als", ".adg", ".alc")   # Live sets, and the racks and clips saved in Live's browser (issue #32)
RACK_EXTS = (".adg", ".alc")


def backup_path(p):
    """Where a repair keeps the original: a 'Freighter backup' folder next to it. Racks and clips get an extra
    '.original' ending, so Live's browser doesn't list the old copy (with the old paths) next to the repaired one."""
    p = Path(p)
    name = p.name + ".original" if p.suffix.lower() in RACK_EXTS else p.name
    return p.parent / "Freighter backup" / name


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
        backup = backup_path(path)
        backup.parent.mkdir(exist_ok=True)
        made = not backup.exists()
        if made:
            shutil.copy2(path, backup)
        data = new_text.encode("utf-8")
        write_replace(Path(path), gzip.compress(data) if gz else data)
        journal_repair(path, backup, made)
    return result


def repair_projects(dry_run=False):
    """Repoint every Ableton and FL project on this computer at its moved samples. Returns a summary."""
    maps = load_pathmaps(None)
    roots = project_search_roots(maps, [])
    fl_open = fl_is_running()
    out = {"projects": 0, "repaired": 0, "samples_fixed": 0, "in_place": 0, "missing": [], "old_format": [],
           "damaged": [], "unreadable": [], "fl_open": fl_open}
    found = all_projects(roots, racks=True)
    for p in (p for p in found if p.suffix.lower() in ABLETON_EXTS):
        try:
            r = fix_als(p, maps, dry_run)
        except Exception:
            out["unreadable"].append(str(p))
            continue
        out["projects"] += 1
        if r["old_format"]:
            out["old_format"].append(str(p))
            continue
        out["repaired"] += bool(r["fixed"])
        out["samples_fixed"] += r["fixed"]
        out["in_place"] += r["ok"]
        out["missing"] += [f"{p.name}: {m}" for m in r["missing"]]
    for p in (p for p in found if p.suffix.lower() == ".flp"):
        try:
            r = fix_flp(p, maps, dry_run=dry_run or fl_open)
        except (ValueError, OSError, struct.error):
            out["unreadable"].append(str(p))
            continue
        out["projects"] += 1
        out["repaired"] += bool(r["fixed"]) and not (dry_run or fl_open)
        out["samples_fixed"] += r["fixed"]
        out["in_place"] += r["ok"]
        out["missing"] += [f"{p.name}: {m}" for m in r["missing"]]
        if r["damaged"]:
            out["damaged"].append(str(p))
    return out


def cmd_fix_ableton(args):
    maps = load_pathmaps(args.pathmap)
    if not maps:
        print("No path map found yet (it's created by 'unpack'). Checking projects for missing samples only.")
    roots = project_search_roots(maps, args.folder)
    print("Looking for Ableton projects, racks and clips in:\n  " + "\n  ".join(map(str, roots)), flush=True)
    projects = [p for p in find_projects(roots, ABLETON_EXTS) if not in_program_place(p)]
    print(f"Found {len(projects)} projects, racks and clips.\n")
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
FLP_TEXT_DATAPATH = 202 # the project's data folder (recordings, renders), stored as an absolute path
FLP_TEXT_VERSION = 199  # FL version string
FL_PLACEHOLDER = re.compile(r"^%FL[^%]*%", re.I)


def flp_events(data, strict=False):
    """Walk an .flp file's events. Returns (data_start, data_end, events) where each event is
    (event id, start offset, payload start, payload end, unicode flag in effect). Raises ValueError if the file
    isn't an FL project. A damaged file (e.g. an autosave caught mid-save) raises when strict (the writer never
    patches one), and otherwise gives the events before the damage, as reading always has.
    The writer below uses the same walk, so reading and writing can't disagree."""
    if data[:4] != b"FLhd":
        raise ValueError("not an FL Studio project")
    hlen = struct.unpack("<I", data[4:8])[0]
    pos = 8 + hlen
    if data[pos:pos + 4] != b"FLdt":
        raise ValueError("no data chunk")
    dlen = struct.unpack("<I", data[pos + 4:pos + 8])[0]
    start = pos + 8
    pos, end = start, start + dlen
    if end > len(data):
        if strict:
            raise ValueError("truncated")
        end = len(data)
    unicode, events = False, []
    while pos < end:
        at, eid = pos, data[pos]
        pos += 1
        if eid < 64:
            size = 1
        elif eid < 128:
            size = 2
        elif eid < 192:
            size = 4
        else:
            size = shift = 0
            while True:
                if pos >= end:
                    if strict:
                        raise ValueError("truncated")
                    return start, end, events
                b = data[pos]
                pos += 1
                size |= (b & 0x7F) << shift
                shift += 7
                if not b & 0x80:
                    break
        if pos + size > end:
            if strict:
                raise ValueError("damaged: an event runs past the end of the file")
            return start, end, events
        events.append((eid, at, pos, pos + size, unicode))
        if eid == FLP_TEXT_VERSION:
            version = data[pos:pos + size].rstrip(b"\0").decode("ascii", "replace")
            nums = [int(x) for x in re.findall(r"\d+", version)[:2]] + [0, 0]
            unicode = nums[0] > 11 or (nums[0] == 11 and nums[1] >= 5)
        pos += size
    return start, end, events


def flp_text(data, ev):
    eid, _at, a, b, unicode = ev
    return data[a:b].decode("utf-16-le" if unicode else "latin-1", "replace").rstrip("\0")


def flp_samples(path):
    """Read the sample paths an .flp project references. Read-only.
    Returns (FL version string, [paths]). Raises ValueError for unreadable files."""
    with open(long_path(path), "rb") as f:
        data = f.read()
    _start, _end, events = flp_events(data)
    version = next((data[a:b].rstrip(b"\0").decode("ascii", "replace")
                    for eid, _at, a, b, _u in events if eid == FLP_TEXT_VERSION), "")
    samples = [t for t in (flp_text(data, ev) for ev in events if ev[0] == FLP_TEXT_SAMPLE) if t]
    return version, samples


def flp_varint(n):
    out = bytearray()
    while True:
        c, n = n & 0x7F, n >> 7
        out.append(c | (0x80 if n else 0))
        if not n:
            return bytes(out)


def flp_rewrite(data, new_path_for):
    """Patch an .flp in memory: each sample-path (196) and project-data-folder (202) event whose path
    new_path_for(path) maps to something new is re-encoded; every other byte is copied unchanged and the data
    chunk's length is fixed. Never re-saves the whole project (full re-saves are known to corrupt FL files).
    Returns (new bytes, {old path: new path} for what changed, [paths that couldn't be written in this format])."""
    start, end, events = flp_events(data, strict=True)
    out = bytearray(data[:start])
    copied, changed, skipped = start, {}, []
    for ev in events:
        eid, at, _a, b, unicode = ev
        if eid not in (FLP_TEXT_SAMPLE, FLP_TEXT_DATAPATH):
            continue
        old = flp_text(data, ev)
        new = new_path_for(old) if old else None
        if not new or new == old:
            continue
        try:
            payload = new.encode("utf-16-le") + b"\0\0" if unicode else new.encode("latin-1") + b"\0"
        except UnicodeEncodeError:       # an old (pre-11.5) project can't hold this name
            skipped.append(new)
            continue
        out += data[copied:at] + bytes([eid]) + flp_varint(len(payload)) + payload
        copied = b
        changed[old] = new
    out += data[copied:end]
    struct.pack_into("<I", out, start - 4, len(out) - start)
    out += data[end:]                    # anything after the data chunk stays as it was
    return bytes(out), changed, skipped


def flp_same_except_paths(a, b):
    """True when two .flp files have the same events in the same order, and only path events differ."""
    _s1, _e1, ev1 = flp_events(a, strict=True)
    _s2, _e2, ev2 = flp_events(b, strict=True)
    if [e[0] for e in ev1] != [e[0] for e in ev2]:
        return False
    for x, y in zip(ev1, ev2):
        if x[0] not in (FLP_TEXT_SAMPLE, FLP_TEXT_DATAPATH) and a[x[2]:x[3]] != b[y[2]:y[3]]:
            return False
    return a[:8] == b[:8] and a[8:_s1 - 8] == b[8:_s2 - 8]


def fl_is_running():
    """True when FL Studio is open (it could save over a project being repaired). False if unsure."""
    try:
        if rs.IS_WIN:
            out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=20).stdout
            return any(name in out.lower() for name in ('"fl64.exe"', '"fl.exe"', '"fl studio'))
        out = subprocess.run(["ps", "-Axo", "comm"], capture_output=True, text=True, timeout=20).stdout
        return any("fl studio" in line.lower() and ".app/contents/macos/" in line.lower() for line in out.splitlines())
    except (OSError, subprocess.SubprocessError):
        return False


def fix_flp(path, maps, dry_run=False):
    """Repoint an FL Studio project's samples (and its project-data folder) at their moved copies.
    Only the path events change; the original is kept in a 'Freighter backup' folder next to the project.
    Returns what was found and done."""
    result = {"project": str(path), "version": "", "ok": 0, "fixed": 0, "builtin": 0, "missing": [],
              "skipped": [], "damaged": False}
    version, samples = flp_samples(path)
    result["version"] = version
    with open(long_path(path), "rb") as f:
        data = f.read()
    try:
        flp_events(data, strict=True)
    except ValueError:
        result["damaged"] = True       # never patch a damaged file (often an autosave caught mid-save)
    found = {}

    def new_path_for(old):
        if FL_PLACEHOLDER.match(old):
            return None
        if old not in found:
            status, new = locate(old, maps)
            found[old] = (status, str(new) if new else None)
        status, new = found[old]
        return new if status == "moved" else None

    for smp in dict.fromkeys(samples):
        if FL_PLACEHOLDER.match(smp):
            result["builtin"] += 1
            continue
        new_path_for(smp)
        status = found[smp][0]
        if status == "ok":
            result["ok"] += 1
        elif status == "missing":
            result["missing"].append(smp)
    wanted = [smp for smp in dict.fromkeys(samples) if found.get(smp, ("",))[0] == "moved"]
    if not wanted or result["damaged"]:
        return result
    new_data, changed, skipped = flp_rewrite(data, new_path_for)
    result["skipped"] = skipped
    result["fixed"] = sum(1 for smp in wanted if smp in changed)
    if dry_run or not changed:
        return result
    # check the patched file before it replaces anything: same events, only the paths we meant to change
    _v, new_samples = (None, [t for t in (flp_text(new_data, ev) for ev in flp_events(new_data, strict=True)[2]
                                          if ev[0] == FLP_TEXT_SAMPLE) if t])
    if not flp_same_except_paths(data, new_data) or new_samples != [changed.get(x, x) for x in samples]:
        raise RuntimeError(f"Freighter's FL repair didn't check out for {path}; the project was left unchanged.")
    backup = backup_path(path)
    backup.parent.mkdir(exist_ok=True)
    made = not backup.exists()
    if made:
        shutil.copy2(path, backup)
    write_replace(Path(path), new_data)
    journal_repair(path, backup, made)
    return result


def cmd_fix_fl(args):
    """Repoint FL Studio projects at moved samples. check-fl (dry_run) only reports."""
    dry = getattr(args, "dry_run", False)
    # a rehearsal repairs its own copies, which FL can't have open, so it always tests the repair
    if not dry and not getattr(args, "copies_only", False) and fl_is_running():
        print("FL Studio is open. Close it first, so it can't save over a project while it's being repaired.\n"
              "Checking only; nothing was changed.")
        dry = True
    maps = load_pathmaps(args.pathmap)
    roots = project_search_roots(maps, args.folder)
    print("Looking for FL Studio projects in:\n  " + "\n  ".join(map(str, roots)), flush=True)
    projects = find_projects(roots, ".flp", include_backups=args.include_backups)
    print(f"Found {len(projects)} projects.\n")
    totals = {"ok": 0, "builtin": 0, "fixed": 0, "missing": 0, "projects_fixed": 0}
    unreadable, damaged, report = [], [], [
        f"Freighter {__version__} FL Studio {'check' if dry else 'repair'}, {dt.datetime.now():%Y-%m-%d %H:%M}", ""]
    for p in projects:
        try:
            r = fix_flp(p, maps, dry_run=dry)
        except (ValueError, OSError, struct.error) as e:
            unreadable.append(f"{p} ({e})")
            continue
        for k in ("ok", "builtin", "fixed"):
            totals[k] += r[k]
        totals["missing"] += len(r["missing"])
        totals["projects_fixed"] += bool(r["fixed"])
        if r["damaged"]:
            damaged.append(str(p))
        if r["fixed"] or r["missing"]:
            verb = "can be repointed" if dry else "repointed"
            print(f"  {p.name} (FL {r['version']}): {r['fixed']} {verb}, {len(r['missing'])} missing")
        report.append(f"{p}  FL {r['version']}  fixed={r['fixed']} missing={len(r['missing'])}"
                      + ("  DAMAGED: not changed" if r["damaged"] else ""))
        report += [f"    missing: {m}" for m in r["missing"]]
        report += [f"    can't be written in this old project format: {x}" for x in r["skipped"]]

    if dry:
        print(f"\nSamples: {totals['ok']} found in place, {totals['builtin']} in FL's own folders (FL finds those "
              f"itself), {totals['fixed']} can be repointed to their moved copies, {totals['missing']} missing.")
    else:
        print(f"\nRepaired {totals['projects_fixed']} FL Studio projects: {totals['fixed']} samples now point to their "
              f"moved copies. {totals['ok']} were already in place, {totals['builtin']} are in FL's own folders "
              f"(FL finds those itself), {totals['missing']} are missing.")
        if totals["projects_fixed"]:
            print("The original of each changed project is kept in a 'Freighter backup' folder next to it.")
    if damaged:
        print(f"\n{len(damaged)} projects look damaged (often an autosave caught mid-save) and were left unchanged.")
    if unreadable:
        print(f"\n{len(unreadable)} projects couldn't be read (listed in the report).")
    out = home() / "Music" / "Freighter"
    out.mkdir(parents=True, exist_ok=True)
    name = "fl-check-report.txt" if dry else "fl-repair-report.txt"
    (out / name).write_text("\n".join(report + ["", "Damaged (left unchanged):"] + damaged
                                       + ["", "Unreadable:"] + unreadable), encoding="utf-8")
    print(f"Full list: {out / name}")
    return 0


# --------------------------------------------------------------------------- plugin readiness (#70)

FLP_TEXT_PLUGIN_INTERNAL = 201  # "Fruity Wrapper" for third-party plugins, else the FL plugin's own name
FLP_DATA_PLUGIN = 213           # plugin state; for wrapped plugins a list of records
FL_WRAPPER = "Fruity Wrapper"
PLUGINDESC_RE = re.compile(r"<PluginDesc>(.*?)</PluginDesc>", re.S)
VALUE_RE = lambda tag: re.compile(rf'<{tag} Value="([^"]*)"')


def plugin_format(path):
    ext = Path(str(path).replace("\\", "/")).suffix.lower()
    return {".vst3": "VST3", ".dll": "VST2", ".vst": "VST2", ".component": "AU", ".clap": "CLAP"}.get(ext, "")


def flp_plugins(data):
    """Third-party plugins an FL project uses, and FL's own plugins. Third-party ones sit behind
    "Fruity Wrapper": their state (event 213) is a list of records (uint32 id, uint64 size, data); record 54 is the
    plugin's real name, 56 its maker and 55 the file it was loaded from (newer FL versions)."""
    _s, _e, events = flp_events(data)
    third, native, last = [], set(), None
    for ev in events:
        if ev[0] == FLP_TEXT_PLUGIN_INTERNAL:
            last = flp_text(data, ev)
            if last and last != FL_WRAPPER:
                native.add(last)
        elif ev[0] == FLP_DATA_PLUGIN and last == FL_WRAPPER:
            b, pos, recs = data[ev[2]:ev[3]], 4, {}
            while pos + 12 <= len(b):
                rid, size = struct.unpack("<IQ", b[pos:pos + 12])
                pos += 12
                recs[rid] = b[pos:pos + size]
                pos += size
            name = recs.get(54, b"").decode("utf-8", "replace").strip("\0 ")
            if name:
                path = recs.get(55, b"").decode("utf-8", "replace").strip("\0 ")
                third.append({"name": name, "vendor": recs.get(56, b"").decode("utf-8", "replace").strip("\0 "),
                              "format": plugin_format(path)})
            last = None
    return third, sorted(native)


def als_plugins(text):
    """Third-party plugins a Live set uses, from each <PluginDesc>."""
    out = []
    for m in PLUGINDESC_RE.finditer(text):
        block = m.group(1)
        if "<Vst3PluginInfo" in block:
            name, fmt = VALUE_RE("Name").search(block), "VST3"
        elif "<AuPluginInfo" in block:
            name, fmt = VALUE_RE("Name").search(block), "AU"
        elif "<VstPluginInfo" in block:
            name, fmt = VALUE_RE("PlugName").search(block), "VST2"
        else:
            continue
        maker = VALUE_RE("Manufacturer").search(block)
        if name and name.group(1):
            out.append({"name": html.unescape(name.group(1)), "format": fmt,
                        "vendor": html.unescape(maker.group(1)) if maker else ""})
    return out


def project_plugins(p):
    """{"daw", "version", "plugins": [third-party], "native": [FL's own]} for one project, or None if unreadable."""
    try:
        if p.suffix.lower() == ".flp":
            with open(long_path(p), "rb") as f:
                data = f.read()
            third, native = flp_plugins(data)
            return {"daw": "FL Studio", "version": flp_samples(p)[0], "plugins": third, "native": native}
        text, _ = read_als(p)
        v = re.search(r'Creator="Ableton Live ([^"]+)"', text[:2000])
        return {"daw": "Ableton Live", "version": v.group(1) if v else "", "plugins": als_plugins(text), "native": []}
    except (ValueError, OSError, struct.error):
        return None


SHELL_MAKERS = {"waves": "waveshell"}   # one installed shell provides all of that maker's plugins


def plugin_checker(installed):
    """A function telling whether a plugin a project uses is installed here (rows from the plugin scan)."""
    have = set()
    for r in installed:
        have |= rs.match_keys(r.get("name", ""), r.get("vendor", ""), r.get("key", ""))
    shells = {maker for maker, shell in SHELL_MAKERS.items() if any(k.startswith(shell) for k in have)}

    def installed_already(pl):
        if rs.match_keys(pl["name"], pl.get("vendor", "")) & have:
            return True
        return any(m in (pl.get("vendor") or "").lower() for m in shells)
    return installed_already


def plugin_readiness(projects, installed):
    """Which projects open with what's installed. projects: {path: project_plugins(...)}; installed: rows from the
    plugin scan. Returns a dict for the report: missing plugins ordered by how many projects they hold back."""
    installed_already = plugin_checker(installed)
    missing, blocked, ready = {}, {}, []
    versions, warnings = {}, set()
    for path, info in projects.items():
        if not info:
            continue
        v = info.get("version") or ""
        if v:
            best = versions.get(info["daw"])
            nums = lambda x: [int(n) for n in re.findall(r"\d+", x)[:3]]
            if best is None or nums(v) > nums(best[0]):
                versions[info["daw"]] = (v, Path(path).name)
        need = []
        for pl in info["plugins"]:
            if pl["format"] == "AU" and THIS_OS == "windows":
                warnings.add(f"{pl['name']} was used as an AU (Mac-only) plugin in {Path(path).name}: install its VST3 "
                             "version and swap it in.")
            if not installed_already(pl):
                key = min(rs.match_keys(pl["name"], pl.get("vendor", "")), key=len)
                entry = missing.setdefault(key, {"name": pl["name"], "vendor": pl.get("vendor", ""), "projects": set()})
                entry["vendor"] = entry["vendor"] or pl.get("vendor", "")
                entry["projects"].add(path)
                need.append(key)
        if need:
            blocked[path] = set(need)
        else:
            ready.append(path)
    ranked = sorted(missing.values(), key=lambda e: (-len(e["projects"]), e["name"].lower()))
    # walk the list in order: how many projects open after installing the first N plugins
    unlocked, got, steps = 0, set(), []
    keys_in_order = [min(rs.match_keys(e["name"], e["vendor"]), key=len) for e in ranked]
    for key, e in zip(keys_in_order, ranked):
        got.add(key)
        now = sum(1 for need in blocked.values() if need <= got)
        steps.append(now - unlocked)
        unlocked = now
    return {"total": sum(1 for i in projects.values() if i), "ready": sorted(ready), "blocked": blocked,
            "missing": [dict(e, projects=sorted(e["projects"]), opens=n) for e, n in zip(ranked, steps)],
            "versions": versions, "warnings": sorted(warnings)}


def samples_now(p):
    """(samples checked, [missing ones]) for a project as it is now, by the rules the repair uses: FL's own sounds
    and installed Packs come with the DAW, %FLStudioUserData% means this computer's FL folder, and Live finds a
    sample through its path relative to the project. None when it can't be checked (Live 10 or older)."""
    p = Path(p)
    if p.suffix.lower() == ".flp":
        refs = []
        for s in dict.fromkeys(flp_samples(p)[1]):
            if FL_USER_DATA.match(s):
                refs.append(str(fl_user_data(home()).joinpath(*FL_USER_DATA.sub("", s).replace("\\", "/").strip("/").split("/"))))
            elif not FL_PLACEHOLDER.match(s):
                refs.append(s)
        missing = [r for r in refs if not in_program_place(r) and not exists(r)]
        return len(refs), missing
    text, _ = read_als(p)
    if not als_new_format(text):
        return None
    checked, missing = 0, []
    for sm in SAMPLEREF_RE.finditer(text):
        for fm in FILEREF_RE.finditer(sm.group(1)):
            block = fm.group(1)
            pack, pm, rel = PACK_NAME_RE.search(block), PATH_RE.search(block), REL_RE.search(block)
            if (pack and pack.group(1)) or not (pm and pm.group(2)):
                continue
            path = html.unescape(pm.group(2))
            if in_program_place(path):
                continue
            checked += 1
            if not exists(path) and not (rel and rel.group(1) and exists(p.parent / html.unescape(rel.group(1)))):
                missing.append(path)
    return checked, list(dict.fromkeys(missing))


def project_health(roots=None, installed=None):
    """Every project on this computer and whether it's ready: its samples found, its plugins installed
    (issue #78). Problems first."""
    roots = roots or project_search_roots(load_pathmaps(None), [])
    has = plugin_checker(rs.scan([]) if installed is None else installed)
    out = []
    for p in all_projects(roots):
        info = project_plugins(p)
        try:
            samples = samples_now(p)
        except (ValueError, OSError, struct.error):
            samples = "unreadable"
        need = list(dict.fromkeys(pl["name"] for pl in (info or {}).get("plugins", []) if not has(pl)))
        entry = {"path": str(p), "name": p.name, "daw": (info or {}).get("daw", ""), "version": (info or {}).get("version", ""),
                 "plugins_needed": need, "samples": 0, "missing_samples": []}
        if samples == "unreadable" or info is None:
            entry["status"] = "unreadable"
        elif samples is None:
            entry["status"] = "unchecked"     # Live 10 or older: open it in Live to check
        else:
            entry["samples"], entry["missing_samples"] = samples[0], samples[1]
            entry["status"] = "ready" if not samples[1] and not need else "attention"
        out.append(entry)
    order = {"attention": 0, "unreadable": 1, "ready": 2, "unchecked": 3}
    out.sort(key=lambda e: (order[e["status"]], -len(e["missing_samples"]) - len(e["plugins_needed"]), e["name"].lower()))
    unchecked = sum(1 for e in out if e["status"] == "unchecked")
    # sets saved by Live 10 or older can't be checked (often demo sets that came with sample packs): they're
    # listed apart and don't count against "ready"
    return {"projects": out, "total": len(out) - unchecked, "unchecked": unchecked,
            "ready": sum(1 for e in out if e["status"] == "ready")}


def health_lines(h):
    lines = [(f"All {h['total']} projects are ready" if h["ready"] == h["total"] else
              f"{h['ready']} of {h['total']} projects are ready") + ": every sample found and every plugin installed."]
    if h["unchecked"]:
        lines.append(f"{h['unchecked']} Ableton sets saved by Live 10 or older can't be checked automatically (often "
                     "demo sets that came with sample packs); they're listed at the end.")
    for e in h["projects"]:
        if e["status"] in ("ready", "unchecked"):
            continue
        lines.append("")
        lines.append(f"{e['name']}  ({e['path']})")
        if e["status"] == "unreadable":
            lines.append("  Couldn't be read. It may be damaged; open it in your DAW to check.")
        if e["missing_samples"]:
            lines.append(f"  {len(e['missing_samples'])} sample(s) missing:")
            lines += [f"    {m}" for m in e["missing_samples"][:20]]
        if e["plugins_needed"]:
            lines.append("  Needs these plugins installed: " + ", ".join(e["plugins_needed"]))
    old = [e for e in h["projects"] if e["status"] == "unchecked"]
    if old:
        lines += ["", "Saved by Live 10 or older (open them in Live to check):"] + [f"  {e['path']}" for e in old]
    return lines


def cmd_health(args):
    """Check every project: samples found, plugins installed (issue #78)."""
    print("Checking every project's samples and plugins...", flush=True)
    h = project_health()
    lines = health_lines(h)
    out = home() / "Music" / "Freighter" / "Reports"
    out.mkdir(parents=True, exist_ok=True)
    report = out / "project-health.txt"
    report.write_text(f"Freighter {__version__} project check, {dt.datetime.now():%Y-%m-%d %H:%M}\n\n"
                      + "\n".join(lines), encoding="utf-8")
    print("\n" + "\n".join(lines[:40]))
    if len(lines) > 40:
        print(f"... the rest is in {report}")
    print(f"\nSaved in {report}")
    return 0


# The app most makers' plugins install through (issue #79). Names only for apps we're sure of; everything else is
# "from each maker's website". Matched by the longest maker name in the vendor, like the maker links.
INSTALLER_APPS = {
    "native instruments": "Native Access", "izotope": "Native Access (or iZotope Product Portal)",
    "waves": "Waves Central", "arturia": "Arturia Software Center",
    "plugin alliance": "Plugin Alliance Installation Manager", "brainworx": "Plugin Alliance Installation Manager",
    "spitfire audio": "Spitfire Audio app", "uvi": "UVI Portal", "softube": "Softube Central",
    "slate digital": "Complete Access Hub", "universal audio": "UA Connect", "output": "Output Hub",
    "toontrack": "Toontrack Product Manager", "eastwest": "EW Installation Center", "ik multimedia": "IK Product Manager",
    "kilohearts": "Kilohearts Installer", "steinberg": "Steinberg Download Assistant",
    "roland": "Roland Cloud Manager", "image-line": "the FL Studio installer",
}
INSTALLER_LINKS = {"Native Access": "https://www.native-instruments.com/en/specials/native-access/",
                   "Waves Central": "https://www.waves.com/downloads/central"}
WEBSITE = "From each maker's website"


def installer_for(vendor):
    # "Native-instruments" (from a Mac bundle ID) is Native Instruments too: compare letters and digits only
    v = _plain(vendor or "")
    names = [name for name in INSTALLER_APPS if _plain(name) in v]
    return INSTALLER_APPS[max(names, key=len)] if names else WEBSITE


def group_by_installer(missing):
    """The missing plugins grouped by the app that installs them, most-used app first; the makers' own websites
    last. missing: plugin_readiness()["missing"]-style entries ({name, vendor, projects})."""
    groups = {}
    for e in missing:
        g = groups.setdefault(installer_for(e.get("vendor")), {"plugins": [], "projects": set()})
        g["plugins"].append(e["name"])
        g["projects"] |= set(e.get("projects") or [])
    out = [{"app": app, "link": INSTALLER_LINKS.get(app), "plugins": g["plugins"], "projects": len(g["projects"])}
           for app, g in groups.items()]
    return sorted(out, key=lambda g: (g["app"] == WEBSITE, -g["projects"], g["app"].lower()))


# --------------------------------------------------------------------------- plugins that copy (issue #80)

# Makers whose plugins need their own installer even though they have no installer app of their own: copy
# protection (iLok) or parts the installer puts elsewhere. Copying the plugin isn't enough, so they stay on the
# install list. Guessing wrong here only costs a download.
NEEDS_ITS_INSTALLER = ("spectrasonics", "refx", "soundtoys", "antares", "eventide", "mcdsp", "sonnox", "avid",
                       "celemony", "synchro arts", "pace anti-piracy")
COPY_FORMATS = {"VST2", "VST3", "CLAP", "AU"}   # AAX is Pro Tools only, and installs with iLok
PLUGIN_COPY_MAX = 2 * 1024 ** 3   # a "plugin" bigger than this holds its sounds: it installs from its maker
FORMAT_FOLDERS = {"components", "vst", "vst2", "vst3", "clap", "aax", "plug-ins", "plugins", "vstplugins", "64bit",
                  "64-bit", "x64"}   # a plugin folder's own sorting, never a maker's folder
SAFETY_FLAGS = ("Changed after its maker signed it", "Your virus scanner flagged it")   # issue #81
NOT_COPIED_FLAGS = SAFETY_FLAGS + ("No program file", "32-bit only")
SUPPORT_FILE_FLAG = "Doesn't look like a plugin"   # a support file next to plugins: it comes with its folder
_PLUGIN_SCAN = {}


def plugin_roots():
    """The folders this computer's plugins are installed in. With a stand-in home (tests, and a rehearsal's new
    computer), only the ones FREIGHTER_PLUGIN_ROOTS lists, so a test never sees or packs the real plugins."""
    if os.environ.get("FREIGHTER_HOME"):
        return [Path(p) for p in os.environ.get("FREIGHTER_PLUGIN_ROOTS", "").split(os.pathsep) if p]
    return [root for root, _exts in (rs.mac_roots() if rs.IS_MAC else rs.win_roots() if rs.IS_WIN else [])]


def installed_plugins(fresh=False):
    """This computer's plugins (rows like _Manifest's), kept for a minute so one move plan scans only once."""
    key = (os.environ.get("FREIGHTER_HOME"), os.environ.get("FREIGHTER_PLUGIN_ROOTS"))
    hit = _PLUGIN_SCAN.get(key)
    if hit and not fresh and time.time() - hit[0] < 60:
        return hit[1]
    try:
        rows = rs.scan(plugin_roots(), system=False) if os.environ.get("FREIGHTER_HOME") else rs.scan([])
    except Exception:   # never let the plugin scan stop a move
        rows = []
    _PLUGIN_SCAN.clear()
    _PLUGIN_SCAN[key] = (time.time(), rows)
    return rows


def plugin_copy_reason(row):
    """Why a plugin can't simply be copied to the new computer, or None when it can: it's only files, from a
    maker with no installer app, and nothing about it looks wrong (a broken signature means a cracked copy)."""
    if row.get("format") not in COPY_FORMATS:
        return "Pro Tools plugins install with their maker's installer"
    vendor = (row.get("vendor") or "").lower()
    app = installer_for(vendor)
    if app != WEBSITE:
        return f"installs through {app}"
    if any(_plain(m) in _plain(vendor) for m in NEEDS_ITS_INSTALLER):
        return "needs its maker's installer (copy protection, or parts it installs elsewhere)"
    flags = row.get("flags") or []
    for f in ([flags] if isinstance(flags, str) else flags):
        if f.startswith(NOT_COPIED_FLAGS):
            return f
    return None


def has_links(p):
    """Whether p is, or holds, a symbolic link. Freighter copies files, not links, so a plugin with links inside
    (frameworks in Mac plugins) would arrive broken: it installs from its maker instead."""
    if os.path.islink(p):
        return True
    for cur, dirs, files in os.walk(long_path(p)):
        if any(os.path.islink(os.path.join(cur, n)) for n in dirs + files):
            return True
    return False


def plugin_units(rows, roots=None, taken=()):
    """What to copy so the plugins that copy cleanly need no download on the new computer (issue #80).
    Returns ([(unit, [rows])], [(row, why it doesn't copy)]). A unit is the plugin itself (a file, or a bundle
    folder), or its maker's folder inside a plugin folder (VSTPlugins\\Xfer) when every plugin in that folder
    copies, so the support files next to them come too. A plugin inside a folder in `taken` is left to it."""
    fold = THIS_OS == "windows"
    nroots = sorted(((norm(str(r), fold), Path(r)) for r in (plugin_roots() if roots is None else roots)),
                    key=lambda x: -len(x[0]))
    taken = [norm(str(t), fold) for t in taken]

    def maker_folder(p):
        n = norm(str(p), fold)
        for nr, r in nroots:
            if n.startswith(nr + "/"):
                parts = Path(p).parts[len(r.parts):]
                i = 0
                while i < len(parts) - 1 and parts[i].lower() in FORMAT_FOLDERS:   # Plug-Ins/VST3/<maker>
                    i += 1
                return Path(r, *parts[:i + 1]) if i < len(parts) - 1 else None
        return None

    ok, no = [], []
    for row in rows:
        if any(str(f).startswith(SUPPORT_FILE_FLAG) for f in row.get("flags") or []):
            continue   # not a plugin: a file plugins next to it need, which comes when their folder does
        why = plugin_copy_reason(row)
        (no if why else ok).append((row, why))
    blocked = {norm(str(f), fold) for row, _ in no for f in [maker_folder(row["path"])] if f is not None}
    checked = {}

    def problem(unit):
        k = norm(str(unit), fold)
        if k not in checked:
            if has_links(unit):
                checked[k] = "has links inside, so it installs from its maker"
            else:
                size = file_size(unit) if os.path.isfile(long_path(unit)) else folder_bytes(unit, PLUGIN_COPY_MAX)
                checked[k] = ("it holds its sounds too, so it installs from its maker"
                              if (size or 0) > PLUGIN_COPY_MAX else None)
        return checked[k]

    units = {}
    for row, _ in ok:
        p = Path(row["path"])
        n = norm(str(p), fold)
        if any(n == t or n.startswith(t + "/") for t in taken):
            continue
        folder = maker_folder(p)
        unit = folder if folder is not None and norm(str(folder), fold) not in blocked and problem(folder) is None else p
        why = problem(unit)
        if why:
            no.append((row, why))
            continue
        units.setdefault(norm(str(unit), fold), (unit, []))[1].append(row)
    return list(units.values()), no


def plugin_items(skips=(), taken=(), report=None):
    """Pack items for the plugins that copy cleanly (issue #80): each bundle or maker folder is an item, and
    single-file plugins (a Windows .dll or .vst3) are one item per folder. report, if a dict, gets {copied,
    install}: what comes, and what installs from its maker on the new computer and why."""
    rows = [dict(r, flags=list(r.get("flags") or [])) for r in installed_plugins()]
    candidates = [r for r in rows if plugin_copy_reason(r) is None]
    if candidates:
        try:
            rs.apply_safety(candidates)   # signatures: files changed after signing are never carried over (#81)
        except Exception:
            pass
    units, no = plugin_units(rows, taken=taken)
    brief = lambda r: {"name": r["name"], "vendor": r.get("vendor", ""), "format": r.get("format", "")}
    items, loose = [], {}
    for unit, urows in units:
        if os.path.isfile(long_path(unit)):
            loose.setdefault(str(unit.parent), []).append((unit, urows))
            continue
        names = sorted({r["name"] for r in urows}, key=str.lower)
        label = (f"Plugin: {urows[0]['name']} ({urows[0]['format']})"
                 if len(urows) == 1 and norm(urows[0]["path"], True) == norm(str(unit), True)
                 else f"Plugins from {unit.name}: {', '.join(names)}")
        items.append({"id": item_id("vst", unit), "label": label, "source_path": str(unit), "kind": "extra",
                      "plugin_files": True, "plugins": [brief(r) for r in urows]})
    for folder, files in sorted(loose.items()):
        names = sorted({r["name"] for _u, urows in files for r in urows}, key=str.lower)
        items.append({"id": item_id("vst", folder), "label": f"Plugins: {', '.join(names)}",
                      "source_path": folder, "kind": "extra", "only": sorted(u.name for u, _r in files),
                      "plugin_files": True, "plugins": [brief(r) for _u, urows in files for r in urows]})
    items = [it for it in items if it["id"] not in skips]
    if report is not None:
        report["copied"] = [p for it in items for p in it["plugins"]]
        # one line per plugin, and none for a plugin that comes in another format (its Pro Tools copy, say)
        seen = {rs.match_key(p["name"]) for p in report["copied"]}
        report["install"] = []
        for row, why in no:
            k = rs.match_key(row["name"])
            if k not in seen:
                seen.add(k)
                report["install"].append(dict(brief(row), why=why, safety=why.startswith(SAFETY_FLAGS)))
    return items


def can_write(folder):
    """Whether Freighter can create files in folder (or, if it doesn't exist yet, the nearest folder above it)."""
    p = Path(folder)
    while not os.path.exists(long_path(p)):
        if p.parent == p:
            return False
        p = p.parent
    import tempfile
    try:
        fd, tmp = tempfile.mkstemp(dir=long_path(p), prefix=".freighter-check-")
        os.close(fd)
        os.remove(tmp)
        return True
    except OSError:
        return False


def plugin_destination(manifest, it, rehearsal_root, staging):
    """Where a copied plugin item goes on this computer (issue #80): (folder to unpack into, its plugin folder or
    None, how, the places where a copy already installed here counts). how is "in place" (the plugin folder it was in on the old computer), "user" (a Mac's own
    Plug-Ins folder, when the shared one needs an administrator), "admin" (a Windows plugin folder in Program
    Files: unpacked to `staging` first, then put in place with Windows' permission) or "waiting" (this
    computer has no such folder: it waits in `staging`)."""
    final = same_place(manifest, it["source_path"], rehearsal_root)
    if final is None:
        return staging, None, "waiting", []
    user = home() / str(final).lstrip("/") if THIS_OS == "mac" and str(final).startswith("/Library/") else None
    places = [final] + ([user] if user else [])   # a copy installed in either one counts as installed here
    if can_write(final) or ("only" not in it and any(os.path.exists(long_path(x)) for x in places)):
        return final, final, "in place", places
    if user is not None and can_write(user):
        return user, user, "user", places
    return staging, final, "admin" if THIS_OS == "windows" else "waiting", places


def run_as_admin(argv):
    """Windows: run argv as an administrator once the person says yes to Windows' prompt (User Account
    Control), and wait for it. Returns its exit code, or None when it didn't run (they said no)."""
    import ctypes
    from ctypes import wintypes

    class ShellExecuteInfo(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong), ("hwnd", wintypes.HWND),
                    ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                    ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
                    ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                    ("dwHotKey", wintypes.DWORD), ("hIconOrMonitor", wintypes.HANDLE), ("hProcess", wintypes.HANDLE)]
    info = ShellExecuteInfo(cbSize=ctypes.sizeof(ShellExecuteInfo), fMask=0x40,   # SEE_MASK_NOCLOSEPROCESS
                            lpVerb="runas", lpFile=argv[0], lpParameters=subprocess.list2cmdline(argv[1:]), nShow=0)
    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info)) or not info.hProcess:
        return None
    k32 = ctypes.windll.kernel32
    k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.WaitForSingleObject(info.hProcess, 0xFFFFFFFF)
    code = wintypes.DWORD()
    k32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
    k32.CloseHandle(info.hProcess)
    return code.value


def self_command(*args):
    """The command line that runs this Freighter again with args (the built program, or this script)."""
    if getattr(sys, "frozen", False):
        return [sys.executable, *args]
    return [sys.executable, str(Path(__file__).resolve()), *args]


def as_admin(job, folder):
    """Do a plugin-folder job as an administrator (Windows asks first): job is {"files": [[src, dst], ...]}
    to copy and/or {"remove": [[path, sha256], ...], "folders": [...]} to undo. Returns True if it ran."""
    plan = Path(folder) / "plugin-folders-job.json"
    plan.write_text(json.dumps(job), encoding="utf-8")
    try:
        return run_as_admin(self_command("plugin-folders", str(plan))) is not None
    except Exception:   # no Windows prompt available: the files simply stay where they are
        return False
    finally:
        plan.unlink(missing_ok=True)


def plugin_folders_job(plan_path):
    """What `freighter plugin-folders` does, as an administrator (issue #80): copy plugins into plugin folders,
    never over a file that's already there, or remove the ones an unpack put there (only if unchanged since).
    It only touches plugin folders (by place or by name, like C:\\VSTPlugins) and Program Files.
    Returns (copied, removed)."""
    job = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    fold = THIS_OS == "windows"
    allowed = [norm(str(r), fold) for r in plugin_roots()] + \
        [norm(os.environ[v], fold) for v in ("ProgramFiles", "ProgramFiles(x86)", "CommonProgramFiles")
         if os.environ.get(v)]
    ok = lambda p: any(norm(p, fold).startswith(a + "/") for a in allowed if a) or \
        any(part.lower() in FORMAT_FOLDERS for part in Path(p).parts[:-1])   # e.g. C:\VSTPlugins
    copied = removed = 0
    for src, dst in job.get("files", []):
        if ok(dst) and not os.path.exists(long_path(dst)):
            try:
                copy_and_hash(src, dst)
                copied += 1
            except OSError:
                pass
    for path, sha in job.get("remove", []):
        try:
            if ok(path) and hash_file(path) == sha:
                os.remove(long_path(path))
                removed += 1
        except OSError:
            pass
    for d in sorted(job.get("folders", []), key=len, reverse=True):
        if ok(d):
            try:
                os.rmdir(long_path(d))
            except OSError:
                pass
    return copied, removed


def cmd_plugin_folders(args):
    copied, removed = plugin_folders_job(args.plan)
    print(f"Copied {copied} plugin files, removed {removed}.")
    return 0


def old_computer_plugins():
    """What unpacks left for the Plugins to install page (issue #82): the old computers' plugin lists (rows like
    _Manifest's CSV), the plugins Freighter copied over, and those it left out for safety ({name, vendor, threat
    or why})."""
    base = home() / "Music" / "Freighter"
    rows, copied, unsafe = [], [], []
    for f in sorted(base.glob("*/plugins-*.csv")) if base.is_dir() else []:
        try:
            with open(f, newline="", encoding="utf-8") as fh:
                rows += list(csv.DictReader(fh))
        except (OSError, csv.Error):
            pass
    for f in sorted(base.glob("*/plugins-copied.json")) if base.is_dir() else []:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        copied += data.get("placed", [])
        unsafe += [{"name": x.get("name", ""), "vendor": x.get("vendor", ""),
                    "why": f"your virus scanner flagged it ({x.get('threat')})"} for x in data.get("flagged", [])]
    for f in sorted(base.glob("*/plugins-not-copied.txt")) if base.is_dir() else []:
        try:
            lines = f.read_text(encoding="utf-8").split("\n\n", 2)[-1].splitlines()
        except OSError:
            continue
        for line in lines:   # "Name (VST3): reason" or "Name (VST3), Other (AU): reason"
            names, _, why = line.partition("): ")
            for n in names.split("), "):
                if n.strip():
                    unsafe.append({"name": n.rsplit(" (", 1)[0].strip(), "vendor": "", "why": why.strip()})
    return rows, copied, unsafe


def readiness_lines(r):
    lines = [f"{len(r['ready'])} of {r['total']} projects have every plugin they use."]
    if r["missing"]:
        lines += ["", "Install by app (most of a maker's plugins come back through one app):"]
        for g in group_by_installer(r["missing"]):
            lines.append(f"  {g['app']}: {', '.join(g['plugins'])} (used in {g['projects']} "
                         f"project{'s' if g['projects'] != 1 else ''})")
        lines += ["", "Plugins to install, most useful first:"]
        opened = len(r["ready"])
        for e in r["missing"]:
            opened += e["opens"]
            who = f" by {e['vendor']}" if e["vendor"] else ""
            n = len(e["projects"])
            lines.append(f"  {e['name']}{who}: used in {n} project{'s' if n != 1 else ''}"
                         + (f" -> {opened} of {r['total']} open" if e["opens"] else ""))
    if r["versions"]:
        lines += ["", "DAW versions your projects need (the newest one that saved them):"]
        lines += [f"  {daw} {v} or newer (e.g. {name})" for daw, (v, name) in sorted(r["versions"].items())]
    if r["warnings"]:
        lines += ["", "Watch out:"] + [f"  {w}" for w in r["warnings"]]
    return lines


def cmd_plugins(args):
    """Which plugins each project needs, and which of them this computer is missing (issue #70)."""
    maps = load_pathmaps(None)
    roots = project_search_roots(maps, args.folder)
    print("Reading your projects...", flush=True)
    projects = {str(p): project_plugins(p) for p in all_projects(roots)}
    print("Scanning installed plugins...", flush=True)
    r = plugin_readiness(projects, rs.scan(list(args.extra_dir)))
    lines = [f"Freighter {__version__} plugin readiness, {dt.datetime.now():%Y-%m-%d %H:%M}", ""] + readiness_lines(r)
    if r["blocked"]:
        lines += ["", "Projects still waiting for plugins:"]
        for path, need in sorted(r["blocked"].items()):
            names = sorted(e["name"] for e in r["missing"] if path in e["projects"])
            lines.append(f"  {Path(path).name}: {', '.join(names)}")
    print("\n" + "\n".join(lines[2:]))
    out = home() / "Music" / "Freighter" / "Reports"
    out.mkdir(parents=True, exist_ok=True)
    (out / "plugin-readiness.txt").write_text("\n".join(lines), encoding="utf-8")
    print(f"\nFull list: {out / 'plugin-readiness.txt'}")
    return 0


def cmd_check_fl(args):
    """Report only: what fix-fl would repoint, and what's missing."""
    args.dry_run = True
    return cmd_fix_fl(args)


# --------------------------------------------------------------------------- scan

def cmd_scan(args):
    h = home()
    roots = all_user_folders(h)
    rows = []
    print("Looking for projects...", flush=True)
    found = all_projects(roots)
    for p in (p for p in found if p.suffix.lower() == ".flp"):
        try:
            _v, samples = flp_samples(p)
            refs = sum(1 for s in set(samples) if not FL_PLACEHOLDER.match(s))
        except (ValueError, OSError, struct.error):
            refs = None
        rows.append(("FL Studio", p, refs))
    for p in (p for p in found if p.suffix.lower() == ".als"):
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


def repaired_paths(p):
    """{path as the original project stored it: path the repaired project uses now} for a project a repair
    rewrote (empty if it wasn't), so a rehearsal can check what the DAW will actually load."""
    src = backup_path(p)
    if not src.is_file():
        return {}
    try:
        if p.suffix.lower() == ".flp":
            before, after = flp_samples(src)[1], flp_samples(p)[1]
        else:
            before, after = (als_sample_refs(read_als(x)[0], unique=False) for x in (src, p))
    except (ValueError, OSError, struct.error):
        return {}
    return dict(zip(before, after)) if len(before) == len(after) else {}


def project_refs(p):
    """(kind, [referenced file paths as the project stored them]) or (kind, None) if unreadable."""
    src = backup_path(p)  # the original, if a repair rewrote it
    if p.suffix.lower() == ".flp":
        try:
            # FL's own sounds (%FLStudioFactoryData%) come with FL; the user's data (%FLStudioUserData%) is checked
            return "FL Studio", [s for s in dict.fromkeys(flp_samples(src if src.is_file() else p)[1])
                                 if FL_USER_DATA.match(s) or not FL_PLACEHOLDER.match(s)]
        except (ValueError, OSError, struct.error):
            return "FL Studio", None
    try:
        text, _ = read_als(src if src.is_file() else p)
    except Exception:
        return "Ableton", None
    if not als_new_format(text):
        return "Ableton (Live 10 or older)", None
    return "Ableton", als_sample_refs(text)


REHEARSAL_NAMES = ("Rehearsal", "Freighter Rehearsal")


def rehearsal_folder(to=None):
    """Where a rehearsal goes. Always a folder of its own: --to E:\\ must not turn the whole drive into
    "a rehearsal" (issue #62)."""
    return (Path(to).expanduser() / REHEARSAL_NAMES[1] if to else home() / "Music" / "Freighter" / REHEARSAL_NAMES[0]).resolve()


def is_rehearsal(folder):
    """Only a folder Freighter made for a rehearsal: its name, its marker, and never a drive's top folder."""
    f = Path(folder)
    return f.name in REHEARSAL_NAMES and f != Path(f.anchor) and (f / REHEARSAL_MARKER).is_file()


def remove_rehearsal(folder):
    """Delete a rehearsal's files (copies only: the real files were never touched). Returns the bytes freed."""
    f = Path(folder).expanduser().resolve()
    if not is_rehearsal(f):
        sys.exit(f"{f} isn't a Freighter rehearsal folder, so Freighter won't delete it.")
    freed = 0
    for root, _dirs, files in os.walk(long_path(f)):
        for n in files:
            try:
                freed += os.lstat(os.path.join(root, n)).st_size
            except OSError:
                pass

    def retry(fn, path, _exc):   # Windows won't delete read-only files until they're made writable
        os.chmod(path, 0o666 if os.path.isfile(path) else 0o777)
        fn(path)
    if sys.version_info >= (3, 12):
        shutil.rmtree(long_path(f), onexc=retry)
    else:
        shutil.rmtree(long_path(f), onerror=retry)
    return freed


def cmd_rehearse(args):
    """A full move on one computer: pack, unpack into a stand-in 'new computer' folder, repair,
    then check every project's samples. Your real folders are only read, never changed."""
    global HIDE
    base = rehearsal_folder(args.to)
    if getattr(args, "clean", False):
        freed = remove_rehearsal(base)
        print(f"Deleted the rehearsal in {base} ({rs.human_size(freed)} freed). Your real files weren't touched.")
        return 0
    LAST["rehearse_folder"] = str(base)   # known even if the rehearsal stops part-way, so it can be cleaned up
    drive, new_home = base / "drive", base / "new-computer"
    real_home = home()
    rng = range_from_args(args)
    items = pack_items(args.include, set(args.skip), rng=rng)
    need = sum(size for it in items
               for _r, _a, size in ([(n, None, (Path(it["source_path"]) / n).stat().st_size)
                                     for n in it["only"]] if "only" in it else
                                    walk_files(it["source_path"], None if it.get("plugin_files") else [])))
    base.mkdir(parents=True, exist_ok=True)
    (base / REHEARSAL_MARKER).write_text("A Freighter rehearsal. Freighter never packs this folder.\n", encoding="utf-8")
    free = shutil.disk_usage(base).free
    print(f"Rehearsal needs about {rs.human_size(need * 2)} (a bundle plus an unpacked copy); "
          f"{rs.human_size(free)} free here.")
    if need * 2 > free:
        sys.exit("Not enough space. Leave big items out with --skip ID, or use --to on a bigger drive.")

    print("\n== 1/4  Pack, as the old computer")
    cmd_pack(argparse.Namespace(to=str(drive), list=False, include=args.include, skip=args.skip,
                                include_programs=False, since=getattr(args, "since", None),
                                until=getattr(args, "until", None)))
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
        cmd_unpack(argparse.Namespace(bundle=str(bundles[-1]), dest_for=[], rehearsal_root=str(new_home)))
        print("\n== 3/4  Repair projects")
        cmd_fix_ableton(argparse.Namespace(pathmap=None, folder=[], dry_run=False))
        cmd_fix_fl(argparse.Namespace(pathmap=None, folder=[], include_backups=False, dry_run=False, copies_only=True))

        print("\n== 4/4  Check every project on the new computer")
        maps = load_pathmaps(None)
        roots = project_search_roots(maps, [])
        totals = {"kept": 0, "lost": 0, "already": 0, "projects": 0, "unchecked": 0}
        lost, unchecked, already = [], [], []
        for p in all_projects(roots, racks=True):
            kind, refs = project_refs(p)
            if refs is None:
                totals["unchecked"] += 1
                unchecked.append(f"{p}  ({kind})")
                continue
            totals["projects"] += 1
            now = repaired_paths(p)
            for r in refs:
                if in_program_place(r):
                    continue                     # comes with an installed program, not part of the move
                if FL_USER_DATA.match(r):        # FL's own user-data folder: compare old home with new home
                    rest = FL_USER_DATA.sub("", r).replace("\\", "/").lstrip("/")
                    original = fl_user_data(real_home).joinpath(*rest.split("/"))
                    moved = fl_user_data(new_home).joinpath(*rest.split("/"))
                    moved = moved if moved.exists() else None
                elif now.get(r, r) != r:         # the repair rewrote it: check the path the project uses now
                    original = Path(r)
                    moved = Path(now[r]) if os.path.exists(long_path(now[r])) else None
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
        old_live = sum(1 for u in unchecked if u.endswith("(Ableton (Live 10 or older))"))
        not_checked = "" if not totals["unchecked"] else (
            f" (+{totals['unchecked']} Ableton files saved by Live 10 or older, often demo sets and racks that came "
            "with sample packs: Freighter can't read their sample paths yet)" if old_live == totals["unchecked"]
            else f" (+{totals['unchecked']} that can't be checked automatically)")
        lines = [
            f"Freighter {__version__} rehearsal, {dt.datetime.now():%Y-%m-%d %H:%M}",
            f"Projects checked: {totals['projects']}{not_checked}",
            f"Samples that worked before the move: {checked}",
            f"  still found after the move: {totals['kept']}",
            f"  LOST in the move: {totals['lost']}",
            f"Samples already missing before the move (not Freighter's fault): {totals['already']}",
        ]
        plugs = (LAST.get("unpack") or {}).get("plugins") or {}
        copied = sum(len(r["names"]) for r in plugs.get("placed", []))
        if copied:   # issue #80
            lines.append(f"Plugins copied into their plugin folders (here, inside the rehearsal): {copied}")
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
        LAST["rehearse"] = {"passed": not totals["lost"], "totals": totals, "lost": lost, "already": already,
                            "plugins": copied,
                            "unchecked": unchecked, "old_live": old_live, "report": str(report), "folder": str(base)}
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
    7  Check my projects: missing samples, and plugins to install

  Tidy up
    8  Sort my sample folders into one library (_Crate)

    9  Open the Freighter app in your web browser (new)

    u  Check for a newer Freighter     r  Report a problem
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


REPO = "chasubowl-creator/freighter"


def version_label():
    """'0.9.0-test.2' for a release build, '0.9.0 (build 6e6a9bd)' for another build, '0.9.0 (from source)'."""
    if BUILD and BUILD.startswith("v"):
        return BUILD[1:]
    if BUILD:
        return f"{__version__} (build {BUILD.split('@')[-1]})"
    return f"{__version__} (from source)"


def system_label():
    """'Windows 11', 'macOS 15.6.1': enough to reproduce a problem, nothing personal."""
    import platform
    if rs.IS_MAC:
        v = platform.mac_ver()[0]
        return f"macOS {v}" if v else "macOS"
    if rs.IS_WIN:
        try:
            build = int(platform.version().split(".")[-1])
        except ValueError:
            build = 0
        return ("Windows 11" if build >= 22000 else f"Windows {platform.release()}") + (f" (build {build})" if build else "")
    return f"{platform.system()} {platform.release()}"


def report_url():
    """GitHub's "Report a problem" form, with the version and kind of computer filled in (issue #41). Nothing else
    goes in the link: no names, folders or files."""
    from urllib.parse import urlencode
    return f"https://github.com/{REPO}/issues/new?" + urlencode(
        {"template": "problem.yml", "version": version_label(), "system": system_label()})


def parse_version(v):
    """'v0.9.0-test.2' -> (0, 9, 0, 0, 2). A final release sorts after its test builds: '0.9.0' -> (0, 9, 0, 1, 0)."""
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)(?:-[A-Za-z]+\.?(\d+))?", (v or "").strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3)), 0 if m.group(4) else 1, int(m.group(4) or 0)


def fetch_releases():
    """Freighter's releases from GitHub. Sends nothing about the person or their files; a plain program name as
    the User-Agent. Falls back to curl where Python can't check GitHub's certificate (some Mac builds)."""
    import ssl
    import urllib.error
    import urllib.request
    url = f"https://api.github.com/repos/{REPO}/releases?per_page=30"
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "Freighter-update-check"}
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=8) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.URLError as e:
        if not isinstance(getattr(e, "reason", None), ssl.SSLError):
            raise
    out = subprocess.run(["curl", "-fsSL", "--max-time", "8", "-H", "User-Agent: Freighter-update-check", url],
                         capture_output=True, timeout=15)
    if out.returncode:
        raise OSError("curl couldn't reach GitHub")
    return json.loads(out.stdout.decode("utf-8"))


def check_for_update(fetch=None):
    """Whether a newer Freighter is out (issue #40). Only ever runs when someone asks. People on a final release
    are offered final releases; test builds and the source code see test builds too."""
    current = BUILD[1:] if BUILD and BUILD.startswith("v") else __version__
    out = {"current": version_label(), "latest": None, "url": f"https://github.com/{REPO}/releases",
           "newer": False, "release_build": bool(BUILD and BUILD.startswith("v"))}
    try:
        releases = (fetch or fetch_releases)()
    except Exception:
        out["error"] = "Couldn't reach GitHub. Check the internet connection and try again."
        return out
    mine = parse_version(current)
    found = [(parse_version(r.get("tag_name")), r) for r in releases if isinstance(r, dict) and not r.get("draft")]
    found = [(k, r) for k, r in found if k]
    if mine and mine[3] == 1 and out["release_build"]:
        found = [(k, r) for k, r in found if not r.get("prerelease")]
    if found:
        key, newest = max(found, key=lambda kr: kr[0])
        out.update(latest=newest["tag_name"].lstrip("v"), url=newest.get("html_url") or out["url"],
                   newer=mine is None or key > mine)
    return out


def cmd_selftest(args):
    """Check this copy of Freighter works as built (issue #83): its bundled photo and fonts load, the app page
    renders, and a small move with a plugin works end to end in a stand-in home (pack, unpack, repair, undo).
    --program-files also puts a plugin into Program Files for real (the Windows build check, which runs as an
    administrator). The real home folder is never touched."""
    import gzip as gz
    import tempfile
    from urllib.request import urlopen
    import app
    problems = []

    def check(ok, what):
        print(("ok    " if ok else "FAIL  ") + what, flush=True)
        if not ok:
            problems.append(what)

    print("== The look: bundled photo, fonts, page and plugin catalog")
    for name in app.ASSETS:
        data = app.asset(name) or b""
        check(data[:4] == b"wOF2" or data[:3] == b"\xff\xd8\xff", f"bundled {name} ({len(data):,} bytes)")
    check(len(app.pcat.BRANDS) >= 20, f"plugin catalog: {len(app.pcat.BRANDS)} brands")
    a, server, url = app.serve()
    try:
        page = urlopen(url, timeout=20).read().decode("utf-8")
        check("freighter.exe" in page and "{{version}}" not in page, "the app page renders")
        r = urlopen(f"http://127.0.0.1:{a.port}/assets/fonts/sora-800.woff2", timeout=20)
        check(r.status == 200 and r.headers.get("Content-Type") == "font/woff2", "the app serves its fonts")
    finally:
        server.shutdown()
        server.server_close()

    print("\n== A small move with a plugin, in a stand-in home")
    tmp = Path(tempfile.mkdtemp(prefix="freighter-selftest-"))
    prev = {k: os.environ.get(k) for k in ("FREIGHTER_HOME", "FREIGHTER_PLUGIN_ROOTS")}
    pf_root = None
    try:
        old, new, drive = tmp / "old", tmp / "new", tmp / "drive"
        kick = old / "Desktop" / "Selftest Kit" / "kick.wav"
        kick.parent.mkdir(parents=True)
        kick_bytes = b"RIFF" + os.urandom(4000)
        kick.write_bytes(kick_bytes)
        song = old / "Desktop" / "Selftest Project" / "Song.als"
        song.parent.mkdir(parents=True)
        song.write_bytes(gz.compress(('<?xml version="1.0" encoding="UTF-8"?>\n<Ableton Creator="Ableton Live 12.1">'
                                      '<SampleRef>\n<FileRef>\n<RelativePathType Value="0" />\n<RelativePath Value="" />\n'
                                      f'<Path Value="{html.escape(str(kick))}" />\n<Type Value="1" />\n'
                                      '<LivePackName Value="" />\n</FileRef>\n</SampleRef></Ableton>').encode("utf-8")))

        def fake_plugin(root):
            if THIS_OS == "windows":   # a VST3 can be a single file on Windows
                f = root / "Selftest Synth.vst3"
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_bytes(os.urandom(6000))
                return f
            b = root / "Selftest Synth.vst3"
            (b / "Contents" / "MacOS").mkdir(parents=True)
            (b / "Contents" / "Info.plist").write_bytes(
                b'<?xml version="1.0" encoding="UTF-8"?><plist version="1.0"><dict><key>CFBundleName</key>'
                b'<string>Selftest Synth</string><key>CFBundleIdentifier</key><string>dev.selftest.synth</string>'
                b'</dict></plist>')
            exe = b / "Contents" / "MacOS" / "Selftest Synth"
            exe.write_bytes(os.urandom(6000))
            os.chmod(exe, 0o755)
            return b
        roots = [old / "Plugins" / "VST3"]
        plugin = fake_plugin(roots[0])
        if getattr(args, "program_files", False) and THIS_OS == "windows":
            pf_root = Path(os.environ.get("CommonProgramFiles") or r"C:\Program Files\Common Files") / \
                "Freighter Selftest" / "VST3"
            fake_plugin(pf_root)
            roots.append(pf_root)
        os.environ["FREIGHTER_HOME"], os.environ["FREIGHTER_PLUGIN_ROOTS"] = str(old), os.pathsep.join(map(str, roots))
        _PLUGIN_SCAN.clear()

        t = time.time()
        r = rs.scan_files([plugin])
        took = time.time() - t
        print(f"      virus scan: {r['scanner'] or 'none'} took {took:.1f} s for one plugin ({r['note']})", flush=True)
        if r["scanner"]:
            check(not r["hits"] and not r["errors"], "the virus scanner checked a plugin")

        cmd_pack(argparse.Namespace(to=str(drive), list=False, include=[], skip=[], include_programs=False,
                                    since=None, until=None))
        bundle = next(drive.glob("Freighter-*"), None)
        check(bundle is not None and (bundle / COMPLETE).is_file(), "packed a finished bundle")
        items = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))["items"] if bundle else []
        check(sum(1 for it in items if it.get("plugin_files")) == len(roots), f"packed {len(roots)} copied plugin item(s)")
        shutil.rmtree(old)   # the old computer is gone: the project must now find the moved sample
        if pf_root:
            shutil.rmtree(pf_root)   # gone from Program Files too, as on a new computer

        os.environ["FREIGHTER_HOME"] = str(new)
        new.mkdir()
        cmd_unpack(argparse.Namespace(bundle=str(bundle), dest_for=[]))
        placed = (LAST.get("unpack") or {}).get("plugins", {}).get("placed", [])
        here = new / "Desktop" / "Selftest Kit" / "kick.wav"
        check(here.is_file() and here.read_bytes() == kick_bytes, "the sample arrived, identical")
        check((new / "Plugins" / "VST3" / plugin.name).exists(), "the plugin went back in its plugin folder")
        if pf_root:
            check((pf_root / plugin.name).is_file(), f"the plugin went back into Program Files ({pf_root})")
        check(len(placed) == len(roots), f"{len(placed)} plugin item(s) reported as placed")
        repair_projects()
        moved_song = new / "Desktop" / "Selftest Project" / "Song.als"
        text = gz.decompress(moved_song.read_bytes()).decode("utf-8") if moved_song.is_file() else ""
        check(html.escape(str(here)) in text, "the project was repaired to the moved sample")
        r = undo_unpack()
        check(not (new / "Plugins" / "VST3" / plugin.name).exists() and not here.exists(), "undo removed what it put there")
        if pf_root:
            check(not (pf_root / plugin.name).exists(), "undo removed the plugin from Program Files")
    except Exception as e:   # a crash is a failed check, with its reason
        check(False, f"the small move stopped: {e.__class__.__name__}: {e}")
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _PLUGIN_SCAN.clear()
        shutil.rmtree(tmp, ignore_errors=True)
        if pf_root:
            shutil.rmtree(pf_root.parent, ignore_errors=True)
    print("\n" + (f"Self-check FAILED: {len(problems)} problem(s)." if problems else "Self-check passed."))
    return 1 if problems else 0


def cmd_check_update(args):
    r = check_for_update()
    if r.get("error"):
        print(r["error"])
        return 1
    if r["newer"]:
        print(f"Freighter {r['latest']} is out (you have {r['current']}). Download it from:\n  {r['url']}")
    elif r["latest"]:
        print(f"You have Freighter {r['current']}; the newest release is {r['latest']}.")
    else:
        print("No releases found yet.")
    return 0


def write_crash_report(choice):
    """Save what went wrong, so a tester can send it in. Returns the file's path."""
    import platform
    import tempfile
    import traceback
    text = "\n".join([
        f"Freighter {__version__} crash report, {dt.datetime.now():%Y-%m-%d %H:%M:%S}",
        f"Build: {version_label()}",
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
        print(MENU.format(version=version_label()))
        try:
            choice = input("  Choose 1-9, u, r or q: ").strip().lower()
        except EOFError:   # nothing more to read (input closed): same as quitting
            return 0
        try:
            if choice == "u":
                cmd_check_update(None)
            elif choice == "r":
                import webbrowser
                print("  Opening the problem report form in your web browser...")
                print(f"  {report_url()}")
                webbrowser.open(report_url())
            elif choice == "1":
                html_path, _ = rs.run_scan()
                import webbrowser
                webbrowser.open(html_path.as_uri())
            elif choice == "2":
                since, until = ask_range()
                cmd_pack(argparse.Namespace(to=None, list=True, include=[], skip=[], include_programs=False,
                                            since=since, until=until))
            elif choice == "3":
                since, until = ask_range()
                cmd_rehearse(argparse.Namespace(to=None, include=[], skip=[], since=since, until=until))
            elif choice == "4":
                drive = ask_path("  Drag the drive (or a folder on it) into this window, then press Enter: ")
                if drive:
                    since, until = ask_range()
                    code = cmd_pack(argparse.Namespace(to=drive, list=False, include=[], skip=[],
                                                       include_programs=False, since=since, until=until))
                    if code == 0 and LAST.get("pack") and input(
                            "\n  Check the drive now? It reads everything back to catch a bad drive or cable while "
                            "this\n  computer still has the originals. For the best check, unplug the drive and plug it "
                            "back in first. (y/n): ").strip().lower().startswith("y"):
                        cmd_verify(argparse.Namespace(bundle=LAST["pack"]["bundle"]))
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
                cmd_fix_fl(argparse.Namespace(pathmap=None, folder=[], include_backups=False, dry_run=False))
            elif choice == "7":
                cmd_health(None)
                cmd_plugins(argparse.Namespace(folder=[], extra_dir=[]))
            elif choice == "8":
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
            elif choice == "9":
                import app
                app.run()
            elif choice in ("q", "quit", "exit", ""):
                return 0
            else:
                print("  Type a number from 1 to 9, or q to quit.")
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
                print("  Please report it, and drag that file into the form:")
                print(f"  {report_url()}")
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
    add_range_args(s)
    s.set_defaults(fn=cmd_pack)

    s = sub.add_parser("unpack", help="put a bundle's files in place on this computer")
    s.add_argument("bundle")
    s.add_argument("--anyway", action="store_true",
                   help="unpack what's there even if packing the bundle wasn't finished")
    s.add_argument("--preview", action="store_true", help="only show where everything would go")
    s.add_argument("--keep-together", action="store_true",
                   help="put moved folders in Music/Freighter/<old computer> even on a same-OS move")
    s.add_argument("--dest-for", action="append", default=[], metavar="ID=FOLDER",
                   help="put one item somewhere else, e.g. used-3fa2b1c9=/Volumes/Samples (IDs are shown by pack --list)")
    s.set_defaults(fn=cmd_unpack)

    s = sub.add_parser("fix-ableton", help="repoint samples in Ableton projects (Live 11+)")
    s.add_argument("--pathmap", metavar="FILE")
    s.add_argument("--folder", action="append", default=[], metavar="FOLDER", help="only look here")
    s.add_argument("--dry-run", action="store_true", help="report only, change nothing")
    s.set_defaults(fn=cmd_fix_ableton)

    s = sub.add_parser("fix-fl", help="repoint FL Studio projects at moved samples (keeps a backup)")
    s.add_argument("--pathmap", metavar="FILE")
    s.add_argument("--folder", action="append", default=[], metavar="FOLDER", help="only look here")
    s.add_argument("--include-backups", action="store_true", help="also repair autosave/backup projects")
    s.add_argument("--dry-run", action="store_true", help="report only, change nothing")
    s.set_defaults(fn=cmd_fix_fl)

    s = sub.add_parser("undo-unpack", help="remove what unpacking created and put repaired projects back")
    s.add_argument("--from", dest="source", metavar="COMPUTER", help="only the unpack from this computer")
    s.set_defaults(fn=cmd_undo_unpack)

    s = sub.add_parser("plugins", help="which plugins your projects need, and which this computer is missing")
    s.add_argument("--folder", action="append", default=[], metavar="FOLDER", help="only look here")
    s.add_argument("--extra-dir", action="append", default=[], metavar="FOLDER", help="also scan this plugin folder")
    s.set_defaults(fn=cmd_plugins)

    s = sub.add_parser("check-fl", help="report moved and missing samples in FL Studio projects (changes nothing)")
    s.add_argument("--pathmap", metavar="FILE")
    s.add_argument("--folder", action="append", default=[], metavar="FOLDER", help="only look here")
    s.add_argument("--include-backups", action="store_true", help="also check autosave/backup projects")
    s.set_defaults(fn=cmd_check_fl)

    s = sub.add_parser("rehearse", help="practice the whole move on this computer, safely")
    s.add_argument("--to", metavar="FOLDER", help="a drive or folder to rehearse on; Freighter makes a 'Freighter Rehearsal' folder there (default: Music/Freighter/Rehearsal)")
    s.add_argument("--include", action="append", default=[], metavar="FOLDER")
    s.add_argument("--skip", action="append", default=[], metavar="ID")
    s.add_argument("--clean", action="store_true", help="delete an earlier rehearsal's files (with the same --to)")
    add_range_args(s)
    s.set_defaults(fn=cmd_rehearse)

    s = sub.add_parser("health", help="check every project: samples found, plugins installed")
    s.set_defaults(fn=cmd_health)

    s = sub.add_parser("verify", help="check the drive: read every packed file back and compare it")
    s.add_argument("bundle", metavar="DRIVE_OR_BUNDLE")
    s.set_defaults(fn=cmd_verify)

    s = sub.add_parser("selftest", help="check this copy of Freighter works (its build runs this)")
    s.add_argument("--program-files", action="store_true",
                   help="Windows, as administrator: also put a test plugin into Program Files and take it out again")
    s.set_defaults(fn=cmd_selftest)

    s = sub.add_parser("check-update", help="ask GitHub whether a newer Freighter is out")
    s.set_defaults(fn=cmd_check_update)

    s = sub.add_parser("plugin-folders", help="used by unpack, as an administrator, to put plugins in Program Files")
    s.add_argument("plan")
    s.set_defaults(fn=cmd_plugin_folders)

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
