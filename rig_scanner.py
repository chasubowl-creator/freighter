#!/usr/bin/env python3
"""
Rig Scanner - lists the audio plugins installed on this computer and writes a report.

* Reads plugin info files only. It never loads a plugin, so a broken plugin can't crash it.
* Works on macOS and Windows. Needs only Python 3.8+ (no extra packages).
* Nothing leaves your computer.

Usage:
  python3 rig_scanner.py                      scan and open the report
  python3 rig_scanner.py --compare OTHER.csv  also show plugins from another computer's
                                              scan that are missing here (for moving rigs)
  python3 rig_scanner.py --extra-dir "D:\\VST"  also scan a custom plugin folder (repeatable)
  python3 rig_scanner.py --quick              skip measuring backup folder sizes
  python3 rig_scanner.py --no-open            don't open the report when done
"""

import argparse
import csv
import datetime as dt
import html
import json
import os
import platform
import plistlib
import re
import socket
import struct
import subprocess
import sys
import webbrowser
from pathlib import Path

IS_MAC = sys.platform == "darwin"
IS_WIN = os.name == "nt"
HOME = Path.home()

# --------------------------------------------------------------------------- helpers

def app_dir():
    """Folder to write reports next to: the program's own folder, also when packaged as an .exe/app."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def human_size(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def path_stats(p):
    """Total bytes and file count of a file or folder."""
    p = Path(p)
    try:
        if p.is_file():
            return p.stat().st_size, 1
    except OSError:
        return 0, 0
    total = count = 0
    for root, _dirs, files in os.walk(p, onerror=lambda e: None):
        for fn in files:
            try:
                total += os.lstat(os.path.join(root, fn)).st_size
                count += 1
            except OSError:
                pass
    return total, count


TOKEN_JUNK = re.compile(
    r"\b(x64|x86|64 ?bit|32 ?bit|vst3?|vst2|au|aax|clap|component|stereo|mono|plugin|plug in|win64|win32)\b"
)


def match_key(name):
    """Normalise a plugin name so the same plugin matches across formats and computers."""
    s = name.lower()
    s = re.sub(r"\(.*?\)|\[.*?\]", " ", s)
    s = re.sub(r"[_\-.]+", " ", s)
    s = TOKEN_JUNK.sub(" ", s)
    s = re.sub(r"[^a-z0-9]", "", s)
    return s or re.sub(r"[^a-z0-9]", "", name.lower())


def vendor_key(v):
    return re.sub(r"[^a-z0-9]", "", v.lower())


SKIP_ID_PARTS = {
    "com", "net", "org", "co", "uk", "de", "fr", "io", "nl", "se", "it", "jp", "ch", "at",
    "be", "dk", "no", "fi", "au", "ca", "us", "es", "pl", "ru", "www", "audio", "music",
    "audiounit", "vst", "vst3", "plugins", "plugin",
}


def vendor_from_bundle_id(bid):
    for part in (bid or "").split("."):
        if part and part.lower() not in SKIP_ID_PARTS:
            return part[:1].upper() + part[1:]
    return ""


# --------------------------------------------------------------------------- binary arch

MACHO_CPU = {7: "i386", 0x01000007: "x86_64", 12: "arm", 0x0100000C: "arm64",
             18: "ppc", 0x01000012: "ppc64"}
PE_MACHINE = {0x14C: "x86", 0x8664: "x64", 0xAA64: "arm64", 0xA641: "arm64ec"}


def macho_archs(path):
    try:
        with open(path, "rb") as f:
            head = f.read(8)
            if len(head) < 8:
                return []
            magic = head[:4]
            if magic in (b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf"):  # universal binary
                n = struct.unpack(">I", head[4:8])[0]
                if n == 0 or n > 30:
                    return []
                size = 20 if magic == b"\xca\xfe\xba\xbe" else 32
                archs = []
                for _ in range(n):
                    ent = f.read(size)
                    if len(ent) < 4:
                        break
                    cpu = struct.unpack(">I", ent[:4])[0]
                    archs.append(MACHO_CPU.get(cpu, hex(cpu)))
                return archs
            if magic in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe"):
                cpu = struct.unpack("<I", head[4:8])[0]
            elif magic in (b"\xfe\xed\xfa\xcf", b"\xfe\xed\xfa\xce"):
                cpu = struct.unpack(">I", head[4:8])[0]
            else:
                return []
            return [MACHO_CPU.get(cpu, hex(cpu))]
    except OSError:
        return []


def pe_arch(path):
    try:
        with open(path, "rb") as f:
            if f.read(2) != b"MZ":
                return []
            f.seek(0x3C)
            off = struct.unpack("<I", f.read(4))[0]
            f.seek(off)
            if f.read(4) != b"PE\0\0":
                return []
            m = struct.unpack("<H", f.read(2))[0]
            return [PE_MACHINE.get(m, hex(m))]
    except (OSError, struct.error):
        return []


def is_apple_silicon():
    if not IS_MAC:
        return False
    try:
        out = subprocess.run(["sysctl", "-n", "hw.optional.arm64"], capture_output=True, text=True)
        return out.stdout.strip() == "1"
    except OSError:
        return platform.machine() == "arm64"


# --------------------------------------------------------------------------- metadata readers

def read_plist(bundle):
    try:
        with open(Path(bundle) / "Contents" / "Info.plist", "rb") as f:
            return plistlib.load(f)
    except Exception:
        return None


def read_moduleinfo(bundle):
    """VST3 SDK 3.7.5+ bundles carry Contents/Resources/moduleinfo.json (JSON5-ish)."""
    p = Path(bundle) / "Contents" / "Resources" / "moduleinfo.json"
    if not p.is_file():
        return {}
    try:
        txt = p.read_text(encoding="utf-8", errors="replace")
        txt = re.sub(r",\s*([}\]])", r"\1", txt)
        d = json.loads(txt)
    except Exception:
        return {}
    fi = d.get("Factory Info") or {}
    return {"name": d.get("Name", ""), "version": d.get("Version", ""), "vendor": fi.get("Vendor", "")}


def win_version_info(path):
    """Read CompanyName / ProductName / FileVersion from a Windows DLL's version resource."""
    if not IS_WIN:
        return {}
    try:
        import ctypes
        from ctypes import wintypes
        ver = ctypes.windll.version
        size = ver.GetFileVersionInfoSizeW(str(path), None)
        if not size:
            return {}
        buf = ctypes.create_string_buffer(size)
        if not ver.GetFileVersionInfoW(str(path), 0, size, buf):
            return {}
        ptr, length = ctypes.c_void_p(), wintypes.UINT()
        langs = []
        if ver.VerQueryValueW(buf, "\\VarFileInfo\\Translation", ctypes.byref(ptr), ctypes.byref(length)) \
                and length.value >= 4:
            arr = (ctypes.c_ushort * (length.value // 2)).from_address(ptr.value)
            langs = ["%04x%04x" % (arr[i], arr[i + 1]) for i in range(0, len(arr) - 1, 2)]
        langs += ["040904b0", "040904e4", "000004b0"]
        out = {}
        for key in ("CompanyName", "ProductName", "FileVersion", "ProductVersion"):
            for lang in langs:
                if ver.VerQueryValueW(buf, f"\\StringFileInfo\\{lang}\\{key}",
                                      ctypes.byref(ptr), ctypes.byref(length)) and length.value:
                    out[key] = ctypes.wstring_at(ptr.value, length.value).rstrip("\x00").strip()
                    break
        return out
    except Exception:
        return {}


def looks_like_vst2_dll(path):
    """VST2 DLLs export 'VSTPluginMain' (or the old 'main'); dependency DLLs don't."""
    try:
        import mmap
        with open(path, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as m:
            return m.find(b"VSTPluginMain") != -1 or m.find(b"\0main\0") != -1
    except (OSError, ValueError):
        return True  # can't tell; keep it


# --------------------------------------------------------------------------- where to look

MAC_EXTS = {".component": "AU", ".vst": "VST2", ".vst3": "VST3", ".clap": "CLAP", ".aaxplugin": "AAX"}
WIN_EXTS = {".vst3": "VST3", ".clap": "CLAP", ".aaxplugin": "AAX"}
WIN_VST2_EXTS = {".dll": "VST2", **WIN_EXTS}


def mac_roots():
    return [
        (Path("/Library/Audio/Plug-Ins"), MAC_EXTS),
        (HOME / "Library/Audio/Plug-Ins", MAC_EXTS),
        (Path("/Library/Application Support/Avid/Audio/Plug-Ins"), MAC_EXTS),
    ]


def win_roots():
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pfx = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    cf = os.environ.get("CommonProgramFiles", os.path.join(pf, "Common Files"))
    roots = [
        (Path(cf) / "VST3", WIN_EXTS),
        (Path(cf) / "CLAP", WIN_EXTS),
        (Path(cf) / "Avid" / "Audio" / "Plug-Ins", WIN_EXTS),
        (HOME / "AppData" / "Local" / "Programs" / "Common" / "VST3", WIN_EXTS),
    ]
    vst2 = set()
    try:
        import winreg
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for key in (r"SOFTWARE\VST", r"SOFTWARE\WOW6432Node\VST"):
                try:
                    with winreg.OpenKey(hive, key) as k:
                        v, _ = winreg.QueryValueEx(k, "VSTPluginsPath")
                        if v:
                            vst2.add(v)
                except OSError:
                    pass
    except ImportError:
        pass
    for base in (pf, pfx):
        for sub in ("VSTPlugins", "Vstplugins", "VST", "VST2", "Steinberg\\VSTPlugins",
                    "Common Files\\VST2", "Common Files\\Steinberg\\VST2"):
            vst2.add(os.path.join(base, sub))
    for d in vst2:
        roots.append((Path(d), WIN_VST2_EXTS))
    return roots


def find_plugins(root, exts):
    found = []
    if not root.is_dir():
        return found
    for cur, dirs, files in os.walk(root, onerror=lambda e: None):
        keep = []
        for d in dirs:
            ext = os.path.splitext(d)[1].lower()
            if ext in exts:
                found.append((Path(cur) / d, exts[ext]))
            else:
                keep.append(d)
        dirs[:] = keep  # don't descend into plugin bundles
        for fn in files:
            ext = os.path.splitext(fn)[1].lower()
            if ext in exts:
                found.append((Path(cur) / fn, exts[ext]))
    return found


# --------------------------------------------------------------------------- per-plugin readers

def mac_entry(path, fmt):
    pl = read_plist(path)
    flags = []
    name, vendor, version, bid = path.stem, "", "", ""
    if pl is None:
        flags.append("Can't read its info file (may be damaged or incomplete)")
    else:
        name = pl.get("CFBundleName") or path.stem
        version = str(pl.get("CFBundleShortVersionString") or pl.get("CFBundleVersion") or "")
        bid = pl.get("CFBundleIdentifier", "")
        if fmt == "AU":
            comps = pl.get("AudioComponents") or []
            if comps and isinstance(comps[0], dict):
                n = comps[0].get("name", "")
                if ":" in n:
                    vendor, name = [s.strip() for s in n.split(":", 1)]
                if len(comps) > 1 and "shell" in path.stem.lower():
                    name = path.stem
    if fmt == "VST3":
        mi = read_moduleinfo(path)
        vendor = vendor or mi.get("vendor", "")
        version = version or mi.get("version", "")
    vendor = vendor or vendor_from_bundle_id(bid)

    archs = []
    macos = path / "Contents" / "MacOS"
    exe = macos / pl.get("CFBundleExecutable", "") if pl and pl.get("CFBundleExecutable") else None
    if exe is None or not exe.is_file():
        exe = next((p for p in macos.iterdir() if p.is_file()), None) if macos.is_dir() else None
    if exe is None:
        if pl is not None:
            flags.append("No program file inside (broken or partial install)")
    else:
        archs = macho_archs(exe)
    return name, vendor, version, archs, flags


def win_binary(path):
    if path.is_file():
        return path
    contents = path / "Contents"
    for sub in ("x86_64-win", "arm64x-win", "arm64ec-win", "arm64-win", "x86-win", "x64", "Win64", "Win32"):
        d = contents / sub
        if d.is_dir():
            for f in d.iterdir():
                if f.is_file() and f.suffix.lower() in (".vst3", ".aaxplugin", ".dll", ".clap"):
                    return f
    return None


def win_entry(path, fmt):
    flags = []
    binary = win_binary(path)
    info = win_version_info(binary) if binary else {}
    mi = read_moduleinfo(path) if fmt == "VST3" and path.is_dir() else {}
    name = path.stem
    vendor = info.get("CompanyName") or mi.get("vendor", "")
    version = info.get("ProductVersion") or info.get("FileVersion") or mi.get("version", "")
    archs = pe_arch(binary) if binary else []
    if binary is None:
        flags.append("No program file inside (broken or partial install)")
    if fmt == "VST2" and binary and not looks_like_vst2_dll(binary):
        flags.append("Doesn't look like a plugin (probably a support file)")
    return name, vendor, version, archs, flags


# --------------------------------------------------------------------------- safety checks

SIG_LABEL = {
    "signed": "Signed",
    "adhoc": "Self-signed (no maker ID)",
    "unsigned": "Not signed",
    "untrusted": "Signed, not trusted",
    "broken": "SIGNATURE BROKEN",
    "unknown": "Couldn't check",
}
FLAG_BROKEN = ("Signature broken: its files were changed after the maker signed it, which is what "
               "cracked plugins look like. Don't carry it over; reinstall from the maker.")


def parse_codesign(display_err, verify_rc, verify_err):
    """Turn `codesign -dv` and `codesign --verify` output into (status, signer)."""
    if "not signed at all" in display_err:
        return "unsigned", ""
    signer = ""
    for line in display_err.splitlines():
        if line.startswith("Authority="):
            a = line.split("=", 1)[1].strip()
            signer = a.split(":", 1)[1].strip() if ":" in a else a
            signer = re.sub(r"\s*\([A-Z0-9]{10}\)$", "", signer)
            break
    if verify_rc != 0:
        return "broken", signer
    if "Signature=adhoc" in display_err:
        return "adhoc", ""
    return ("signed", signer) if signer else ("unknown", "")


def mac_signature(path):
    try:
        d = subprocess.run(["codesign", "-dv", "--verbose=2", str(path)], capture_output=True, text=True, timeout=60)
        if "not signed at all" in d.stderr:
            return "unsigned", ""
        v = subprocess.run(["codesign", "--verify", str(path)], capture_output=True, text=True, timeout=120)
        return parse_codesign(d.stderr, v.returncode, v.stderr)
    except (OSError, subprocess.SubprocessError):
        return "unknown", ""


WIN_STATUS = {"Valid": "signed", "NotSigned": "unsigned", "HashMismatch": "broken", "NotTrusted": "untrusted"}


def parse_win_signatures(text):
    """Parse the JSON our PowerShell Get-AuthenticodeSignature call prints: {path: (status, signer)}."""
    try:
        data = json.loads(text) if text.strip() else []
    except ValueError:
        return {}
    if isinstance(data, dict):
        data = [data]
    out = {}
    for d in data:
        if not isinstance(d, dict):
            continue
        path = d.get("Path")
        if isinstance(path, dict):  # PowerShell can wrap a string with extra properties: {"value": ..., ...}
            path = path.get("value")
        if not isinstance(path, str) or not path:
            continue
        signer = d.get("Signer")
        signer = signer.get("value", "") if isinstance(signer, dict) else (signer or "")
        status = WIN_STATUS.get(str(d.get("Status")), "unknown")
        out[os.path.normcase(path)] = (status, signer if status != "unsigned" else "")
    return out


def win_signatures(paths):
    """Check Authenticode signatures for many files in one PowerShell call."""
    if not paths:
        return {}
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write("\n".join(str(p) for p in paths))
        listfile = f.name
    script = (
        "Import-Module Microsoft.PowerShell.Security;"
        "$ErrorActionPreference='SilentlyContinue';"
        f"Get-Content -LiteralPath '{listfile}' -Encoding UTF8 | ForEach-Object {{"
        " $s = Get-AuthenticodeSignature -LiteralPath $_;"
        " $n = ''; if ($s.SignerCertificate) { $n = $s.SignerCertificate.GetNameInfo('SimpleName', $false) };"
        " [pscustomobject]@{Path=[string]$_; Status=[string]$s.Status; Signer=[string]$n} } | ConvertTo-Json -Compress"
    )
    try:
        # PowerShell 7 rewrites PSModulePath; Windows PowerShell then can't load its own Security module.
        # Clearing it lets powershell.exe rebuild its default module path.
        env = {k: v for k, v in os.environ.items() if k.upper() != "PSMODULEPATH"}
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                           capture_output=True, text=True, timeout=900, env=env)
        return parse_win_signatures(r.stdout)
    except (OSError, subprocess.SubprocessError):
        return {}
    finally:
        try:
            os.remove(listfile)
        except OSError:
            pass


def find_virus_scanner():
    """(name, how to run) for a virus scanner already on this computer, or (None, hint)."""
    if IS_WIN:
        cands = sorted(Path(os.environ.get("ProgramData", r"C:\ProgramData"), "Microsoft", "Windows Defender",
                            "Platform").glob("*/MpCmdRun.exe"), reverse=True)
        cands.append(Path(os.environ.get("ProgramFiles", r"C:\Program Files"), "Windows Defender", "MpCmdRun.exe"))
        for c in cands:
            if c.is_file():
                return "Microsoft Defender", str(c)
        return None, "Microsoft Defender wasn't found."
    import shutil as _sh
    clam = _sh.which("clamscan")
    if clam:
        return "ClamAV", clam
    return None, "No virus scanner found. On a Mac, install the free ClamAV with: brew install clamav"


def parse_clamscan(output):
    """{file: threat name} from `clamscan --infected` output lines like '/path: Name FOUND'."""
    hits = {}
    for line in output.splitlines():
        if line.endswith(" FOUND") and ": " in line:
            f, name = line[:-6].rsplit(": ", 1)
            hits[f] = name
    return hits


def virus_scan(paths):
    """{plugin path: threat name} for plugins a virus scanner flags, plus a one-line status."""
    name, how = find_virus_scanner()
    if not name:
        return {}, how
    hits = {}
    if name == "Microsoft Defender":
        for p in paths:
            try:
                r = subprocess.run([how, "-Scan", "-ScanType", "3", "-File", str(p), "-DisableRemediation"],
                                   capture_output=True, text=True, timeout=600)
            except (OSError, subprocess.SubprocessError):
                continue
            if r.returncode == 2:
                m = re.search(r"Threat\s*:\s*(.+)", r.stdout)
                hits[str(p)] = m.group(1).strip() if m else "threat found"
    else:
        try:
            r = subprocess.run([how, "-r", "--infected", "--no-summary"] + [str(p) for p in paths],
                               capture_output=True, text=True, timeout=3600)
        except (OSError, subprocess.SubprocessError):
            return {}, "ClamAV couldn't run."
        for f, threat in parse_clamscan(r.stdout).items():
            owner = next((str(p) for p in paths if f == str(p) or f.startswith(str(p) + os.sep)), f)
            hits[owner] = threat
    return hits, f"{name} scanned {len(paths)} plugins"


def apply_safety(plugins, virus=False):
    """Add signature status (and optionally virus-scan results) to each plugin."""
    targets = [p.get("_check") for p in plugins if p.get("_check")]
    if IS_WIN:
        sigs = win_signatures(targets)
    for i, p in enumerate(plugins, 1):
        if IS_MAC and i % 25 == 0:
            print(f"  checked signatures of {i}/{len(plugins)} plugins...", flush=True)
        t = p.get("_check")
        if not t:
            status, signer = "unknown", ""
        elif IS_MAC:
            status, signer = mac_signature(t)
        else:
            status, signer = sigs.get(os.path.normcase(str(t)), ("unknown", ""))
        p["signature"], p["signer"] = status, signer
        if status == "broken":
            p["flags"].append(FLAG_BROKEN)
    status_line = "Virus scan not run (add --virus-scan)"
    if virus:
        print("Scanning plugins for viruses (this can take a few minutes)...", flush=True)
        hits, status_line = virus_scan([p["path"] for p in plugins])
        for p in plugins:
            if p["path"] in hits:
                p["flags"].append(f"Virus scanner flagged it ({hits[p['path']]}). Delete it; don't reinstall this copy.")
    return status_line


# --------------------------------------------------------------------------- scan

def scan(extra_dirs):
    roots = mac_roots() if IS_MAC else win_roots() if IS_WIN else []
    for d in extra_dirs:
        roots.append((Path(d), MAC_EXTS if IS_MAC else WIN_VST2_EXTS))

    seen, hits = set(), []
    for root, exts in roots:
        for path, fmt in find_plugins(root, exts):
            key = os.path.normcase(str(path.resolve() if path.exists() else path))
            if key not in seen:
                seen.add(key)
                hits.append((path, fmt))

    apple_silicon = is_apple_silicon()
    plugins = []
    for i, (path, fmt) in enumerate(sorted(hits, key=lambda h: str(h[0]).lower()), 1):
        if i % 25 == 0:
            print(f"  read {i}/{len(hits)} plugins...", flush=True)
        reader = mac_entry if IS_MAC else win_entry
        try:
            name, vendor, version, archs, flags = reader(path, fmt)
        except Exception as e:  # never let one odd plugin stop the scan
            name, vendor, version, archs, flags = path.stem, "", "", [], [f"Couldn't read it ({e.__class__.__name__})"]
        if archs:
            if apple_silicon and "arm64" not in archs and any(a in ("x86_64", "i386") for a in archs):
                flags.append("Intel-only: runs only under Rosetta on this Mac")
            if all(a in ("i386", "x86", "ppc", "arm") for a in archs):
                flags.append("32-bit only: modern DAWs can't load it")
        size, _ = path_stats(path)
        try:
            modified = dt.datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")
        except OSError:
            modified = ""
        plugins.append({
            "name": name, "vendor": vendor or "Unknown maker", "format": fmt, "version": version,
            "archs": ", ".join(archs), "path": str(path), "size": size, "modified": modified,
            "flags": flags, "key": match_key(name),
            "signature": "", "signer": "",
            "_check": path if IS_MAC else win_binary(path),  # what the signature check looks at
        })

    # Tidy vendor spelling (e.g. "fabfilter" vs "FabFilter") using the best-looking variant.
    best = {}
    for p in plugins:
        k = vendor_key(p["vendor"])
        cur = best.get(k)
        score = lambda s: (sum(c.isupper() for c in s) + (" " in s), len(s))
        if cur is None or score(p["vendor"]) > score(cur):
            best[k] = p["vendor"]
    for p in plugins:
        p["vendor"] = best[vendor_key(p["vendor"])]

    by_key = {}
    for p in plugins:
        by_key.setdefault(p["key"], []).append(p)

    # One plugin listed under two maker spellings ("Xfer" vs "Xfer Records"): merge to the fuller one.
    rename = {}
    for group in by_key.values():
        names = {p["vendor"] for p in group if p["vendor"] != "Unknown maker"}
        if len(names) > 1:
            keep = max(names, key=len)
            for n in names:
                if n != keep and vendor_key(keep).startswith(vendor_key(n)):
                    rename[n] = keep
    for p in plugins:
        p["vendor"] = rename.get(p["vendor"], p["vendor"])
        if p["vendor"] == "Unknown maker":
            others = {q["vendor"] for q in by_key[p["key"]]} - {"Unknown maker"}
            if len(others) == 1:
                p["vendor"] = others.pop()

    # Duplicate checks.
    for group in by_key.values():
        fmts = [p["format"] for p in group]
        for p in group:
            if fmts.count(p["format"]) > 1:
                p["flags"].append("Installed twice in the same format (different folders)")
            if p["format"] == "VST2" and "VST3" in fmts:
                p["flags"].append("Older VST2 copy of a VST3 you have (keep it if old projects use it)")
    return plugins


# --------------------------------------------------------------------------- DAWs & backup folders

def find_daws():
    found = []
    if IS_MAC:
        pats = ("Ableton Live", "FL Studio", "Logic Pro", "GarageBand", "Bitwig", "REAPER",
                "Pro Tools", "Cubase", "Studio One", "Reason")
        for app in sorted(Path("/Applications").glob("*.app")):
            if app.stem.lower().startswith(tuple(p.lower() for p in pats)):
                pl = read_plist(app) or {}
                v = pl.get("CFBundleShortVersionString", "")
                found.append(f"{app.stem} {v}".strip())
    elif IS_WIN:
        pf = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        globs = ["Image-Line/FL Studio*", "Ableton/Live*", "Bitwig Studio*", "REAPER*",
                 "PreSonus/Studio One*", "Steinberg/Cubase*", "Avid/Pro Tools*", "Reason Studios/Reason*"]
        for g in globs:
            for d in sorted(pf.glob(g)):
                if d.is_dir():
                    found.append(d.name)
        pd = Path(os.environ.get("ProgramData", r"C:\ProgramData"))
        for d in sorted(pd.glob("Ableton/Live*")):
            if d.is_dir() and d.name not in found:
                found.append(d.name)
    return found


def backup_folders(quick):
    docs, music = HOME / "Documents", HOME / "Music"
    cands = [
        ("FL Studio user data (projects, presets, samples)", docs / "Image-Line"),
        ("Ableton User Library", music / "Ableton" / "User Library"),
        ("Ableton User Library", docs / "Ableton" / "User Library"),
        ("Logic projects and settings", music / "Logic"),
        ("Logic / GarageBand content", music / "Audio Music Apps"),
        ("Splice samples", HOME / "Splice"),
        ("Splice samples", docs / "Splice"),
        ("Native Instruments user content", docs / "Native Instruments"),
    ]
    out, seen = [], set()
    for label, p in cands:
        if p.is_dir() and str(p) not in seen:
            seen.add(str(p))
            if quick:
                out.append({"label": label, "path": str(p), "size": None, "files": None})
            else:
                print(f"  measuring {p} ...", flush=True)
                size, files = path_stats(p)
                out.append({"label": label, "path": str(p), "size": size, "files": files})
    return out


# --------------------------------------------------------------------------- compare

def load_other(csv_path):
    with open(csv_path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def match_keys(name, vendor, key=""):
    """A plugin's match key, plus the same key without a leading maker name
    ("FabFilter Pro-Q 3" on Windows vs "Pro-Q 3" as a Mac AU)."""
    k = key or match_key(name)
    keys = {k}
    vk = vendor_key(vendor or "")
    for prefix in {vk, vk.replace("records", ""), vk.replace("audio", ""), vk.replace("instruments", "")}:
        if len(prefix) >= 3 and k.startswith(prefix) and len(k) > len(prefix) + 1:
            keys.add(k[len(prefix):])
    return keys


def compare(plugins, other_rows):
    here = set()
    for p in plugins:
        here |= match_keys(p["name"], p["vendor"], p["key"])
    there = {}
    for r in other_rows:
        ks = match_keys(r.get("name", ""), r.get("vendor", ""), r.get("key", ""))
        if not (ks & here) and "Doesn't look like a plugin" not in r.get("flags", ""):
            there.setdefault(min(ks, key=len), r)
    missing = {}
    for r in there.values():
        missing.setdefault(r.get("vendor") or "Unknown maker", []).append(r)
    other_keys = set()
    for r in other_rows:
        other_keys |= match_keys(r.get("name", ""), r.get("vendor", ""), r.get("key", ""))
    only_here = sorted({p["name"] for p in plugins
                        if not (match_keys(p["name"], p["vendor"], p["key"]) & other_keys)}, key=str.lower)
    return missing, only_here


# --------------------------------------------------------------------------- report

CSS = """
:root{--bg:#fafaf8;--card:#fff;--text:#1d1d1b;--muted:#6b6b66;--line:#e4e3de;--accent:#2f5bd3;
--warn:#a34a00;--warnbg:#fff3e6;--okbg:#eef3ff;--chip:#f1f0ec}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--card:#1f1f1d;--text:#ecebe6;--muted:#a3a29b;
--line:#33332f;--accent:#7fa2ff;--warn:#ffb36b;--warnbg:#3a2a1a;--okbg:#1d2640;--chip:#2a2a27}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);
font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
main{max-width:1100px;margin:0 auto;padding:32px 16px 64px}
h1{font-size:28px;margin:0 0 4px}h2{font-size:20px;margin:40px 0 8px}h3{font-size:16px;margin:20px 0 6px}
.sub{color:var(--muted);margin:0 0 24px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.card .n{font-size:26px;font-weight:650}.card .l{color:var(--muted);font-size:13px}
.card.warn .n{color:var(--warn)}
.note{background:var(--okbg);border-radius:8px;padding:10px 14px;margin:12px 0}
.flag{display:inline-block;background:var(--warnbg);color:var(--warn);border-radius:6px;padding:1px 8px;
font-size:12.5px;margin:2px 4px 2px 0}
.chip{display:inline-block;background:var(--chip);border-radius:6px;padding:0 7px;font-size:12px;margin-left:4px;color:var(--muted)}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line);vertical-align:top;font-size:13.5px}
th{cursor:pointer;user-select:none;background:var(--chip);font-weight:600;white-space:nowrap}
td.path{color:var(--muted);font-size:12px;word-break:break-all}
.tablewrap{overflow-x:auto}
input[type=search]{width:100%;max-width:420px;padding:8px 12px;border:1px solid var(--line);border-radius:8px;
background:var(--card);color:var(--text);font-size:14px;margin:4px 0 10px}
.vendor{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 14px;margin:8px 0}
.vendor label{display:flex;gap:10px;align-items:baseline;font-weight:600}
.vendor ul{margin:6px 0 2px 28px;padding:0;color:var(--muted);font-size:13.5px}
.vendor input:checked+span{text-decoration:line-through;color:var(--muted)}
footer{color:var(--muted);font-size:12.5px;margin-top:40px}
"""

JS = """
const q=document.getElementById('q'),rows=[...document.querySelectorAll('#all tbody tr')];
q&&q.addEventListener('input',()=>{const v=q.value.toLowerCase();rows.forEach(r=>{r.style.display=r.textContent.toLowerCase().includes(v)?'':'none'})});
document.querySelectorAll('#all th').forEach((th,i)=>th.addEventListener('click',()=>{
 const tb=th.closest('table').tBodies[0],asc=th.dataset.asc!=='1';th.dataset.asc=asc?'1':'0';
 [...tb.rows].sort((a,b)=>{const x=a.cells[i].dataset.v??a.cells[i].textContent,y=b.cells[i].dataset.v??b.cells[i].textContent;
 const nx=parseFloat(x),ny=parseFloat(y);const c=(!isNaN(nx)&&!isNaN(ny))?nx-ny:x.localeCompare(y,undefined,{sensitivity:'base'});return asc?c:-c})
 .forEach(r=>tb.appendChild(r))}));
const KEY='rig-checklist-'+document.title;let saved={};try{saved=JSON.parse(localStorage.getItem(KEY)||'{}')}catch(e){}
document.querySelectorAll('.vendor input').forEach(cb=>{cb.checked=!!saved[cb.dataset.v];cb.addEventListener('change',()=>{
 saved[cb.dataset.v]=cb.checked;try{localStorage.setItem(KEY,JSON.stringify(saved))}catch(e){}})});
"""


def e(s):
    return html.escape(str(s), quote=True)


def vendor_checklist(groups, title_note=""):
    out = []
    for vendor in sorted(groups, key=str.lower):
        items = groups[vendor]
        names = {}
        for it in items:
            names.setdefault(it["key"] if "key" in it and it["key"] else match_key(it["name"]),
                             [it["name"], set()])[1].add(it.get("format", ""))
        lis = "".join(
            f"<li>{e(n)}{''.join(f'<span class=chip>{e(f)}</span>' for f in sorted(fs) if f)}</li>"
            for n, fs in sorted(names.values(), key=lambda x: x[0].lower()))
        out.append(f'<div class=vendor><label><input type=checkbox data-v="{e(title_note + vendor)}">'
                   f'<span>{e(vendor)} <span class=chip>{len(names)}</span></span></label><ul>{lis}</ul></div>')
    return "".join(out)


def build_report(plugins, daws, folders, cmp_result, cmp_name, host, title, safety_line=None):
    os_name = "macOS " + platform.mac_ver()[0] if IS_MAC else platform.platform() if IS_WIN else platform.platform()
    if IS_MAC and is_apple_silicon():
        os_name += " (Apple Silicon)"
    fmts = {}
    for p in plugins:
        fmts[p["format"]] = fmts.get(p["format"], 0) + 1
    flagged = [p for p in plugins if p["flags"]]
    vendors = sorted({p["vendor"] for p in plugins}, key=str.lower)
    unique = len({p["key"] for p in plugins})
    total_size = sum(p["size"] for p in plugins)

    h = [f"<!doctype html><html lang=en><head><meta charset=utf-8>"
         f"<meta name=viewport content='width=device-width,initial-scale=1'><title>{e(title)}</title>"
         f"<style>{CSS}</style></head><body><main>"]
    h.append(f"<h1>Rig report: {e(host)}</h1><p class=sub>{e(os_name)} · scanned "
             f"{dt.datetime.now().strftime('%B %d, %Y %H:%M')} · DAWs found: {e(', '.join(daws) or 'none')}</p>")
    fmt_line = " · ".join(f"{k} {v}" for k, v in sorted(fmts.items()))
    h.append("<div class=cards>"
             f"<div class=card><div class=n>{unique}</div><div class=l>different plugins</div></div>"
             f"<div class=card><div class=n>{len(plugins)}</div><div class=l>installed files ({e(fmt_line)})</div></div>"
             f"<div class=card><div class=n>{len(vendors)}</div><div class=l>makers</div></div>"
             f"<div class='card{' warn' if flagged else ''}'><div class=n>{len(flagged)}</div><div class=l>need attention</div></div>"
             f"<div class=card><div class=n>{e(human_size(total_size))}</div><div class=l>plugin files on disk</div></div>"
             "</div>")

    if cmp_result is not None:
        missing, only_here = cmp_result
        n_missing = sum(len(v) for v in missing.values())
        h.append(f"<h2>Moving rigs: {n_missing} plugins from {e(cmp_name)} aren't on this computer yet</h2>")
        h.append("<p class=note>Grouped by maker, so you know which accounts to sign in to. Tick a maker off once "
                 "you've installed its plugins here. Some Windows-only plugins have no Mac version; check "
                 "each maker's site. Names are matched loosely, so a few may be false alarms.</p>")
        h.append(vendor_checklist(missing, "missing:") if missing else "<p>Nothing missing.</p>")
        if only_here:
            h.append(f"<h3>Only on this computer ({len(only_here)})</h3><p class=sub>{e(', '.join(only_here))}</p>")

    h.append(f"<h2>Needs attention ({len(flagged)})</h2>")
    if flagged:
        h.append("<div class=tablewrap><table><thead><tr><th>Plugin</th><th>Maker</th><th>Format</th><th>What's wrong</th>"
                 "</tr></thead><tbody>")
        for p in sorted(flagged, key=lambda p: (p["flags"][0], p["name"].lower())):
            h.append(f"<tr><td>{e(p['name'])}</td><td>{e(p['vendor'])}</td><td>{e(p['format'])}</td><td>"
                     + "".join(f"<span class=flag>{e(f)}</span>" for f in p["flags"])
                     + f"<div class=path>{e(p['path'])}</div></td></tr>")
        h.append("</tbody></table></div>")
    else:
        h.append("<p>Nothing to fix.</p>")

    if safety_line is not None:
        counts = {}
        for p in plugins:
            counts[p.get("signature") or "unknown"] = counts.get(p.get("signature") or "unknown", 0) + 1
        order = ["signed", "adhoc", "untrusted", "unsigned", "broken", "unknown"]
        chips = " ".join(f"<span class=chip>{e(SIG_LABEL[k])}: {counts[k]}</span>" for k in order if counts.get(k))
        h.append("<h2>Safety check</h2><p class=sub>A maker's digital signature proves a plugin came from them and "
                 "hasn't been changed since. <b>Signature broken</b> is the warning sign: cracked plugins have "
                 "their files altered. Plenty of honest free plugins, and anything you built yourself, simply "
                 f"aren't signed.</p><p>{chips}</p><p class=sub>{e(safety_line)}</p>")

    groups = {}
    for p in plugins:
        groups.setdefault(p["vendor"], []).append(p)
    h.append("<h2>Reinstall checklist by maker</h2><p class=sub>What you'd need to set up on a new computer. "
             "Tick makers off as you go (ticks are saved in this browser only).</p>")
    h.append(vendor_checklist(groups, "all:"))

    h.append(f"<h2>Also back up these folders</h2>")
    if folders:
        h.append("<div class=tablewrap><table><thead><tr><th>What</th><th>Size</th><th>Folder</th></tr></thead><tbody>")
        for f in folders:
            size = "not measured" if f["size"] is None else f"{human_size(f['size'])} · {f['files']:,} files"
            h.append(f"<tr><td>{e(f['label'])}</td><td>{e(size)}</td><td class=path>{e(f['path'])}</td></tr>")
        h.append("</tbody></table></div>")
    else:
        h.append("<p>No standard DAW or sample folders found.</p>")
    h.append("<p class=sub>Also copy your project folders and any sample libraries you keep elsewhere, "
             "for example on an external drive.</p>")

    h.append(f"<h2>All plugins ({len(plugins)})</h2><input id=q type=search placeholder='Filter by name, maker, format...'>")
    h.append("<div class=tablewrap><table id=all><thead><tr><th>Plugin</th><th>Maker</th><th>Format</th><th>Version</th>"
             "<th>Chip</th><th>Signed by</th><th>Size</th><th>Modified</th><th>Location</th></tr></thead><tbody>")
    for p in sorted(plugins, key=lambda p: (p["vendor"].lower(), p["name"].lower(), p["format"])):
        h.append(f"<tr><td>{e(p['name'])}</td><td>{e(p['vendor'])}</td><td>{e(p['format'])}</td>"
                 f"<td>{e(p['version'])}</td><td>{e(p['archs'])}</td><td>{e(p.get('signer') or SIG_LABEL.get(p.get('signature') or '', ''))}</td><td data-v={p['size']}>{e(human_size(p['size']))}</td>"
                 f"<td>{e(p['modified'])}</td><td class=path>{e(p['path'])}</td></tr>")
    h.append("</tbody></table></div>")
    h.append("<footer>Made by Rig Scanner. It reads plugin info files only, never loads plugins, and sends nothing "
             "anywhere. Plugins in custom folders: rerun with --extra-dir \"FOLDER\".</footer>")
    h.append(f"<script>{JS}</script></main></body></html>")
    return "".join(h)


def write_csv(plugins, path):
    cols = ["name", "vendor", "format", "version", "archs", "signature", "signer", "size_mb", "modified", "flags",
            "path", "key"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for p in plugins:
            w.writerow({**{c: p.get(c, "") for c in cols}, "size_mb": f"{p['size'] / 1048576:.1f}",
                        "flags": " | ".join(p["flags"])})


# --------------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="List installed audio plugins and write a rig report.")
    ap.add_argument("--compare", metavar="CSV", help="plugins.csv from another computer's scan")
    ap.add_argument("--extra-dir", action="append", default=[], metavar="FOLDER", help="also scan this folder")
    ap.add_argument("--out", metavar="FOLDER", help="where to write the report (default: next to this script)")
    ap.add_argument("--quick", action="store_true", help="skip measuring backup folder sizes")
    ap.add_argument("--no-open", action="store_true", help="don't open the report when done")
    ap.add_argument("--virus-scan", action="store_true",
                    help="also scan plugins with Microsoft Defender (Windows) or ClamAV (Mac, if installed)")
    ap.add_argument("--no-safety", action="store_true", help="skip the plugin signature check")
    args = ap.parse_args()

    if not (IS_MAC or IS_WIN):
        print("Rig Scanner supports macOS and Windows.")
        return 1

    out_dir = Path(args.out) if args.out else None
    html_path, _csv = run_scan(out_dir, args.extra_dir, args.quick, args.compare,
                               safety=not args.no_safety, virus=args.virus_scan)
    if not args.no_open:
        webbrowser.open(html_path.as_uri())
    if getattr(sys, "frozen", False) and len(sys.argv) == 1:
        input("\nYour report is open in the browser. Press Enter to close this window...")
    return 0


def host_name():
    return socket.gethostname().split(".")[0] or "this-computer"


def run_scan(out_dir=None, extra_dirs=(), quick=False, compare_csv=None, extra_html="", safety=True, virus=False):
    """Scan, write rig-report.html + plugins.csv, and return their paths."""
    host = host_name()
    today = dt.date.today().isoformat()
    out_dir = Path(out_dir) if out_dir else app_dir() / f"rig-report-{host}-{today}"
    out_dir.mkdir(parents=True, exist_ok=True)

    print("Scanning plugin folders...", flush=True)
    plugins = scan(list(extra_dirs))
    print(f"Found {len(plugins)} plugin files.", flush=True)
    safety_line = None
    if safety:
        print("Checking plugin signatures...", flush=True)
        safety_line = apply_safety(plugins, virus)
    daws = find_daws()
    print("Looking for DAW and sample folders...", flush=True)
    folders = backup_folders(quick)

    cmp_result, cmp_name = None, ""
    if compare_csv:
        rows = load_other(compare_csv)
        cmp_name = Path(compare_csv).parent.name.replace("rig-report-", "") or "the other computer"
        cmp_result = compare(plugins, rows)

    title = f"Rig report {host} {today}"
    csv_path = out_dir / "plugins.csv"
    html_path = out_dir / "rig-report.html"
    write_csv(plugins, csv_path)
    page = build_report(plugins, daws, folders, cmp_result, cmp_name, host, title, safety_line)
    if extra_html:
        page = page.replace("<h2>All plugins", extra_html + "<h2>All plugins", 1)
    html_path.write_text(page, encoding="utf-8")

    flagged = sum(1 for p in plugins if p["flags"])
    print(f"\nDone. {len({p['key'] for p in plugins})} different plugins, {flagged} need attention.")
    print(f"Report: {html_path}\nData:   {csv_path}")
    return html_path, csv_path


if __name__ == "__main__":
    sys.exit(main())
