#!/usr/bin/env python3
"""
Rig Mover - move a music production setup between computers (Windows <-> Mac, or same OS).

Plugins move as a list (reinstall them from each maker). Your own files - projects,
samples, presets - move as files, via a bundle folder on an external drive.

  OLD computer:
    python3 rig_mover.py scan                         rig report + list of your projects
    python3 rig_mover.py pack --list                  show what can be moved, with sizes
    python3 rig_mover.py pack --to /Volumes/DRIVE     copy it to a bundle on the drive
        options: --include FOLDER (extra folders, e.g. sample libraries), --skip ID

  NEW computer:
    python3 rig_mover.py unpack /Volumes/DRIVE/RigMover-PC-2026-11-12
    python3 rig_mover.py fix-ableton                  repoint samples in Ableton projects
    python3 rig_mover.py check-fl                     find moved/missing samples in FL projects
    python3 rig_scanner.py --compare BUNDLE/plugins.csv   plugins still to reinstall

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
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rig_scanner as rs  # noqa: E402

THIS_OS = "mac" if rs.IS_MAC else "windows" if rs.IS_WIN else "other"
BUNDLE_VERSION = 1
JUNK_FILES = {".ds_store", "thumbs.db", "desktop.ini", "icon\r"}
SKIP_DIRS = {".trash", "$recycle.bin", "rig mover backup"}


def home():
    """Home folder. RIGMOVER_HOME overrides it (used for testing)."""
    return Path(os.environ.get("RIGMOVER_HOME") or Path.home())


# --------------------------------------------------------------------------- known folders

def known_roots(os_name, h):
    """Standard places producers keep their own files, by logical id, for one OS."""
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
    return roots


def moved_root(source_host):
    return home() / "Music" / "Rig Mover" / source_host


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
    """Windows needs a \\\\?\\ prefix for paths over 260 characters."""
    p = Path(p)
    if rs.IS_WIN:
        s = str(p.resolve())
        if not s.startswith("\\\\?\\"):
            s = "\\\\?\\" + s
        return s
    return str(p)


def copy_and_hash(src, dst):
    """Copy src to dst through a temporary name; return the SHA-256 of what was copied."""
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".rigmover-part")
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


def walk_files(root):
    """Yield (relative posix path, absolute path, size) for every real file under root."""
    root = Path(root)
    for cur, dirs, files in os.walk(root, onerror=lambda e: None):
        dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIRS and not os.path.islink(os.path.join(cur, d))]
        for fn in files:
            if fn.lower() in JUNK_FILES or fn.endswith(".rigmover-part"):
                continue
            ap = Path(cur) / fn
            if ap.is_symlink():
                continue
            try:
                size = ap.stat().st_size
            except OSError:
                continue
            yield ap.relative_to(root).as_posix(), ap, size


class Progress:
    def __init__(self, total_bytes, label):
        self.total, self.done, self.label, self.last, self.start = total_bytes, 0, label, 0.0, time.time()

    def add(self, n):
        self.done += n
        now = time.time()
        if now - self.last > 2 or self.done >= self.total:
            self.last = now
            pct = 100 * self.done / self.total if self.total else 100
            rate = self.done / max(now - self.start, 0.001)
            print(f"  {self.label}: {pct:5.1f}%  {rs.human_size(self.done)} of {rs.human_size(self.total)}"
                  f"  ({rs.human_size(rate)}/s)", flush=True)


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

def pack_items(includes, skips):
    items = []
    for rid, (label, path) in known_roots(THIS_OS, home()).items():
        if path.is_dir() and rid not in skips:
            items.append({"id": rid, "label": label, "source_path": str(path), "kind": "known"})
    for i, inc in enumerate(includes, 1):
        p = Path(inc).expanduser().resolve()
        if not p.is_dir():
            sys.exit(f"Not a folder: {inc}")
        iid = f"extra{i}"
        if iid not in skips:
            items.append({"id": iid, "label": p.name, "source_path": str(p), "kind": "extra"})
    folders, loose = project_sample_folders(items)
    for i, folder in enumerate(folders, 1):
        iid = f"used{i}"
        if iid not in skips:
            items.append({"id": iid, "label": f"Samples your projects use: {folder.name}",
                          "source_path": str(folder), "kind": "extra"})
    for i, (folder, files) in enumerate(sorted(loose.items()), 1):
        iid = f"loose{i}"
        if iid not in skips:
            items.append({"id": iid, "label": f"Loose samples your projects use from {Path(folder).name}",
                          "source_path": folder, "kind": "extra", "only": sorted(files)})
    return items


def sample_folder_for(sample, h):
    """The folder to move for one referenced sample: two levels under home
    (e.g. ~/Desktop/DrumKits), or three levels from the root elsewhere (e.g. /Volumes/SSD/Samples)."""
    p = Path(sample)
    try:
        rel = p.relative_to(h)
        parts = rel.parts[:2] if len(rel.parts) > 2 else rel.parts[:1]
        return h.joinpath(*parts) if parts else None
    except ValueError:
        parts = p.parts[:4] if rs.IS_MAC else p.parts[:3]
        return Path(*parts) if len(parts) > 1 else None


def project_sample_folders(items):
    """Folders outside the packed items that FL/Ableton projects pull samples from."""
    h = home()
    covered = [Path(it["source_path"]) for it in items]
    roots = [r for r in (h / "Music", h / "Documents", h / "Desktop") if r.is_dir()]
    roots += [c for c in covered if c.is_dir()]
    refs = set()
    for p in find_projects(roots, ".flp", include_backups=True):
        try:
            refs.update(s for s in flp_samples(p)[1] if not FL_PLACEHOLDER.match(s))
        except (ValueError, OSError, struct.error):
            pass
    for p in find_projects(roots, ".als"):
        try:
            text, _ = read_als(p)
            refs.update(html.unescape(m.group(2)) for m in PATH_RE.finditer(text) if m.group(2))
        except Exception:
            pass
    folders, loose = set(), {}
    for s in refs:
        if not os.path.isfile(s):
            continue
        sp = Path(s)
        if any(c == sp or c in sp.parents for c in covered):
            continue
        if sp.parent.parent == h:
            # a file sitting straight in ~/Downloads, ~/Desktop etc.: take the file, not the whole folder
            loose.setdefault(str(sp.parent), set()).add(sp.name)
            continue
        f = sample_folder_for(s, h)
        if f and f.is_dir() and f != h:
            folders.add(f)
    folders = sorted((f for f in folders if not any(o != f and o in f.parents for o in folders)),
                     key=lambda f: str(f).lower())
    return folders, loose


def cmd_pack(args):
    items = pack_items(args.include, set(args.skip))
    if not items:
        sys.exit("Nothing to pack: no standard music folders found. Add folders with --include FOLDER.")

    print("Measuring folders...", flush=True)
    for it in items:
        if "only" in it:
            files = []
            for name in it["only"]:
                ap = Path(it["source_path"]) / name
                if ap.is_file():
                    files.append((name, ap, ap.stat().st_size))
        else:
            files = list(walk_files(it["source_path"]))
        it["_files"] = files
        it["files"] = len(files)
        it["bytes"] = sum(f[2] for f in files)
    total = sum(it["bytes"] for it in items)

    if args.list or not args.to:
        print(f"\n{'ID':<22} {'SIZE':>10} {'FILES':>8}  WHAT")
        for it in items:
            print(f"{it['id']:<22} {rs.human_size(it['bytes']):>10} {it['files']:>8}  {it['label']}  ({it['source_path']})")
        print(f"\nTotal {rs.human_size(total)}. Pack with: --to DRIVE   (leave one out with --skip ID)")
        if not args.to:
            return 0
        if args.list:
            return 0

    host = rs.host_name()
    bundle = Path(args.to).expanduser() / f"RigMover-{host}-{dt.date.today().isoformat()}"
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / "index").mkdir(exist_ok=True)

    already = 0
    for it in items:
        already += sum(r["size"] for r in read_jsonl(bundle / "index" / f"{it['id']}.jsonl").values())
    free = shutil.disk_usage(bundle).free
    if total - already > free:
        sys.exit(f"Not enough space on the drive: need {rs.human_size(total - already)}, "
                 f"have {rs.human_size(free)}. Leave something out with --skip ID.")

    print("\nSaving the plugin list...", flush=True)
    rs.run_scan(bundle / "rig-report", quick=True)
    shutil.copy2(bundle / "rig-report" / "plugins.csv", bundle / "plugins.csv")

    manifest = {
        "bundle_version": BUNDLE_VERSION, "created": dt.datetime.now().isoformat(timespec="seconds"),
        "source_host": host, "source_os": THIS_OS, "source_home": str(home()),
        "items": [{k: v for k, v in it.items() if not k.startswith("_")} for it in items],
    }
    (bundle / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    prog = Progress(total, "Copying")
    prog.add(0)
    errors = []
    for it in items:
        index_path = bundle / "index" / f"{it['id']}.jsonl"
        done = read_jsonl(index_path)
        if index_path.exists():
            # Rewrite the index from its complete records, so a line cut off by an interruption
            # can't swallow the next record appended after it.
            index_path.write_text("".join(json.dumps(r) + "\n" for r in done.values()), encoding="utf-8")
        data_root = bundle / "data" / it["id"]
        with open(index_path, "a", encoding="utf-8") as index:
            for rel, ap, size in it["_files"]:
                stored = safe_rel(rel)
                dst = data_root / stored
                rec = done.get(rel)
                if rec and rec["size"] == size and dst.is_file() and dst.stat().st_size == size:
                    prog.add(size)
                    continue
                try:
                    digest = copy_and_hash(ap, dst)
                except OSError as e:
                    errors.append(f"{ap}: {e}")
                    continue
                index.write(json.dumps({"rel": rel, "stored": stored, "size": size, "sha256": digest}) + "\n")
                index.flush()
                prog.add(size)

    print(f"\nBundle ready: {bundle}")
    if errors:
        (bundle / "pack-errors.txt").write_text("\n".join(errors), encoding="utf-8")
        print(f"{len(errors)} files couldn't be copied; see pack-errors.txt. Run the same command again to retry.")
    print("Next, on the new computer:  python3 rig_mover.py unpack \"" + str(bundle) + "\"")
    return 0


# --------------------------------------------------------------------------- unpack

def cmd_unpack(args):
    bundle = Path(args.bundle).expanduser()
    try:
        manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        sys.exit(f"Not a Rig Mover bundle (no readable manifest.json): {bundle}")
    src_host = manifest["source_host"]
    dest_known = known_roots(THIS_OS, home())
    landing = moved_root(src_host)
    landing.mkdir(parents=True, exist_ok=True)

    plan = []
    for it in manifest["items"]:
        if it["kind"] == "known" and it["id"] in dest_known:
            dest = dest_known[it["id"]][1]
        else:
            dest = landing / Path(it["source_path"].replace("\\", "/")).name
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
    if shutil.disk_usage(landing).free < total:
        print(f"Warning: this disk may not have room for {rs.human_size(total)}.")

    prog = Progress(total, "Copying")
    prog.add(0)
    conflicts, bad, entries = [], [], []
    for it, dest, idx in plan:
        data_root = bundle / "data" / it["id"]
        for rel, rec in idx.items():
            src = data_root / rec["stored"]
            target_rel = rec["rel"] if THIS_OS != "windows" else rec["stored"]
            dst = dest / target_rel
            if dst.exists():
                if dst.stat().st_size == rec["size"] and hash_file(dst) == rec["sha256"]:
                    prog.add(rec["size"])
                    continue  # already there, identical
                dst = dst.with_name(f"{dst.stem} (from {src_host}){dst.suffix}")
                if dst.exists() and dst.stat().st_size == rec["size"]:
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
        entries.append({"id": it["id"], "label": it["label"], "old": it["source_path"], "new": str(dest)})

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

    lines = [f"Rig Mover unpack from {src_host} ({manifest['source_os']}) on {pathmap['created']}", ""]
    lines += [f"{e['label']}: {e['old']} -> {e['new']}" for e in entries]
    if conflicts:
        lines += ["", "Files that already existed here with different contents. The moved copy was saved",
                  f"next to them with '(from {src_host})' in the name:"] + conflicts
    if bad:
        lines += ["", "Problems:"] + bad
    (landing / "unpack-report.txt").write_text("\n".join(lines), encoding="utf-8")

    print(f"\nDone. Moved files are in place. Path map: {pm_path}")
    if conflicts:
        print(f"{len(conflicts)} files already existed with different contents; see unpack-report.txt.")
    if bad:
        print(f"{len(bad)} problems; see unpack-report.txt.")
    print("\nNext:")
    print("  python3 rig_mover.py fix-ableton     (if you use Ableton)")
    print("  python3 rig_mover.py check-fl        (if you use FL Studio)")
    print(f"  python3 rig_scanner.py --compare \"{landing / f'plugins-{src_host}.csv'}\"   (plugins to reinstall)")
    return 0


# --------------------------------------------------------------------------- path mapping

def norm(p, fold_case):
    s = re.sub(r"/+", "/", p.replace("\\", "/")).rstrip("/")
    return s.lower() if fold_case else s


def load_pathmaps(explicit):
    paths = [Path(explicit)] if explicit else sorted((home() / "Music" / "Rig Mover").glob("*/pathmap.json"))
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
        pairs = [(e["old"], e["new"]) for e in pm["entries"]] + [(pm["home"]["old"], pm["home"]["new"])]
        for old_root, new_root in pairs:
            k = norm(old_root, fold)
            if o == k or o.startswith(k + "/"):
                rest = o_raw[len(k):].lstrip("/")
                for r in dict.fromkeys((rest, safe_rel(rest))) if rest else ("",):
                    # safe_rel: names Windows can't hold (e.g. "kick: hard.wav") were stored renamed
                    out.append(Path(new_root).joinpath(*r.split("/")) if r else Path(new_root))
    return out


HIDE = None  # during a rehearsal: (old home, rehearsal folder). Files under the old home count as gone.


def _norm_abs(p):
    return os.path.normcase(os.path.abspath(str(p)))


def exists(p):
    """os.path.exists, except that a rehearsal pretends the old computer's files are gone."""
    if not p:
        return False
    if HIDE:
        s, (old, keep) = _norm_abs(p), HIDE
        under = lambda root: s == root or s.startswith(root.rstrip(os.sep) + os.sep)
        if under(old) and not under(keep):
            return False
    return os.path.exists(p)


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
    roots = [h / "Music", h / "Documents", h / "Desktop"]
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
    found = set()
    for root in roots:
        for cur, dirs, files in os.walk(root, onerror=lambda e: None):
            dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIRS and not d.startswith(".")
                       and (include_backups or d.lower() != "backup")]
            for fn in files:
                if fn.lower().endswith(ext) and not fn.startswith("._"):
                    found.add(Path(cur) / fn)
    return sorted(found, key=lambda p: str(p).lower())


# --------------------------------------------------------------------------- Ableton

FILEREF_RE = re.compile(r"<FileRef(?:\s[^>]*)?>(.*?)</FileRef>", re.S)
PATH_RE = re.compile(r'(<Path Value=")([^"]*)(")')
REL_RE = re.compile(r'<RelativePath Value="([^"]*)"')
CREATOR_RE = re.compile(r'Creator="([^"]*)"')


def xml_attr_escape(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def read_als(path):
    raw = Path(path).read_bytes()
    try:
        return gzip.decompress(raw).decode("utf-8"), True
    except OSError:
        return raw.decode("utf-8", errors="replace"), False


def als_new_format(text):
    """Live 11+ stores plain <Path Value=...> in each FileRef; Live 10 and older use binary aliases."""
    return '<Path Value="' in text


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

    new_text = FILEREF_RE.sub(fix_ref, text)
    if result["fixed"] and not dry_run:
        backup_dir = proj_dir / "Rig Mover backup"
        backup_dir.mkdir(exist_ok=True)
        backup = backup_dir / Path(path).name
        if not backup.exists():
            shutil.copy2(path, backup)
        data = new_text.encode("utf-8")
        Path(path).write_bytes(gzip.compress(data) if gz else data)
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
    report = []
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
        print("Originals are saved in a 'Rig Mover backup' folder next to each changed project.")
    if totals["old"]:
        print(f"{totals['old']} projects are from Live 10 or older, which this can't rewrite. Open each in Live, "
              "then File > Manage Files > Locate, and point Live at the moved sample folder.")
    out = (home() / "Music" / "Rig Mover")
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
    data = Path(path).read_bytes()
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
    unreadable, report, add_folders = [], [], set()
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
    out = home() / "Music" / "Rig Mover"
    out.mkdir(parents=True, exist_ok=True)
    (out / "fl-check-report.txt").write_text("\n".join(report + ["", "Unreadable:"] + unreadable),
                                             encoding="utf-8")
    print(f"Full list: {out / 'fl-check-report.txt'}")
    return 0


# --------------------------------------------------------------------------- scan

def cmd_scan(args):
    h = home()
    roots = [r for r in (h / "Music", h / "Documents", h / "Desktop") if r.is_dir()]
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
            refs = len(PATH_RE.findall(text)) if als_new_format(text) else None
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

def project_refs(p):
    """(kind, [referenced file paths as the project stored them]) or (kind, None) if unreadable."""
    if p.suffix.lower() == ".flp":
        try:
            return "FL Studio", [s for s in dict.fromkeys(flp_samples(p)[1]) if not FL_PLACEHOLDER.match(s)]
        except (ValueError, OSError, struct.error):
            return "FL Studio", None
    src = p.parent / "Rig Mover backup" / p.name  # the original, if fix-ableton rewrote it
    try:
        text, _ = read_als(src if src.is_file() else p)
    except Exception:
        return "Ableton", None
    if not als_new_format(text):
        return "Ableton (Live 10 or older)", None
    return "Ableton", [v for v in dict.fromkeys(html.unescape(m.group(2)) for m in PATH_RE.finditer(text)) if v]


def cmd_rehearse(args):
    """A full move on one computer: pack, unpack into a stand-in 'new computer' folder, repair,
    then check every project's samples. Your real folders are only read, never changed."""
    global HIDE
    base = (Path(args.to).expanduser() if args.to else Path(__file__).resolve().parent / "rehearsal").resolve()
    drive, new_home = base / "drive", base / "new-computer"
    real_home = home()
    items = pack_items(args.include, set(args.skip))
    need = sum(size for it in items
               for _r, _a, size in ([(n, None, (Path(it["source_path"]) / n).stat().st_size)
                                     for n in it["only"]] if "only" in it else walk_files(it["source_path"])))
    base.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(base).free
    print(f"Rehearsal needs about {rs.human_size(need * 2)} (a bundle plus an unpacked copy); "
          f"{rs.human_size(free)} free here.")
    if need * 2 > free:
        sys.exit("Not enough space. Leave big items out with --skip ID, or use --to on a bigger drive.")

    print("\n== 1/4  Pack, as the old computer")
    cmd_pack(argparse.Namespace(to=str(drive), list=False, include=args.include, skip=args.skip))
    bundles = sorted(drive.glob("RigMover-*"), key=lambda p: p.stat().st_mtime)
    if not bundles:
        sys.exit("Packing produced no bundle.")

    os.environ["RIGMOVER_HOME"] = str(new_home)
    new_home.mkdir(parents=True, exist_ok=True)
    HIDE = (_norm_abs(real_home), _norm_abs(base))
    print("\n== 2/4  Unpack, as the new computer (the old computer's files now count as gone)")
    cmd_unpack(argparse.Namespace(bundle=str(bundles[-1]), dest_for=[]))
    print("\n== 3/4  Repair projects")
    cmd_fix_ableton(argparse.Namespace(pathmap=None, folder=[], dry_run=False))
    cmd_check_fl(argparse.Namespace(pathmap=None, folder=[], include_backups=False))

    print("\n== 4/4  Check every project on the new computer")
    maps = load_pathmaps(None)
    roots = project_search_roots(maps, [])
    totals = {"kept": 0, "lost": 0, "already": 0, "projects": 0, "unchecked": 0}
    lost, unchecked = [], []
    for p in find_projects(roots, ".flp") + find_projects(roots, ".als"):
        kind, refs = project_refs(p)
        if refs is None:
            totals["unchecked"] += 1
            unchecked.append(f"{p}  ({kind})")
            continue
        totals["projects"] += 1
        for r in refs:
            before = os.path.exists(r)           # on the old computer
            after = locate(r, maps)[0] != "missing"  # on the new one
            if not before:
                totals["already"] += 1
            elif after:
                totals["kept"] += 1
            else:
                totals["lost"] += 1
                lost.append(f"{p.name}: {r}")

    checked = totals["kept"] + totals["lost"]
    lines = [
        f"Freighter rehearsal, {dt.datetime.now():%Y-%m-%d %H:%M}",
        f"Projects checked: {totals['projects']} (+{totals['unchecked']} that can't be checked automatically)",
        f"Samples that worked before the move: {checked}",
        f"  still found after the move: {totals['kept']}",
        f"  LOST in the move: {totals['lost']}",
        f"Samples already missing before the move (not Freighter's fault): {totals['already']}",
    ]
    if lost:
        lines += ["", "Lost samples (please report these):"] + lost
    if unchecked:
        lines += ["", "Not checked automatically:"] + unchecked
    report = base / "rehearsal-report.txt"
    report.write_text("\n".join(lines), encoding="utf-8")
    print("\n" + "\n".join(lines[:6]))
    verdict = "PASS: every working sample survived the move." if not totals["lost"] else \
        f"FAIL: {totals['lost']} samples were lost. See {report}"
    print("\n" + verdict)
    print(f"Rehearsal files: {base}  (delete this folder when you're done; your real files weren't touched)")
    HIDE = None
    return 0 if not totals["lost"] else 2


# --------------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="Move a music setup between computers.",
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="rig report plus a list of your projects")
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
    s.set_defaults(fn=cmd_pack)

    s = sub.add_parser("unpack", help="put a bundle's files in place on this computer")
    s.add_argument("bundle")
    s.add_argument("--dest-for", action="append", default=[], metavar="ID=FOLDER",
                   help="put one item somewhere else, e.g. extra1=/Volumes/Samples/Libraries")
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
    s.add_argument("--to", metavar="FOLDER", help="where to put the rehearsal (default: ./rehearsal)")
    s.add_argument("--include", action="append", default=[], metavar="FOLDER")
    s.add_argument("--skip", action="append", default=[], metavar="ID")
    s.set_defaults(fn=cmd_rehearse)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
