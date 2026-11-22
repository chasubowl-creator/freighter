"""Automated tests for the scanner (manifest.py) and the mover (freighter.py).

Run from the project folder:   python3 -m unittest discover -s tests -v
Needs only Python. Every test works in its own temporary folders and never touches your real files.
"""
import argparse
import contextlib
import datetime as dt
import gzip
import hashlib
import html
import io
import json
import os
import shutil
import struct
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import manifest as rs  # noqa: E402
import freighter as rm  # noqa: E402


# --------------------------------------------------------------------------- fixtures

# Tests never touch the real home folder: everything that would write to Music/Freighter (crash reports, repair
# journals, reports) goes to a stand-in home for this run instead. Tests that need their own use as_home().
_REAL_HOME_GUARD = None


def setUpModule():
    global _REAL_HOME_GUARD
    _REAL_HOME_GUARD = (os.environ.get("FREIGHTER_HOME"), tempfile.mkdtemp(prefix="freighter-test-home-"))
    os.environ["FREIGHTER_HOME"] = _REAL_HOME_GUARD[1]


def tearDownModule():
    old, stand_in = _REAL_HOME_GUARD
    if old is None:
        os.environ.pop("FREIGHTER_HOME", None)
    else:
        os.environ["FREIGHTER_HOME"] = old
    shutil.rmtree(stand_in, ignore_errors=True)


def write(p, data=b""):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data if isinstance(data, bytes) else data.encode())
    return p


def varint(n):
    out = b""
    while True:
        c, n = n & 0x7F, n >> 7
        out += bytes([c | (0x80 if n else 0)])
        if not n:
            return out


def make_flp(path, samples, version="25.1.0"):
    """A minimal FL Studio project: version event + one sample-path event per sample."""
    unicode = int(version.split(".")[0]) > 11
    body = bytes([199]) + varint(len(version) + 1) + version.encode() + b"\0"
    for s in samples:
        b = (s.encode("utf-16-le") + b"\0\0") if unicode else (s.encode("latin-1") + b"\0")
        body += bytes([196]) + varint(len(b)) + b
    body += bytes([64, 1, 0])  # an unrelated word-sized event
    return write(path, b"FLhd" + struct.pack("<I", 6) + struct.pack("<HHH", 0, 1, 96)
                 + b"FLdt" + struct.pack("<I", len(body)) + body)


def make_als(path, refs, creator="Ableton Live 12.1", plugins=(), pack_refs=()):
    """A minimal Live 11/12 set laid out like the real thing: each sample is <SampleRef><FileRef>...<Path Value>.
    plugins: VST2 binary paths (in PluginDesc, not samples). pack_refs: Core Library samples (LivePackName set)."""
    def fr(p, pack=""):
        p = p.replace("&", "&amp;")
        return (f'<SampleRef>\n<FileRef>\n<RelativePathType Value="0" />\n<RelativePath Value="" />\n'
                f'<Path Value="{p}" />\n<Type Value="1" />\n<LivePackName Value="{pack}" />\n'
                f'<LivePackId Value="" />\n</FileRef>\n<LastModDate Value="0" />\n</SampleRef>')
    def plug(p):
        return (f'<PluginDevice><PluginDesc><VstPluginInfo Id="0">\n<Path Value="{p}" />\n'
                f'<PlugName Value="{Path(p).stem}" />\n</VstPluginInfo></PluginDesc></PluginDevice>')
    xml = (f'<?xml version="1.0" encoding="UTF-8"?>\n<Ableton Creator="{creator}">'
           + "".join(fr(r) for r in refs) + "".join(fr(r, "Core Library") for r in pack_refs)
           + "".join(plug(p) for p in plugins) + "</Ableton>")
    return write(path, gzip.compress(xml.encode()))


@contextlib.contextmanager
def quiet():
    with contextlib.redirect_stdout(io.StringIO()):
        yield


@contextlib.contextmanager
def as_home(h):
    old = os.environ.get("FREIGHTER_HOME")
    os.environ["FREIGHTER_HOME"] = str(h)
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("FREIGHTER_HOME", None)
        else:
            os.environ["FREIGHTER_HOME"] = old


class TempDirCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="freighter-test-"))
        rm.HIDE = None

    def tearDown(self):
        rm.HIDE = None
        shutil.rmtree(self.tmp, ignore_errors=True)


# --------------------------------------------------------------------------- scanner

class ScannerNames(unittest.TestCase):
    def test_match_key_ignores_format_and_bitness_noise(self):
        self.assertEqual(rs.match_key("Serum_x64"), "serum")
        self.assertEqual(rs.match_key("Massive (x64)"), "massive")
        self.assertEqual(rs.match_key("Kontakt 7 VST3"), "kontakt7")
        self.assertEqual(rs.match_key("Serum 2"), rs.match_key("Serum2"))
        self.assertNotEqual(rs.match_key("Serum"), rs.match_key("Serum 2"))

    def test_vendor_from_bundle_id(self):
        self.assertEqual(rs.vendor_from_bundle_id("com.fabfilter.Pro-Q.3"), "Fabfilter")
        self.assertEqual(rs.vendor_from_bundle_id("uk.co.focusrite.scarlett"), "Focusrite")
        self.assertEqual(rs.vendor_from_bundle_id(""), "")

    def test_compare_matches_vendor_prefixed_names(self):
        here = [{"name": "Pro-Q 3", "vendor": "FabFilter", "key": rs.match_key("Pro-Q 3")}]
        other = [{"name": "FabFilter Pro-Q 3", "vendor": "FabFilter", "key": "", "flags": ""},
                 {"name": "Serum_x64", "vendor": "Xfer Records", "key": "", "flags": ""},
                 {"name": "msvcp140", "vendor": "Microsoft", "key": "", "flags": "Doesn't look like a plugin"}]
        missing, _ = rs.compare(here, other)
        self.assertEqual(list(missing), ["Xfer Records"])


class ScannerBinaries(TempDirCase):
    def test_universal_and_thin_macho(self):
        fat = struct.pack(">II", 0xCAFEBABE, 2) + struct.pack(">I", 0x01000007) + b"\0" * 16 \
            + struct.pack(">I", 0x0100000C) + b"\0" * 16
        self.assertEqual(rs.macho_archs(write(self.tmp / "fat", fat)), ["x86_64", "arm64"])
        thin = b"\xcf\xfa\xed\xfe" + struct.pack("<I", 0x01000007)
        self.assertEqual(rs.macho_archs(write(self.tmp / "thin", thin)), ["x86_64"])
        self.assertEqual(rs.macho_archs(write(self.tmp / "junk", b"hello")), [])

    def test_pe_arch(self):
        for machine, name in ((0x8664, "x64"), (0x14C, "x86"), (0xAA64, "arm64")):
            b = bytearray(0x100)
            b[0:2] = b"MZ"
            struct.pack_into("<I", b, 0x3C, 0x80)
            b[0x80:0x84] = b"PE\0\0"
            struct.pack_into("<H", b, 0x84, machine)
            self.assertEqual(rs.pe_arch(write(self.tmp / name, bytes(b))), [name])


class SafetyChecks(TempDirCase):
    def test_parse_codesign(self):
        signed = "Identifier=com.xfer.serum2\nAuthority=Developer ID Application: Steve Duda (42K3SNWUP7)\nAuthority=Apple Root CA\n"
        self.assertEqual(rs.parse_codesign(signed, 0, ""), ("signed", "Steve Duda"))
        self.assertEqual(rs.parse_codesign(signed, 1, "a sealed resource is missing or invalid"), ("broken", "Steve Duda"))
        self.assertEqual(rs.parse_codesign("Signature=adhoc\nTeamIdentifier=not set\n", 0, ""), ("adhoc", ""))
        self.assertEqual(rs.parse_codesign("x: code object is not signed at all\n", 1, ""), ("unsigned", ""))

    def test_parse_windows_signatures(self):
        one = '{"Path":"C:\\\\VST\\\\a.dll","Status":"Valid","Signer":"Xfer Records"}'
        self.assertEqual(rs.parse_win_signatures(one), {os.path.normcase("C:\\VST\\a.dll"): ("signed", "Xfer Records")})
        many = ('[{"Path":"C:\\\\a.dll","Status":"HashMismatch","Signer":"X"},'
                '{"Path":"C:\\\\b.dll","Status":"NotSigned","Signer":""}]')
        got = rs.parse_win_signatures(many)
        self.assertEqual(got[os.path.normcase("C:\\a.dll")][0], "broken")
        self.assertEqual(got[os.path.normcase("C:\\b.dll")][0], "unsigned")
        self.assertEqual(rs.parse_win_signatures("garbage"), {})
        wrapped = '{"Path":{"value":"C:\\\\c.dll","PSPath":"x"},"Status":"Valid","Signer":"Y"}'
        self.assertEqual(rs.parse_win_signatures(wrapped), {os.path.normcase("C:\\c.dll"): ("signed", "Y")})

    def test_parse_clamscan(self):
        out = "/x/Evil.vst3/Contents/MacOS/Evil: Osx.Trojan.Generic FOUND\n/x/ok.wav: OK\n"
        self.assertEqual(rs.parse_clamscan(out), {"/x/Evil.vst3/Contents/MacOS/Evil": "Osx.Trojan.Generic"})

    def test_broken_signature_is_flagged(self):
        plugins = [{"name": "X", "flags": [], "_check": None, "path": "x"}]
        rs.apply_safety(plugins)  # no check target: unknown, not flagged
        self.assertEqual((plugins[0]["signature"], plugins[0]["flags"]), ("unknown", []))

    @unittest.skipUnless(sys.platform == "darwin", "macOS codesign")
    def test_mac_detects_tampering(self):
        copy = self.tmp / "ls-copy"
        shutil.copyfile("/bin/ls", copy)
        self.assertEqual(rs.mac_signature(copy)[0], "signed")
        data = bytearray(copy.read_bytes())
        data[len(data) // 2] ^= 0xFF  # change one byte, as a crack would
        copy.write_bytes(bytes(data))
        self.assertEqual(rs.mac_signature(copy)[0], "broken")
        self.assertEqual(rs.mac_signature(write(self.tmp / "plain.txt", "hello"))[0], "unsigned")

    @unittest.skipUnless(os.name == "nt", "Windows Authenticode")
    def test_windows_signatures(self):
        # Plugins carry their signature inside the file, like python.exe (signed by the Python Software
        # Foundation). Windows' own files are catalog-signed on some editions, so they're not used here.
        signed = Path(sys.executable).resolve()
        unsigned = write(self.tmp / "fake.dll", b"MZ" + b"\0" * 200)
        got = rs.win_signatures([signed, unsigned])
        if got.get(os.path.normcase(str(signed)), ("missing",))[0] != "signed":
            import subprocess
            raw = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                                  f"Get-AuthenticodeSignature -LiteralPath '{signed}' | Format-List *"],
                                 capture_output=True, text=True)
            self.fail(f"got={got!r}\nRAW={raw.stdout!r} ERR={raw.stderr!r}")
        self.assertIn(got[os.path.normcase(str(unsigned))][0], ("unsigned", "unknown"))


class ProgramsLeftBehind(TempDirCase):
    def test_is_program(self):
        self.assertTrue(rm.is_program(write(self.tmp / "setup.exe", b"MZ")))
        self.assertTrue(rm.is_program(write(self.tmp / "evil.scr", b"MZ\x90\x00")))
        self.assertFalse(rm.is_program(write(self.tmp / "Recent files.scr", b"FL settings text")))
        self.assertFalse(rm.is_program(write(self.tmp / "kick.wav", b"RIFF")))

    def test_pack_leaves_programs_in_sample_folders_only(self):
        home = self.tmp / "home"
        kick = write(home / "Desktop" / "Kits" / "Kicks" / "kick.wav", os.urandom(500))
        write(home / "Desktop" / "Kits" / "Serum_crack_installer.exe", b"MZ" + os.urandom(500))
        write(home / "Desktop" / "Kits" / "Cool.vst3" / "Contents" / "x.bin", os.urandom(100))
        fl = home / "Documents" / "Image-Line"
        write(fl / "FL Studio" / "Settings" / "Browser" / "Recent files.scr", b"settings")
        write(fl / "FL Studio" / "Settings" / "make.bat", b"echo hi")
        make_flp(fl / "FL Studio" / "Projects" / "B" / "B.flp", [str(kick)])
        with as_home(home), quiet():
            rm.cmd_pack(argparse.Namespace(to=str(self.tmp / "d"), list=False, include=[], skip=[]))
        bundle = next((self.tmp / "d").glob("Freighter-*"))
        packed = {p.name for p in (bundle / "data").rglob("*")}
        self.assertNotIn("Serum_crack_installer.exe", packed)
        self.assertNotIn("Cool.vst3", packed)
        self.assertIn("kick.wav", packed)
        self.assertIn("Recent files.scr", packed, "DAW settings must move whole")
        self.assertIn("make.bat", packed, "DAW settings must move whole")
        self.assertIn("Serum_crack_installer.exe", (bundle / "left-behind.txt").read_text())


# --------------------------------------------------------------------------- project readers

class FLParser(TempDirCase):
    def test_unicode_paths(self):
        p = make_flp(self.tmp / "a.flp", ["C:\\Users\\Producer\\Kicks\\kick é.wav", "%FLStudioFactoryData%/x.wav"])
        version, samples = rm.flp_samples(p)
        self.assertEqual(version, "25.1.0")
        self.assertEqual(samples[0], "C:\\Users\\Producer\\Kicks\\kick é.wav")

    def test_old_ascii_format(self):
        p = make_flp(self.tmp / "old.flp", ["C:\\Samples\\snare.wav"], version="10.0.9")
        self.assertEqual(rm.flp_samples(p)[1], ["C:\\Samples\\snare.wav"])

    def test_long_path_needs_multibyte_length(self):
        long = "C:\\" + "\\".join(["folder"] * 40) + "\\clap.wav"   # > 127 bytes when encoded
        self.assertEqual(rm.flp_samples(make_flp(self.tmp / "l.flp", [long]))[1], [long])

    def test_rejects_non_flp(self):
        with self.assertRaises(ValueError):
            rm.flp_samples(write(self.tmp / "x.flp", b"RIFF...."))

    def test_truncated_file_raises_cleanly(self):
        p = make_flp(self.tmp / "t.flp", ["C:\\a.wav"])
        write(p, p.read_bytes()[:-10])
        try:
            rm.flp_samples(p)
        except (ValueError, struct.error):
            pass


def make_rich_flp(path, samples, data_path="C:\\Users\\Producer\\Documents\\Image-Line\\FL Studio\\Projects\\Beat\\",
                  version="25.1.0"):
    """An FL project with the kinds of events real ones have around the sample paths: byte, word and dword events,
    plugin names (201/203), plugin wrapper and state data (212/213), mixer names (204) and the data folder (202)."""
    unicode = int(version.split(".")[0]) > 11
    enc = (lambda x: x.encode("utf-16-le") + b"\0\0") if unicode else (lambda x: x.encode("latin-1") + b"\0")
    text = lambda eid, payload: bytes([eid]) + varint(len(payload)) + payload
    body = text(199, version.encode() + b"\0") + bytes([11, 1]) + bytes([66, 0x34, 0x12]) + bytes([156, 1, 2, 3, 4])
    body += text(202, enc(data_path))
    for s in samples:
        body += text(201, enc("Fruity Wrapper")) + text(203, enc("Serum 2"))
        body += text(212, bytes(range(60))) + text(213, bytes(range(256)) * 3)     # long, opaque plugin state
        body += text(196, enc(s)) + bytes([21, 0])
    body += text(204, enc("Insert 1")) + bytes([64, 1, 0])
    return write(path, b"FLhd" + struct.pack("<I", 6) + struct.pack("<HHH", 0, len(samples), 96)
                 + b"FLdt" + struct.pack("<I", len(body)) + body)


class FLRepair(TempDirCase):
    """Issue #68: FL projects are repaired by patching only their path events."""

    def test_no_change_means_byte_identical(self):
        data = make_rich_flp(self.tmp / "a.flp", ["C:\\Kits\\kick.wav", "%FLStudioFactoryData%\\x.wav"]).read_bytes()
        out, changed, skipped = rm.flp_rewrite(data, lambda s: None)
        self.assertEqual(out, data)
        self.assertEqual((changed, skipped), ({}, []))

    def test_only_the_paths_change(self):
        long_new = "/Users/producer/Music/Freighter/OLDPC/" + "Very Long Sample Pack Name/" * 6 + "kick é.wav"
        data = make_rich_flp(self.tmp / "a.flp", ["C:\\Kits\\kick é.wav", "C:\\Kits\\snare.wav"]).read_bytes()
        out, changed, _ = rm.flp_rewrite(data, lambda s: long_new if s.endswith("kick é.wav") else None)
        self.assertGreater(len(long_new.encode("utf-16-le")), 127, "the new length needs a longer varint")
        self.assertEqual(changed, {"C:\\Kits\\kick é.wav": long_new})
        self.assertTrue(rm.flp_same_except_paths(data, out))
        path = self.tmp / "b.flp"
        path.write_bytes(out)
        self.assertEqual(rm.flp_samples(path)[1], [long_new, "C:\\Kits\\snare.wav"])
        start, end, _ = rm.flp_events(out, strict=True)
        self.assertEqual(end, len(out), "the data chunk length was fixed")

    def test_data_folder_and_old_format_projects(self):
        data = make_rich_flp(self.tmp / "a.flp", ["C:\\Kits\\kick.wav"], data_path="C:\\Old\\Beat\\",
                             version="10.0.9").read_bytes()
        out, changed, skipped = rm.flp_rewrite(data, lambda s: {"C:\\Old\\Beat\\": "/New/Beat/",
                                                                "C:\\Kits\\kick.wav": "/New/Kits/kick 中.wav"}.get(s))
        self.assertEqual(changed, {"C:\\Old\\Beat\\": "/New/Beat/"}, "the data folder moves too")
        self.assertEqual(skipped, ["/New/Kits/kick 中.wav"], "a pre-11.5 project can't hold that name: left alone")
        self.assertTrue(rm.flp_same_except_paths(data, out))

    def test_fix_flp_repairs_keeps_a_backup_and_leaves_damaged_files_alone(self):
        moved = write(self.tmp / "new" / "Kits" / "kick.wav", b"kick")
        maps = [{"source_os": "windows", "entries": [{"old": "C:\\Users\\P\\Desktop\\Kits",
                                                       "new": str(moved.parent)}],
                 "home": {"old": "C:\\Users\\P", "new": str(self.tmp / "new")}}]
        beat = make_rich_flp(self.tmp / "Projects" / "Beat.flp", ["C:\\Users\\P\\Desktop\\Kits\\kick.wav",
                                                                 "C:\\Users\\P\\Desktop\\Gone\\x.wav"])
        original = beat.read_bytes()
        r = rm.fix_flp(beat, maps)
        self.assertEqual((r["fixed"], r["missing"]), (1, ["C:\\Users\\P\\Desktop\\Gone\\x.wav"]))
        self.assertEqual(Path(rm.flp_samples(beat)[1][0]).read_bytes(), b"kick")
        self.assertEqual((beat.parent / "Freighter backup" / "Beat.flp").read_bytes(), original)
        self.assertEqual(rm.fix_flp(beat, maps)["fixed"], 0, "a second run finds it already repaired")
        broken = self.tmp / "Projects" / "Broken.flp"
        data = bytearray(original)
        _s, _e, ev = rm.flp_events(original)
        at = next(e for e in ev if e[0] == 213)[1]
        data[at + 1:at + 3] = bytes([0xFF, 0x7F])                                     # a length running far past the end
        broken.write_bytes(bytes(data))
        r = rm.fix_flp(broken, maps)
        self.assertTrue(r["damaged"])
        self.assertEqual(broken.read_bytes(), bytes(data), "a damaged project is never written")

    def test_nothing_is_written_while_fl_is_open(self):
        new = self.tmp / "new"
        write(new / "Music" / "Freighter" / "OLD" / "Kits" / "kick.wav", b"kick")
        write(new / "Music" / "Freighter" / "OLD" / "pathmap.json", json.dumps({
            "source_host": "OLD", "source_os": "windows", "created": "x",
            "entries": [{"old": "C:\\Users\\P\\Desktop\\Kits", "new": str(new / "Music" / "Freighter" / "OLD" / "Kits")}],
            "home": {"old": "C:\\Users\\P", "new": str(new)}}).encode())
        beat = make_rich_flp(new / "Documents" / "Image-Line" / "FL Studio" / "Projects" / "Beat.flp",
                             ["C:\\Users\\P\\Desktop\\Kits\\kick.wav"])
        before = beat.read_bytes()
        with as_home(new), quiet(), mock.patch.object(rm, "fl_is_running", lambda: True):
            rm.cmd_fix_fl(argparse.Namespace(pathmap=None, folder=[], include_backups=False, dry_run=False))
        self.assertEqual(beat.read_bytes(), before)
        with as_home(new), quiet(), mock.patch.object(rm, "fl_is_running", lambda: False):
            rm.cmd_fix_fl(argparse.Namespace(pathmap=None, folder=[], include_backups=False, dry_run=False))
        self.assertNotEqual(beat.read_bytes(), before)


def make_plugin_flp(path, plugins=(), natives=(), version="25.2.5.5055"):
    """An FL project using third-party plugins the way FL stores them: 201 "Fruity Wrapper", 203 display name,
    212 wrapper, 213 records (uint32 id, uint64 size, data) with 54 = name, 56 = maker, 55 = plugin file."""
    enc = lambda x: x.encode("utf-16-le") + b"\0\0"
    text = lambda eid, payload: bytes([eid]) + varint(len(payload)) + payload
    rec = lambda rid, data: struct.pack("<IQ", rid, len(data)) + data
    body = text(199, version.encode() + b"\0")
    for name, maker, file in plugins:
        state = struct.pack("<i", 12) + rec(1, b"\0" * 20) + rec(52, b"X" * 16) + rec(54, name.encode())
        if maker:
            state += rec(56, maker.encode())
        if file:
            state += rec(55, file.encode())
        state += rec(53, b"opaque plugin state")
        body += text(201, enc("Fruity Wrapper")) + text(212, bytes(52)) + text(203, enc(name + " #2")) + text(213, state)
    for n in natives:
        body += text(201, enc(n)) + text(212, bytes(52)) + text(213, b"native state")
    return write(path, b"FLhd" + struct.pack("<I", 6) + struct.pack("<HHH", 0, 1, 96)
                 + b"FLdt" + struct.pack("<I", len(body)) + body)


class PluginReadiness(TempDirCase):
    """Issue #70: which plugins each project needs, and which this computer is missing."""

    def test_reads_fl_plugins_like_fl_stores_them(self):
        f = make_plugin_flp(self.tmp / "a.flp", [("Serum 2", "Xfer Records", "/Library/Audio/Plug-Ins/VST3/Serum2.vst3"),
                                                ("Kickstart 2", "", "")], natives=["Fruity Parametric EQ 2", "FLEX"])
        info = rm.project_plugins(f)
        self.assertEqual(info["daw"], "FL Studio")
        self.assertEqual(info["version"], "25.2.5.5055")
        self.assertEqual(info["plugins"], [{"name": "Serum 2", "vendor": "Xfer Records", "format": "VST3"},
                                           {"name": "Kickstart 2", "vendor": "", "format": ""}],
                         "the real name (record 54), not the display name \"Serum 2 #2\"")
        self.assertEqual(info["native"], ["FLEX", "Fruity Parametric EQ 2"])

    def test_reads_live_plugins(self):
        text = ('<Ableton Creator="Ableton Live 12.1">'
                '<PluginDesc><Vst3PluginInfo Id="0"><Name Value="Pro-Q 3" /></Vst3PluginInfo></PluginDesc>'
                '<PluginDesc><AuPluginInfo Id="0"><Name Value="Valhalla Room" /><Manufacturer Value="Valhalla DSP" />'
                '</AuPluginInfo></PluginDesc>'
                '<PluginDesc><VstPluginInfo Id="0"><Path Value="C:/VST/Serum.dll" /><PlugName Value="Serum" />'
                '</VstPluginInfo></PluginDesc></Ableton>')
        self.assertEqual(rm.als_plugins(text), [{"name": "Pro-Q 3", "format": "VST3", "vendor": ""},
                                                {"name": "Valhalla Room", "format": "AU", "vendor": "Valhalla DSP"},
                                                {"name": "Serum", "format": "VST2", "vendor": ""}])

    def info(self, *plugins, daw="FL Studio", version="25.1.0"):
        return {"daw": daw, "version": version, "native": [],
                "plugins": [{"name": n, "vendor": v, "format": f} for n, v, f in plugins]}

    def test_missing_plugins_ranked_by_how_many_projects_they_open(self):
        projects = {"A.flp": self.info(("Serum 2", "Xfer Records", "VST3"), ("Kickstart 2", "Nicky Romero", "VST3")),
                    "B.flp": self.info(("Serum 2", "", "VST3")),
                    "C.flp": self.info(version="25.2.5"),
                    "D.flp": None}
        r = rm.plugin_readiness(projects, [])
        self.assertEqual(r["total"], 3)
        self.assertEqual(r["ready"], ["C.flp"])
        self.assertEqual([(e["name"], e["vendor"], e["opens"]) for e in r["missing"]],
                         [("Serum 2", "Xfer Records", 1), ("Kickstart 2", "Nicky Romero", 1)])
        self.assertEqual(r["versions"]["FL Studio"][0], "25.2.5")
        lines = "\n".join(rm.readiness_lines(r))
        self.assertIn("1 of 3 projects have every plugin they use.", lines)
        self.assertIn("Serum 2 by Xfer Records: used in 2 projects -> 2 of 3 open", lines)
        self.assertIn("Kickstart 2 by Nicky Romero: used in 1 project -> 3 of 3 open", lines)

    def test_installed_plugins_match_across_names_and_formats(self):
        projects = {"A.flp": self.info(("Serum 2", "Xfer Records", "VST3"), ("CLA-2A Stereo", "Waves", "VST3"))}
        installed = [{"name": "Serum2", "vendor": "Xfer Records", "key": "serum2"},
                     {"name": "WaveShell1-VST3 14.12", "vendor": "Waves", "key": "waveshell1vst31412"}]
        r = rm.plugin_readiness(projects, installed)
        self.assertEqual(r["ready"], ["A.flp"], "Serum2 = Serum 2, and Waves plugins live inside the WaveShell")

    def test_mac_only_plugins_are_flagged_on_windows(self):
        projects = {"Song.als": self.info(("Valhalla Room", "Valhalla DSP", "AU"), daw="Ableton Live", version="12.1")}
        with mock.patch.object(rm, "THIS_OS", "windows"):
            r = rm.plugin_readiness(projects, [])
        self.assertTrue(any("AU (Mac-only)" in w for w in r["warnings"]))


class AbletonFix(TempDirCase):
    def test_rewrites_moved_keeps_missing_and_backs_up(self):
        new = write(self.tmp / "new" / "Kicks" / "kick & co.wav", b"x")
        als = make_als(self.tmp / "proj" / "Song.als",
                       ["C:/Users/Producer/Desktop/Kicks/kick & co.wav", "D:/gone.wav"])
        maps = [{"source_os": "windows", "entries": [{"old": "C:\\Users\\Producer\\Desktop\\Kicks", "new": str(new.parent)}],
                 "home": {"old": "C:\\Users\\Producer", "new": str(self.tmp / "h")}}]
        r = rm.fix_als(als, maps)
        self.assertEqual((r["fixed"], len(r["missing"])), (1, 1))
        text = gzip.decompress(als.read_bytes()).decode()
        self.assertIn(f'Path Value="{new.as_posix().replace("&", "&amp;")}"', text)
        self.assertIn('Path Value="D:/gone.wav"', text)
        self.assertTrue((als.parent / "Freighter backup" / "Song.als").is_file())

    def test_repair_swaps_the_file_in_whole(self):
        """Issue #19: the repaired project is written to a temp file and swapped in, never half-written."""
        target = self.tmp / "song.als"
        target.write_bytes(b"old")
        with mock.patch.object(rm.os, "replace", side_effect=OSError("power cut")):
            with self.assertRaises(OSError):
                rm.write_replace(target, b"new contents")
        self.assertEqual(target.read_bytes(), b"old", "the original survives a failed write")
        rm.write_replace(target, b"new contents")
        self.assertEqual(target.read_bytes(), b"new contents")
        self.assertFalse(list(self.tmp.glob("*.freighter-part")))

    def test_live10_is_reported_not_changed(self):
        als = write(self.tmp / "old.als", gzip.compress(b'<Ableton Creator="Ableton Live 10.1"><FileRef Id="0">'
                                                         b'<RelativePath><RelativePathElement Dir="x"/></RelativePath></FileRef></Ableton>'))
        before = als.read_bytes()
        self.assertTrue(rm.fix_als(als, [])["old_format"])
        self.assertEqual(als.read_bytes(), before)

    def test_dry_run_changes_nothing(self):
        new = write(self.tmp / "n" / "a.wav", b"x")
        als = make_als(self.tmp / "S.als", ["/old/home/a.wav"])
        before = als.read_bytes()
        maps = [{"source_os": "mac", "entries": [{"old": "/old/home", "new": str(new.parent)}],
                 "home": {"old": "/old/home", "new": str(self.tmp)}}]
        self.assertEqual(rm.fix_als(als, maps, dry_run=True)["fixed"], 1)
        self.assertEqual(als.read_bytes(), before)


# --------------------------------------------------------------------------- path mapping

class PathMapping(TempDirCase):
    def test_windows_paths_are_case_and_slash_insensitive(self):
        target = write(self.tmp / "dk" / "Snares" / "snare.wav")
        maps = [{"source_os": "windows", "entries": [{"old": "C:\\Users\\Producer\\Desktop\\DrumKits", "new": str(self.tmp / "dk")}],
                 "home": {"old": "C:\\Users\\Producer", "new": str(self.tmp)}}]
        self.assertEqual(rm.locate("c:/USERS/producer/desktop/drumkits/Snares/snare.wav", maps), ("moved", target))

    def test_mac_paths_stay_case_sensitive(self):
        maps = [{"source_os": "mac", "entries": [{"old": "/Users/x/Kits", "new": str(self.tmp)}],
                 "home": {"old": "/Users/x", "new": str(self.tmp)}}]
        self.assertEqual(rm.candidates("/users/x/kits/a.wav", maps), [])

    def test_names_windows_cant_hold_are_found_renamed(self):
        write(self.tmp / "Kicks" / "kick_ hard.wav")
        maps = [{"source_os": "mac", "entries": [{"old": "/Users/x/Kits", "new": str(self.tmp)}],
                 "home": {"old": "/Users/x", "new": str(self.tmp)}}]
        self.assertEqual(rm.locate("/Users/x/Kits/Kicks/kick: hard.wav", maps)[0], "moved")

    def test_accented_names_match_either_way(self):
        """Issue #13: "é" can be one character (Windows) or e + accent (Mac); both must be found."""
        import unicodedata
        nfc, nfd = unicodedata.normalize("NFC", "Café Kicks"), unicodedata.normalize("NFD", "Café Kicks")
        self.assertNotEqual(nfc, nfd)
        moved = self.tmp / "new" / "Kits"
        maps = [{"source_os": "windows", "entries": [{"old": "C:\\Users\\Producer\\Desktop\\Kits", "new": str(moved)}],
                 "home": {"old": "C:\\Users\\Producer", "new": str(self.tmp / "new")}}]
        stored = write(moved / nfd / "kick.wav", b"k")       # the file was stored with the Mac spelling
        found = [c for c in rm.candidates(f"C:\\Users\\Producer\\Desktop\\Kits\\{nfc}\\kick.wav", maps)]
        self.assertTrue(any(str(c).endswith(str(Path(nfd) / "kick.wav")) for c in found), found)
        self.assertTrue(any(str(c).endswith(str(Path(nfc) / "kick.wav")) for c in found), found)
        status, where = rm.locate(f"C:\\Users\\Producer\\Desktop\\Kits\\{nfc}\\kick.wav", maps)
        self.assertEqual(status, "moved")
        self.assertTrue(where.is_file())
        # and an old root written in the other spelling still matches
        maps[0]["entries"][0]["old"] = f"C:\\Users\\Producer\\Desktop\\{unicodedata.normalize('NFD', 'Kits')}"
        self.assertEqual(rm.locate(f"C:\\Users\\Producer\\Desktop\\Kits\\{nfd}\\kick.wav", maps)[0], "moved")
        self.assertTrue(stored.is_file())

    def test_safe_rel(self):
        self.assertEqual(rm.safe_rel('a:b/c?.wav'), "a_b/c_.wav")
        self.assertEqual(rm.safe_rel("CON/aux.txt"), "_CON/_aux.txt")
        self.assertEqual(rm.safe_rel("trailing. /x"), "trailing/x")

    def test_rehearsal_hides_old_home_but_not_rehearsal_folder(self):
        old = self.tmp / "home"
        keep = old / "rehearsal"
        a, b = write(old / "Desktop" / "a.wav"), write(keep / "b.wav")
        ssd = write(self.tmp / "SSD" / "Samples" / "c.wav")          # a packed folder outside the home folder
        rm.HIDE = ([rm._norm_abs(old), rm._norm_abs(ssd.parent)], rm._norm_abs(keep))
        self.assertFalse(rm.exists(a))
        self.assertFalse(rm.exists(ssd), "every packed source counts as gone, not just the home folder (#65)")
        self.assertTrue(rm.exists(b))
        self.assertTrue(rm.exists(self.tmp))
        rm.HIDE = (None, rm._norm_abs(keep))                          # what a rehearsal uses: all but its folder
        self.assertFalse(rm.exists(ssd))
        self.assertTrue(rm.exists(b))


# --------------------------------------------------------------------------- full moves

class SampleFolders(TempDirCase):
    """Which folder gets moved for a sample a project uses (issue #7)."""

    def test_windows(self):
        h = "C:\\Users\\Producer"
        f = lambda s: (lambda r: str(r) if r else None)(rm.sample_folder_for(s, h, mac=False))
        self.assertEqual(f("D:\\Samples\\kick.wav"), "D:\\Samples")
        self.assertEqual(f("D:\\Samples\\Drums\\Kit\\kick.wav"), "D:\\Samples\\Drums")
        self.assertIsNone(f("D:\\kick.wav"), "a file at the top of a drive is taken alone, never the whole drive")
        self.assertEqual(f("C:\\Users\\Producer\\Desktop\\Kit\\kick.wav"), "C:\\Users\\Producer\\Desktop\\Kit")
        self.assertEqual(f("c:\\users\\producer\\Desktop\\Kit\\kick.wav"), "C:\\Users\\Producer\\Desktop\\Kit")

    def test_mac(self):
        f = lambda s: (lambda r: r.as_posix() if r else None)(rm.sample_folder_for(s, "/Users/producer", mac=True))
        self.assertIsNone(f("/Volumes/SSD/kick.wav"))
        self.assertEqual(f("/Volumes/SSD/Samples/kick.wav"), "/Volumes/SSD/Samples")
        self.assertEqual(f("/Volumes/SSD/Samples/Drums/Kit/kick.wav"), "/Volumes/SSD/Samples")
        self.assertEqual(f("/Users/producer/Desktop/Kit/Kicks/kick.wav"), "/Users/producer/Desktop/Kit")

    def test_landing_names(self):
        self.assertEqual(rm.landing_name("D:\\"), "D drive")
        self.assertEqual(rm.landing_name("e:/"), "E drive")
        self.assertEqual(rm.landing_name("/Volumes/SSD"), "SSD")
        self.assertEqual(rm.landing_name("C:\\Users\\Producer\\Desktop\\Kit"), "Kit")

    def test_sample_at_top_of_a_drive_is_packed_alone(self):
        old = self.tmp / "old"
        drive_root = self.tmp / "SSD"
        kick = write(drive_root / "kick.wav", os.urandom(1000))
        write(drive_root / "huge-unrelated.wav", os.urandom(5000))
        make_flp(old / "Documents" / "Image-Line" / "FL Studio" / "Projects" / "Beat.flp", [str(kick)])
        real = rm.sample_folder_for
        top = lambda s, h, mac=None: None if Path(s).parent == drive_root else real(s, h, mac)
        with as_home(old), mock.patch.object(rm, "sample_folder_for", top):
            items = rm.pack_items([], set())
        loose = [it for it in items if it["id"].startswith("loose")]
        self.assertEqual(len(loose), 1, items)
        self.assertEqual(loose[0]["only"], ["kick.wav"], "only the used file, not the rest of the drive")


class WhatGetsPacked(TempDirCase):
    def items(self, h):
        with as_home(h), quiet():
            return rm.pack_items([], set())

    def test_projects_that_use_no_outside_samples_are_packed(self):
        """Issue #8: a synth-only project outside the DAW folders used to be left behind."""
        h = self.tmp / "home"
        make_als(h / "Desktop" / "Synth Song Project" / "Synth Song.als", [])
        make_flp(h / "Desktop" / "idea.flp", [])
        write(h / "Desktop" / "taxes.pdf", b"not music")
        items = self.items(h)
        self.assertIn(h / "Desktop" / "Synth Song Project", [Path(it["source_path"]) for it in items])
        self.assertTrue(any(it.get("only") == ["idea.flp"] for it in items), "a loose project file comes alone")
        self.assertFalse(any(Path(it["source_path"]) == h / "Desktop" and "only" not in it for it in items),
                         "never the whole Desktop")

    def test_pack_list_names_samples_that_are_already_missing(self):
        h = self.tmp / "home"
        make_flp(h / "Desktop" / "Beat" / "Beat.flp", [str(h / "Unplugged" / "kick.wav")])
        out = io.StringIO()
        with as_home(h), contextlib.redirect_stdout(out):
            rm.cmd_pack(argparse.Namespace(to=None, list=True, include=[], skip=[], include_programs=False))
        self.assertIn("1 sample used by 1 project is already missing", out.getvalue())
        self.assertIn("Beat.flp: " + str(h / "Unplugged" / "kick.wav"), out.getvalue())

    def test_demo_projects_inside_sample_packs_dont_pull_in_the_whole_library(self):
        """Issue #61: a pack's demo kit project used to drag the whole "Sample Packs" folder into the move."""
        h = self.tmp / "home"
        packs = h / "Desktop" / "Sample Packs"
        pack_a = packs / "Pack A"
        kit_sample = write(pack_a / "One Shots" / "Kicks" / "kick.wav", b"a" * 200)
        make_als(pack_a / "Ableton" / "Kit Project" / "Kit.als", [str(kit_sample)])
        for i in range(5):                                            # the rest of the library
            write(packs / "Pack B" / f"loop{i}.wav", b"b" * 200)
        drums = write(h / "Desktop" / "DrumKits" / "snare.wav", b"s" * 200)
        make_flp(h / "Desktop" / "Beats" / "song.flp", [str(drums)])   # the user's own project
        paths = [Path(it["source_path"]) for it in self.items(h) if "only" not in it]
        self.assertNotIn(packs, paths, "the whole library isn't packed")
        self.assertFalse(any(p == packs / "Pack B" or packs / "Pack B" in p.parents for p in paths), paths)
        self.assertTrue(any(p == pack_a or pack_a in p.parents for p in paths), "the demo's own pack comes along")
        self.assertIn(h / "Desktop" / "Beats", paths, "the user's project folder")
        self.assertIn(h / "Desktop" / "DrumKits", paths, "and the samples it uses")

    def test_a_project_moves_with_its_own_folder_not_the_whole_top_folder(self):
        h = self.tmp / "home"
        drums = write(h / "Desktop" / "DrumKits" / "snare.wav", b"s" * 200)
        make_flp(h / "Desktop" / "Everything" / "2024" / "Beat 7" / "beat7.flp", [str(drums)])
        write(h / "Desktop" / "Everything" / "Unrelated" / "big.wav", b"u" * 999)
        paths = [Path(it["source_path"]) for it in self.items(h) if "only" not in it]
        self.assertIn(h / "Desktop" / "Everything" / "2024" / "Beat 7", paths)
        self.assertNotIn(h / "Desktop" / "Everything", paths)

    # ---- issue #62: the remaining ways folders were missed or packed by mistake

    def test_a_sample_in_the_home_folder_itself_comes_as_a_loose_file(self):
        h = self.tmp / "home"
        kick = write(h / "kick.wav", b"k" * 50)
        make_flp(h / "Documents" / "Image-Line" / "FL Studio" / "Projects" / "Beat.flp", [str(kick)])
        loose = [it for it in self.items(h) if "only" in it]
        self.assertEqual([(Path(it["source_path"]), it["only"]) for it in loose], [(h, ["kick.wav"])])

    def test_a_skipped_fl_folder_stays_out(self):
        h = self.tmp / "home"
        kick = write(h / "Desktop" / "Kits" / "kick.wav", b"k" * 50)
        make_flp(h / "Documents" / "Image-Line" / "FL Studio" / "Projects" / "Beat" / "Beat.flp", [str(kick)])
        with as_home(h), quiet():
            items = rm.pack_items([], {"fl_user"})
        image_line = h / "Documents" / "Image-Line"
        for it in items:
            p = Path(it["source_path"])
            self.assertFalse(p == image_line or image_line in p.parents or p in image_line.parents, p)

    def test_an_archive_folder_named_backup_is_searched(self):
        h = self.tmp / "home"
        make_flp(h / "Documents" / "Backup" / "Beats 2019" / "old.flp", [])
        make_flp(h / "Desktop" / "Song" / "Backup" / "Song (autosave).flp", [])     # FL autosave: not moved alone
        paths = [Path(it["source_path"]) for it in self.items(h)]
        self.assertIn(h / "Documents" / "Backup" / "Beats 2019", paths)
        self.assertNotIn(h / "Desktop" / "Song" / "Backup", paths)

    def test_projects_and_samples_deeper_than_260_characters_are_found(self):
        h = self.tmp / "home"
        deep = h.joinpath("Desktop", *["a very long folder name for a sample pack"] * 7)
        os.makedirs(rm.long_path(deep / "Beat"))
        with open(rm.long_path(deep / "kick.wav"), "wb") as f:
            f.write(b"k" * 50)
        flp = make_flp(self.tmp / "stage" / "beat.flp", [str(deep / "kick.wav")]).read_bytes()
        with open(rm.long_path(deep / "Beat" / "beat.flp"), "wb") as f:
            f.write(flp)
        self.assertGreater(len(str(deep / "Beat" / "beat.flp")), 300)
        missing = {}
        with as_home(h), quiet():
            items = rm.pack_items([], set(), missing)
        self.assertEqual(missing, {}, "the deep sample isn't reported missing")
        self.assertTrue(items, "the deep project and its sample are packed")

    def test_freighters_own_output_is_never_packed(self):
        h = self.tmp / "home"
        old_rehearsal = h / "Desktop" / "freighter-windows" / "rehearsal"     # left by an older Freighter
        make_als(old_rehearsal / "new-computer" / "Music" / "Song Project" / "Song.als", [])
        (old_rehearsal / "drive").mkdir(parents=True)
        bundle = h / "Desktop" / "Move" / "Freighter-PC-2026-01-01"            # a half-made bundle
        write(bundle / "manifest.json", b"{}")
        (bundle / "index").mkdir()
        make_flp(bundle / "data" / "fl_user" / "Beat.flp", [])
        make_flp(h / "Desktop" / "Real" / "real.flp", [])
        paths = [it["source_path"] for it in self.items(h)]
        self.assertEqual(paths, [str(h / "Desktop" / "Real")])

    def test_rehearse_to_a_drive_works_in_its_own_folder(self):
        old = self.tmp / "old"
        make_flp(old / "Documents" / "Image-Line" / "FL Studio" / "Projects" / "Beat.flp", [])
        drive = self.tmp / "E"
        drive.mkdir()
        with as_home(old), quiet():
            rm.cmd_rehearse(argparse.Namespace(to=str(drive), include=[], skip=[]))
        self.assertFalse((drive / rm.REHEARSAL_MARKER).exists(), "the drive itself isn't marked")
        self.assertTrue((drive / "Freighter Rehearsal" / rm.REHEARSAL_MARKER).exists())

    def test_safety_net_packs_a_sample_the_folder_rules_missed(self):
        """Issue #64: a symlinked sample inside a packed folder is skipped by the folder walk; the safety net adds it."""
        h = self.tmp / "home"
        real = write(h / "Elsewhere" / "real kick.wav", b"the real kick")
        kits = h / "Desktop" / "Kits"
        snare = write(kits / "snare.wav", b"snare")
        try:
            os.symlink(real, kits / "kick.wav")
        except (OSError, NotImplementedError):
            self.skipTest("can't make symlinks here (Windows without developer mode)")
        make_flp(h / "Documents" / "Image-Line" / "FL Studio" / "Projects" / "Beat.flp",
                 [str(kits / "kick.wav"), str(snare)])
        with as_home(h), quiet():
            plan = rm.plan_move()
        self.assertEqual(plan["stragglers"], 1)
        packed = {str(ap) for it in plan["items"] for _r, ap, _s in it["_files"]}
        self.assertIn(str(kits / "kick.wav"), packed)
        drive, new = self.tmp / "drive", self.tmp / "new"
        drive.mkdir()
        new.mkdir()
        with as_home(h), quiet():
            rm.cmd_pack(argparse.Namespace(to=str(drive), list=False, include=[], skip=[], include_programs=False))
        shutil.rmtree(h)   # the old computer is gone
        with as_home(new), quiet():
            rm.cmd_unpack(argparse.Namespace(bundle=str(next(drive.glob("Freighter-*"))), dest_for=[]))
            maps = rm.load_pathmaps(None)
            status, where = rm.locate(str(kits / "kick.wav"), maps)
        self.assertEqual(status, "moved")
        self.assertEqual(where.read_bytes(), b"the real kick")

    def test_user_library_is_not_packed_twice(self):
        """Issue #9: a project next to the User Library used to pull in the whole Ableton folder."""
        h = self.tmp / "home"
        user_library = rm.known_roots(rm.THIS_OS, h)["ableton_user_library"][1]
        write(user_library / "Presets" / "a.adv", b"preset")
        project = user_library.parent / "Song Project"
        clap = write(project / "Samples" / "Imported" / "clap.wav", os.urandom(200))
        make_als(project / "Song.als", [str(clap)])
        items = self.items(h)
        paths = [Path(it["source_path"]) for it in items if "only" not in it]
        self.assertIn(project, paths)
        for a in paths:
            for b in paths:
                self.assertFalse(a != b and a in b.parents, f"{b} would be packed twice (inside {a})")


class WindowsDrives(unittest.TestCase):
    def test_usb_drives_that_call_themselves_fixed_get_no_warning(self):
        self.assertEqual(rm.windows_drive(2, "E", "C"), (True, True))     # a USB stick
        self.assertEqual(rm.windows_drive(3, "C", "C"), (True, False))    # the drive Windows runs from
        self.assertEqual(rm.windows_drive(3, "E", "C"), (True, None))     # a USB SSD, or a second inside disk
        self.assertEqual(rm.windows_drive(4, "Z", "C"), (False, None))    # network: may hang, so typed in instead
        self.assertEqual(rm.windows_drive(5, "D", "C"), (False, None))    # CD drive
        self.assertEqual(rm.windows_drive(1, "Q", "C"), (False, None))    # unused letter


class Drives(TempDirCase):
    """Issue #11: what different drives can and can't hold."""

    def test_mount_output_gives_the_file_system(self):
        out = ("/dev/disk3s1s1 on / (apfs, sealed, local, read-only, journaled)\n"
               "/dev/disk4s1 on /Volumes/USB STICK (msdos, local, nodev, nosuid, noowners)\n"
               "/dev/disk5s1 on /Volumes/Big (exfat, local, nodev, nosuid, noowners)\n"
               "/dev/sdb1 on /media/me/STICK type vfat (rw,nosuid)")
        self.assertEqual(rm.mount_fs(out, "/Volumes/USB STICK/moves"), "msdos")
        self.assertEqual(rm.mount_fs(out, "/Volumes/Big"), "exfat")
        self.assertEqual(rm.mount_fs(out, "/Users/me"), "apfs")
        self.assertEqual(rm.mount_fs(out, "/media/me/STICK"), "vfat")

    def test_names_that_clash_on_the_drive_get_their_own(self):
        taken = set()
        self.assertEqual(rm.unique_stored("Kits/kick?.wav", taken), "Kits/kick_.wav")
        self.assertEqual(rm.unique_stored("Kits/kick_.wav", taken), "Kits/kick_ (2).wav")
        self.assertEqual(rm.unique_stored("Kits/Kick_.wav", taken), "Kits/Kick_ (3).wav", "exFAT ignores case")
        self.assertEqual(rm.unique_stored("README", taken), "README")

    def test_clashing_names_both_survive_a_move(self):
        src = self.tmp / "Kits"
        try:
            write(src / "kick?.wav", b"first")
        except OSError:
            self.skipTest("this computer can't hold a name with ? in it (Windows)")
        write(src / "kick_.wav", b"second")
        drive = self.tmp / "drive"
        drive.mkdir()
        with as_home(self.tmp / "home"), quiet():
            rm.cmd_pack(argparse.Namespace(to=str(drive), list=False, include=[str(src)], skip=[],
                                           include_programs=False))
        stored = sorted(p.read_bytes() for p in next(drive.glob("Freighter-*")).joinpath("data").rglob("*.wav"))
        self.assertEqual(stored, [b"first", b"second"])

    def test_mac_resource_files_are_skipped(self):
        write(self.tmp / "Kit" / "._kick.wav", b"apple double")
        write(self.tmp / "Kit" / "kick.wav", b"kick")
        self.assertEqual([r for r, _, _ in rm.walk_files(self.tmp / "Kit")], ["kick.wav"])

    def test_fat32_refuses_big_files_before_copying(self):
        src = self.tmp / "Stems"
        write(src / "long-stem.wav", os.urandom(3000))
        drive = self.tmp / "drive"
        drive.mkdir()
        with as_home(self.tmp / "home"), quiet(), mock.patch.object(rm, "filesystem", lambda p: "msdos"), \
                mock.patch.object(rm, "FAT_MAX", 1000), self.assertRaises(SystemExit) as e:
            rm.cmd_pack(argparse.Namespace(to=str(drive), list=False, include=[str(src)], skip=[],
                                           include_programs=False))
        self.assertIn("FAT32", str(e.exception.code))
        self.assertIn("long-stem.wav", str(e.exception.code))
        self.assertEqual(list(drive.iterdir()), [], "nothing was written")

    def test_a_drive_that_cant_be_written_explains_itself(self):
        src = self.tmp / "Kit"
        write(src / "kick.wav", b"kick")
        with as_home(self.tmp / "home"), quiet(), mock.patch.object(rm, "filesystem", lambda p: "ntfs"), \
                mock.patch.object(rm.Path, "mkdir", side_effect=OSError(30, "Read-only file system")), \
                self.assertRaises(SystemExit) as e:
            rm.cmd_pack(argparse.Namespace(to=str(self.tmp / "drive"), list=False, include=[str(src)], skip=[],
                                           include_programs=False))
        self.assertIn("exFAT", str(e.exception.code))


class LongPaths(TempDirCase):
    """Issue #10: paths past Windows' old 260-character limit, and network drives."""

    def test_windows_long_and_network_forms(self):
        with mock.patch.object(rs, "IS_WIN", True):
            self.assertEqual(rm.long_path("Z:\\Samples\\kick.wav"), "\\\\?\\Z:\\Samples\\kick.wav",
                             "a mapped drive stays a drive letter")
            unc = rm.long_path("\\\\nas\\share\\Samples")
            self.assertEqual(unc, "\\\\?\\UNC\\nas\\share\\Samples")
            self.assertEqual(rm.short_path(unc), "\\\\nas\\share\\Samples")
            self.assertEqual(rm.long_path("\\\\?\\C:\\x"), "\\\\?\\C:\\x", "never prefixed twice")

    def test_a_file_300_characters_deep_moves(self):
        src = self.tmp / "Deep"
        deep = src.joinpath(*["a very long folder name for a sample pack"] * 8)
        os.makedirs(rm.long_path(deep))
        target = deep / "kick.wav"
        with open(rm.long_path(target), "wb") as f:
            f.write(b"deep kick")
        self.assertGreater(len(str(target)), 300)
        self.assertEqual([r for r, _, _ in rm.walk_files(src)][0].split("/")[-1], "kick.wav")
        drive, new = self.tmp / "drive", self.tmp / "new"
        drive.mkdir()
        with as_home(self.tmp / "old"), quiet():
            rm.cmd_pack(argparse.Namespace(to=str(drive), list=False, include=[str(src)], skip=[],
                                           include_programs=False))
        bundle = next(drive.glob("Freighter-*"))
        with as_home(new), quiet():
            rm.cmd_unpack(argparse.Namespace(bundle=str(bundle), dest_for=[], keep_together=True))
        found = [os.path.join(c, f) for c, _, fs in os.walk(rm.long_path(new)) for f in fs if f == "kick.wav"]
        self.assertEqual(len(found), 1)
        with open(found[0], "rb") as f:
            self.assertEqual(f.read(), b"deep kick")


class ProgramContent(TempDirCase):
    """Issue #60: files that come with programs are never packed as samples."""

    def test_only_real_samples_are_read_from_live_sets(self):
        kick = "/Users/p/Desktop/Kits/kick.wav"
        core = "/Applications/Ableton Live 12 Suite.app/Contents/App-Resources/Core Library/Samples/clap.wav"
        als = make_als(self.tmp / "Song.als", [kick], plugins=["C:/Program Files/VSTPlugins/Serum.dll"],
                       pack_refs=[core])
        text, _ = rm.read_als(als)
        self.assertEqual(rm.als_sample_refs(text), [kick], "no plugin binaries, no Core Library content")

    def test_a_live_10_set_with_a_vst2_isnt_mistaken_for_live_11(self):
        text, _ = rm.read_als(make_als(self.tmp / "Old.als", [], creator="Ableton Live 10.1.30",
                                       plugins=["C:/VST/Serum.dll"]))
        self.assertFalse(rm.als_new_format(text))
        self.assertTrue(rm.als_new_format(rm.read_als(make_als(self.tmp / "New.als", ["/x.wav"]))[0]))

    def test_program_places(self):
        inside = ["/Applications/Ableton Live 12 Suite.app/Contents/App-Resources/Core Library/x.wav",
                  "C:\\Program Files\\Image-Line\\FL Studio 2024\\Data\\Patches\\x.wav",
                  "C:/ProgramData/Ableton/Live 12 Suite/Resources/Core Library/x.wav",
                  "/Library/Application Support/Native Instruments/Kontakt 7/x.wav",
                  "/Users/p/Music/Cool.vst3/Contents/Resources/x.wav"]
        outside = ["/Users/p/Desktop/Kits/kick.wav", "D:\\Samples\\kick.wav", "/Volumes/SSD/Applications/kick.wav",
                   "C:\\Users\\P\\Documents\\Program Files Backup\\kick.wav"]
        for p in inside:
            self.assertTrue(rm.in_program_place(p), p)
        for p in outside:
            self.assertFalse(rm.in_program_place(p), p)

    def test_a_sample_inside_an_app_is_listed_not_packed(self):
        h = self.tmp / "home"
        inside_app = write(h / "Desktop" / "Tools" / "Sampler.app" / "Contents" / "Resources" / "boom.wav", b"x" * 99)
        kick = write(h / "Desktop" / "Kits" / "kick.wav", b"k" * 99)
        make_flp(h / "Documents" / "Image-Line" / "FL Studio" / "Projects" / "Beat.flp", [str(inside_app), str(kick)])
        installed = set()
        with as_home(h), quiet():
            items = rm.pack_items([], set(), {}, installed)
        sources = [it["source_path"] for it in items]
        self.assertFalse(any("Tools" in p for p in sources), sources)
        self.assertIn(str(h / "Desktop" / "Kits"), sources)
        self.assertEqual(installed, {str(inside_app)})


class FLSettings(TempDirCase):
    """Issue #69: what FL Studio's own settings say about folders."""
    REG_XML = """<?xml version="1.0" encoding="utf-8"?>
<XMLReg><Key Name="Key1"><Key Name="HKEY_CURRENT_USER"><Key Name="Software"><Key Name="Image-Line">
  <Key Name="Shared"><Key Name="Paths">
    <Value Name="FL Studio engine" Type="2">/Applications/FL Studio 2025.app/Contents/Libs/FLEngine_x64.dylib</Value>
    <Value Name="Shared data" Type="2">/Users/me/Music/My FL Data/</Value></Key></Key>
  <Key Name="FL Studio 21"><Key Name="Search paths"><Value Name="0" Type="2">/Users/me/Old Kits/,</Value></Key></Key>
  <Key Name="FL Studio 25">
    <Key Name="Search paths"><Value Name="0" Type="2">"/Users/me/Desktop/Sampels for FL Studio/",</Value>
      <Value Name="1" Type="2">/Users/me/Desktop/DrumKits/,</Value></Key>
    <Key Name="MRU"><Value Name="0" Type="2">/Volumes/SSD/Projects/Beat/Beat.flp</Value><Value Name="1" Type="2"></Value></Key>
  </Key>
</Key></Key></Key></Key></XMLReg>"""

    def test_reads_the_settings_file(self):
        import xml.etree.ElementTree as ET
        got = rm.fl_settings_from_tree(ET.fromstring(self.REG_XML))
        self.assertEqual(got["user_data"], "/Users/me/Music/My FL Data/")
        self.assertEqual(got["search_paths"], ["/Users/me/Desktop/Sampels for FL Studio/", "/Users/me/Desktop/DrumKits/",
                                               "/Users/me/Old Kits/"], "newest FL first, quotes and commas gone")
        self.assertEqual(got["recent"], ["/Volumes/SSD/Projects/Beat/Beat.flp"])

    def test_search_folders_and_recent_projects_move(self):
        h = self.tmp / "home"
        library = h / "Desktop" / "My Library"                        # no project uses it, but FL browses it
        write(library / "Pack" / "loop.wav", b"l" * 50)
        elsewhere = self.tmp / "D" / "Projects" / "Album" / "song.flp"  # a recent project outside the usual folders
        make_flp(elsewhere, [])
        fl = {"user_data": None, "search_paths": [str(library) + "/"], "recent": [str(elsewhere)]}
        with as_home(h), quiet(), mock.patch.object(rm, "fl_settings", lambda: fl):
            items = rm.pack_items([], set())
        by_path = {Path(it["source_path"]): it for it in items}
        self.assertIn(library, by_path)
        self.assertEqual(by_path[library]["label"], "FL Studio browser folder: My Library")
        self.assertIn(elsewhere.parent, by_path, "the recent project's folder is found too")

    def test_a_custom_fl_user_data_folder_is_used(self):
        h = self.tmp / "home"
        custom = h / "Music" / "My FL Data"
        custom.mkdir(parents=True)
        with as_home(h), mock.patch.object(rm, "fl_settings", lambda: {"user_data": str(custom) + "/",
                                                                       "search_paths": [], "recent": []}):
            self.assertEqual(rm.known_roots(rm.THIS_OS, h)["fl_user"][1], custom)

    def test_unpack_lists_the_browser_folders_to_add_back(self):
        old, new, drive = self.tmp / "old", self.tmp / "new", self.tmp / "drive"
        library = write(old / "Desktop" / "My Library" / "loop.wav", b"l").parent
        drive.mkdir()
        new.mkdir()
        fl = {"user_data": None, "search_paths": [str(library)], "recent": []}
        with as_home(old), quiet(), mock.patch.object(rm, "fl_settings", lambda: fl):
            rm.cmd_pack(argparse.Namespace(to=str(drive), list=False, include=[], skip=[], include_programs=False))
        out = io.StringIO()
        with as_home(new), contextlib.redirect_stdout(out):
            rm.cmd_unpack(argparse.Namespace(bundle=str(next(drive.glob("Freighter-*"))), dest_for=[]))
        self.assertIn("Browser extra search folders", out.getvalue())
        self.assertIn(str(new / "Desktop" / "My Library"), out.getvalue())


class OneDriveFolders(TempDirCase):
    """Windows can keep Desktop and Documents somewhere else, usually in OneDrive (issue #6)."""

    def setUp(self):
        super().setUp()
        self.h = self.tmp / "home"
        self.docs = self.h / "OneDrive" / "Documents"
        self.desk = self.h / "OneDrive" / "Desktop"
        self.docs.mkdir(parents=True)
        self.desk.mkdir(parents=True)

    @contextlib.contextmanager
    def windows_with_onedrive(self):
        env = {k: v for k, v in os.environ.items() if k != "FREIGHTER_HOME"}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(rm, "THIS_OS", "windows"), \
                mock.patch.object(rm, "real_home", lambda: self.h), \
                mock.patch.object(rm, "_registry_folders", lambda: {"Documents": self.docs, "Desktop": self.desk}):
            yield

    def test_known_folders_follow_moved_documents(self):
        with self.windows_with_onedrive():
            roots = rm.known_roots("windows", self.h)
            self.assertEqual(roots["fl_user"][1], self.docs / "Image-Line")
            self.assertEqual(roots["ableton_user_library"][1], self.docs / "Ableton" / "User Library")
            self.assertEqual(rm.user_folders()["Music"], self.h / "Music", "unmoved folders stay put")

    def test_other_folders_never_use_the_real_folders(self):
        """Asking about any folder other than the real home must not return this computer's moved folders."""
        with self.windows_with_onedrive():
            other = self.tmp / "someone-else"
            self.assertEqual(rm.known_roots("windows", other)["fl_user"][1], other / "Documents" / "Image-Line")

    def test_fl_data_left_in_the_local_documents_is_still_found(self):
        """Issue #62: Documents moved to OneDrive, but FL's folder stayed in the local Documents."""
        local = self.h / "Documents" / "Image-Line"
        local.mkdir(parents=True)
        with self.windows_with_onedrive():
            self.assertEqual(rm.known_roots("windows", self.h)["fl_user"][1], local)
            self.assertIn(self.h / "Documents", rm.all_user_folders(), "the local Documents is still searched")

    def test_a_rehearsals_stand_in_home_never_uses_the_real_folders(self):
        with self.windows_with_onedrive(), mock.patch.dict(os.environ, {"FREIGHTER_HOME": str(self.h)}):
            self.assertEqual(rm.user_folders()["Documents"], self.h / "Documents")

    def test_projects_and_samples_in_onedrive_are_packed(self):
        kick = write(self.desk / "DrumKits" / "Kicks" / "kick.wav", os.urandom(500))
        loose = write(self.desk / "vox.wav", os.urandom(300))
        make_flp(self.docs / "Image-Line" / "FL Studio" / "Projects" / "Beat.flp", [str(kick), str(loose)])
        with self.windows_with_onedrive(), quiet():
            items = rm.pack_items([], set())
        paths = {it["id"]: Path(it["source_path"]) for it in items}
        self.assertEqual(paths["fl_user"], self.docs / "Image-Line")
        self.assertIn(self.desk / "DrumKits", paths.values(), "the kit folder, not the whole OneDrive Desktop")
        self.assertNotIn(self.desk, [p for i, p in paths.items() if not i.startswith("loose")])
        self.assertTrue(any(it.get("only") == ["vox.wav"] for it in items))

    def test_cloud_only_files_are_recognised(self):
        self.assertTrue(rm.is_cloud_only(mock.Mock(st_file_attributes=0x400000)))
        self.assertFalse(rm.is_cloud_only(mock.Mock(st_file_attributes=0x20)))
        self.assertFalse(rm.is_cloud_only(os.stat(__file__)))


class FullMove(TempDirCase):
    """Pack on one fake home, unpack on another, repair, and check every sample."""

    def build_old_computer(self):
        old = self.tmp / "old"
        kick_name = "kick hard.wav" if os.name == "nt" else "kick: hard.wav"  # ':' is illegal on Windows
        self.kick = write(old / "Desktop" / "DrumKits" / "Kicks" / kick_name, os.urandom(4000))
        self.snare = write(old / "Desktop" / "DrumKits" / "Snares" / "snare.wav", os.urandom(3000))
        self.vox = write(old / "Downloads" / "vox.wav", os.urandom(2000))
        write(old / "Downloads" / "unrelated.zip", os.urandom(50000))
        self.user_library = rm.known_roots(rm.THIS_OS, old)["ableton_user_library"][1]  # differs per OS
        make_als(self.user_library / "Song Project" / "Song.als",
                 [str(self.kick), str(self.vox), "/nowhere/gone.wav"])
        make_flp(old / "Documents" / "Image-Line" / "FL Studio" / "Projects" / "Beat" / "Beat.flp",
                 [str(self.snare), "%FLStudioFactoryData%/Data/x.wav"])
        return old

    def pack(self, old, drive):
        with as_home(old), quiet():
            rm.cmd_pack(argparse.Namespace(to=str(drive), list=False, include=[], skip=[], include_programs=False))
        return next(drive.glob("Freighter-*"))

    @staticmethod
    def item(bundle, name):
        """The bundle item whose folder is called `name` (IDs come from paths, not positions)."""
        items = json.loads((bundle / "manifest.json").read_text())["items"]
        return next(it["id"] for it in items if Path(it["source_path"].replace("\\", "/")).name == name)

    def test_pack_unpack_repair(self):
        old, new, drive = self.build_old_computer(), self.tmp / "new", self.tmp / "drive"
        bundle = self.pack(old, drive)
        manifest = json.loads((bundle / "manifest.json").read_text())
        ids = {it["id"] for it in manifest["items"]}
        self.assertTrue({"fl_user", "ableton_user_library"} <= ids)
        self.assertTrue(any(i.startswith("used-") for i in ids) and any(i.startswith("loose-") for i in ids), ids)
        gone = (bundle / "already-missing.txt").read_text()
        self.assertIn("Song.als: /nowhere/gone.wav", gone, "already-missing samples are listed (issue #15)")
        self.assertFalse(any("unrelated.zip" in p.name for p in (bundle / "data").rglob("*")),
                         "files no project uses must not be packed")

        shutil.rmtree(old)  # the old computer is gone
        new.mkdir()
        with as_home(new), quiet():
            rm.cmd_unpack(argparse.Namespace(bundle=str(bundle), dest_for=[]))
            rm.cmd_fix_ableton(argparse.Namespace(pathmap=None, folder=[], dry_run=False))
            maps = rm.load_pathmaps(None)
        song = rm.known_roots(rm.THIS_OS, new)["ableton_user_library"][1] / "Song Project" / "Song.als"
        text = gzip.decompress(song.read_bytes()).decode()
        for v in [m.group(2) for m in rm.PATH_RE.finditer(text)]:
            if v != "/nowhere/gone.wav":
                self.assertTrue(Path(v).is_file(), v)
        beat = new / "Documents" / "Image-Line" / "FL Studio" / "Projects" / "Beat" / "Beat.flp"
        with as_home(new):
            self.assertEqual(rm.locate(rm.flp_samples(beat)[1][0], maps)[0], "moved")

    def test_resume_after_interruption(self):
        old, drive = self.build_old_computer(), self.tmp / "drive"
        bundle = self.pack(old, drive)
        kit = self.item(bundle, "DrumKits")
        index = bundle / "index" / f"{kit}.jsonl"
        lines = index.read_text().splitlines()      # a header naming the folder, then one line per file
        victim = json.loads(lines[-1])
        (bundle / "data" / kit / victim["stored"]).unlink()
        index.write_text("\n".join(lines[:-1]) + '\n{"rel": "half-writ')  # cut mid-line
        self.pack(old, drive)
        self.assertTrue((bundle / "data" / kit / victim["stored"]).is_file())
        recs = rm.read_jsonl(index)
        self.assertEqual(len(recs), len(lines) - 1)

    def test_resume_after_the_list_changes_keeps_folders_apart(self):
        """Issue #57: a folder that appears between an interrupted pack and its resume used to take over another
        folder's records, so the new computer got the wrong files under the right names."""
        old, new, drive = self.tmp / "old", self.tmp / "new", self.tmp / "drive"
        beats = write(old / "Desktop" / "Beats" / "kick.wav", b"B" * 500)
        kits = write(old / "Desktop" / "Kits" / "kick.wav", b"K" * 500)
        projects = old / "Documents" / "Image-Line" / "FL Studio" / "Projects"
        make_flp(projects / "One.flp", [str(beats), str(kits)])
        bundle = self.pack(old, drive)
        ids_before = {it["source_path"]: it["id"] for it in json.loads((bundle / "manifest.json").read_text())["items"]}
        # a new folder that sorts first, with a same-named, same-sized file, appears before the resume
        album = write(old / "Desktop" / "Album" / "kick.wav", b"A" * 500)
        make_flp(projects / "Two.flp", [str(album)])
        self.pack(old, drive)
        items = json.loads((bundle / "manifest.json").read_text())["items"]
        for it in items:
            if it["source_path"] in ids_before:
                self.assertEqual(it["id"], ids_before[it["source_path"]], "an item keeps its ID")
        new.mkdir()
        with as_home(new), quiet():
            rm.cmd_unpack(argparse.Namespace(bundle=str(bundle), dest_for=[]))
        found = {p.parent.name: p.read_bytes()[:1] for p in new.rglob("kick.wav")}
        self.assertEqual(found, {"Album": b"A", "Beats": b"B", "Kits": b"K"})

    def test_an_index_from_another_folder_is_set_aside(self):
        old, drive = self.build_old_computer(), self.tmp / "drive"
        bundle = self.pack(old, drive)
        kit = self.item(bundle, "DrumKits")
        index = bundle / "index" / f"{kit}.jsonl"
        lines = index.read_text().splitlines()
        lines[0] = json.dumps({"item": kit, "source_path": "/somewhere/else"})
        index.write_text("\n".join(lines) + "\n")
        self.pack(old, drive)
        self.assertTrue((bundle / "index" / f"{kit}.set-aside-1.jsonl").is_file())
        self.assertEqual(json.loads(index.read_text().splitlines()[0])["source_path"],
                         next(it["source_path"] for it in json.loads((bundle / "manifest.json").read_text())["items"]
                              if it["id"] == kit))

    # ---- issue #59: bundles are checked as a whole

    def test_unpack_refuses_an_unfinished_bundle(self):
        old, new, drive = self.build_old_computer(), self.tmp / "new", self.tmp / "drive"
        bundle = self.pack(old, drive)
        (bundle / rm.COMPLETE).unlink()                      # as if packing had been interrupted
        new.mkdir()
        with as_home(new), quiet(), self.assertRaises(SystemExit) as e:
            rm.cmd_unpack(argparse.Namespace(bundle=str(bundle), dest_for=[]))
        self.assertIn("isn't complete", str(e.exception.code))
        self.assertFalse(list(new.rglob("*.wav")), "nothing was unpacked")
        with as_home(new), quiet():
            rm.cmd_unpack(argparse.Namespace(bundle=str(bundle), dest_for=[], anyway=True))
        report = next(new.rglob("unpack-report.txt")).read_text()
        self.assertIn("WASN'T COMPLETE", report)

    def test_a_short_item_is_caught(self):
        old, drive = self.build_old_computer(), self.tmp / "drive"
        bundle = self.pack(old, drive)
        index = bundle / "index" / f"{self.item(bundle, 'DrumKits')}.jsonl"
        index.write_text("\n".join(index.read_text().splitlines()[:-1]) + "\n")   # one file never recorded
        problems = rm.bundle_problems(bundle, json.loads((bundle / "manifest.json").read_text()))
        self.assertTrue(any("of 2 files were packed" in p for p in problems), problems)

    def test_resuming_the_next_day_continues_the_same_bundle(self):
        old, drive = self.build_old_computer(), self.tmp / "drive"
        bundle = self.pack(old, drive)
        (bundle / rm.COMPLETE).unlink()
        yesterday = bundle.with_name(bundle.name[:-10] + "2000-01-01")
        bundle.rename(yesterday)
        self.pack(old, drive)
        self.assertEqual([p.name for p in drive.iterdir()], [yesterday.name], "no second bundle was started")
        self.assertTrue((yesterday / rm.COMPLETE).is_file())

    def test_a_changed_file_of_the_same_size_is_copied_again(self):
        old, drive = self.build_old_computer(), self.tmp / "drive"
        bundle = self.pack(old, drive)
        self.snare.write_bytes(b"R" * self.snare.stat().st_size)          # re-rendered, same length
        later = self.snare.stat().st_mtime + 1000
        os.utime(self.snare, (later, later))
        self.pack(old, drive)
        kit = self.item(bundle, "DrumKits")
        rec = rm.read_jsonl(bundle / "index" / f"{kit}.jsonl")["Snares/snare.wav"]
        self.assertEqual((bundle / "data" / kit / rec["stored"]).read_bytes()[:1], b"R")

    def test_unpack_shows_the_old_computers_reports(self):
        old, new, drive = self.build_old_computer(), self.tmp / "new", self.tmp / "drive"
        bundle = self.pack(old, drive)
        new.mkdir()
        out = io.StringIO()
        with as_home(new), contextlib.redirect_stdout(out):
            rm.cmd_unpack(argparse.Namespace(bundle=str(bundle), dest_for=[]))
        self.assertIn("1 sample was already missing on the old computer", out.getvalue())
        self.assertTrue(list(new.rglob("already-missing.txt")))
        self.assertIn("Done. Every file is in place", out.getvalue())

    # ---- issue #73: preview, undo and top-ups

    def unpack(self, bundle, new, **kw):
        with as_home(new), quiet(), mock.patch.object(rm, "fl_is_running", lambda: False):
            rm.cmd_unpack(argparse.Namespace(bundle=str(bundle), dest_for=[], **kw))
            if not kw.get("preview"):
                rm.cmd_fix_fl(argparse.Namespace(pathmap=None, folder=[], include_backups=False, dry_run=False))

    def test_preview_changes_nothing(self):
        old, new, drive = self.build_old_computer(), self.tmp / "new", self.tmp / "drive"
        bundle = self.pack(old, drive)
        new.mkdir()
        self.unpack(bundle, new, preview=True)
        self.assertEqual(list(new.iterdir()), [], "a preview creates nothing")
        dests = [Path(x["dest"]) for x in rm.LAST["unpack_preview"]]
        self.assertIn(new / "Desktop" / "DrumKits", dests)

    def test_undo_removes_what_unpack_made_and_keeps_your_changes(self):
        old, new, drive = self.build_old_computer(), self.tmp / "new", self.tmp / "drive"
        bundle = self.pack(old, drive)
        mine = write(new / "Desktop" / "my notes.txt", b"mine")                  # already on the new computer
        # a project already on the new computer that uses the old computer's snare path: repair rewrites it
        own = make_flp(new / "Documents" / "Old Beats" / "Old Beat.flp", [str(self.snare)])
        own_before = own.read_bytes()
        shutil.rmtree(old)
        self.unpack(bundle, new)
        kick = next((new / "Desktop" / "DrumKits" / "Kicks").iterdir())
        edited = new / "Desktop" / "DrumKits" / "Snares" / "snare.wav"
        edited.write_bytes(b"I changed this")                                   # the user's work now
        self.assertNotEqual(own.read_bytes(), own_before, "repair rewrote the existing project")
        with as_home(new):
            r = rm.undo_unpack()
        self.assertEqual(r["restored"], 1)
        self.assertEqual(own.read_bytes(), own_before, "the project already here is back as it was")
        self.assertFalse((own.parent / "Freighter backup").exists())
        self.assertFalse(kick.exists(), "unpacked files are gone")
        self.assertEqual(edited.read_bytes(), b"I changed this", "a file changed since the unpack is kept")
        self.assertIn(str(edited), r["kept"])
        self.assertEqual(mine.read_bytes(), b"mine", "files that were already here are untouched")
        self.assertFalse((new / "Documents" / "Image-Line").exists(), "folders the unpack created are removed")
        landing = new / "Music" / "Freighter" / json.loads((bundle / "manifest.json").read_text())["source_host"]
        self.assertFalse(landing.exists(), "the unpack's own folder (path map, reports, journal) is gone")
        self.assertFalse((new / "Music").exists(), "and so are the folders it made to hold it")

    def test_a_top_up_replaces_untouched_files_and_keeps_edited_ones(self):
        old, new, drive = self.build_old_computer(), self.tmp / "new", self.tmp / "drive"
        bundle = self.pack(old, drive)
        new.mkdir()
        self.unpack(bundle, new)
        kit = new / "Desktop" / "DrumKits"
        snare_here = kit / "Snares" / "snare.wav"
        kick_here = next((kit / "Kicks").iterdir())
        kick_here.write_bytes(b"edited on the new computer")
        self.snare.write_bytes(b"N" * 3000)                      # both changed on the old computer since
        self.kick.write_bytes(b"M" * 4000)
        later = self.snare.stat().st_mtime + 100
        os.utime(self.snare, (later, later))
        os.utime(self.kick, (later, later))
        bundle = self.pack(old, drive)
        self.unpack(bundle, new)
        self.assertEqual(snare_here.read_bytes()[:1], b"N", "untouched: the newer one replaced it")
        self.assertFalse(list(kit.rglob("snare (from *")), "no side copy for an untouched file")
        self.assertEqual(kick_here.read_bytes(), b"edited on the new computer", "an edited file is kept")
        self.assertTrue(list(kit.rglob("* (from *).wav")), "and the newer one is saved next to it")

    def test_unpacking_again_doesnt_read_what_it_already_copied(self):
        """Issue #74: the journal says what an earlier unpack copied; unchanged files aren't read again."""
        old, new, drive = self.build_old_computer(), self.tmp / "new", self.tmp / "drive"
        bundle = self.pack(old, drive)
        new.mkdir()
        self.unpack(bundle, new)
        read = []
        real = rm.hash_file
        with mock.patch.object(rm, "hash_file", lambda p: read.append(Path(p)) or real(p)):
            self.unpack(bundle, new)
        self.assertEqual([p for p in read if new in p.parents], [])

    def test_a_same_size_edit_after_unpacking_is_still_noticed(self):
        old, new, drive = self.build_old_computer(), self.tmp / "new", self.tmp / "drive"
        bundle = self.pack(old, drive)
        new.mkdir()
        self.unpack(bundle, new)
        kick_here = next((new / "Desktop" / "DrumKits" / "Kicks").iterdir())
        edit = os.urandom(4000)                       # same size as the original kick
        kick_here.write_bytes(edit)
        self.unpack(bundle, new)
        self.assertEqual(kick_here.read_bytes(), edit, "the edit is kept")
        self.assertTrue(list((new / "Desktop" / "DrumKits").rglob("* (from *).wav")), "the moved one sits next to it")

    def test_unpack_detects_damage_and_keeps_existing_files(self):
        old, new, drive = self.build_old_computer(), self.tmp / "new", self.tmp / "drive"
        bundle = self.pack(old, drive)
        kit = self.item(bundle, "DrumKits")
        rec = next(iter(rm.read_jsonl(bundle / "index" / f"{kit}.jsonl").values()))
        damaged = bundle / "data" / kit / rec["stored"]
        damaged.write_bytes(b"\0" * damaged.stat().st_size)  # same size, wrong content
        # a different file already sits where the snare goes
        landing = new / "Music" / "Freighter" / json.loads((bundle / "manifest.json").read_text())["source_host"]
        existing = write(landing / "DrumKits" / "Snares" / "snare.wav", b"mine")
        with as_home(new), quiet():
            rm.cmd_unpack(argparse.Namespace(bundle=str(bundle), dest_for=[], keep_together=True))
        report = (landing / "unpack-report.txt").read_text()
        self.assertIn("checksum mismatch", report)
        self.assertEqual(existing.read_bytes(), b"mine")
        self.assertTrue(list((landing / "DrumKits" / "Snares").glob("snare (from *).wav")))

    def test_unpack_refuses_a_bundle_from_a_newer_freighter(self):
        """Issue #14."""
        bundle = self.tmp / "Freighter-x"
        write(bundle / "manifest.json", json.dumps({"bundle_version": rm.BUNDLE_VERSION + 1,
                                                    "freighter_version": "9.9.9", "source_host": "x",
                                                    "source_os": "windows", "items": []}).encode())
        with as_home(self.tmp / "new"), self.assertRaises(SystemExit) as e:
            rm.cmd_unpack(argparse.Namespace(bundle=str(bundle), dest_for=[]))
        self.assertIn("9.9.9", str(e.exception.code))
        self.assertIn("Update Freighter", str(e.exception.code))

    def test_unpack_stops_before_copying_when_a_disk_is_full(self):
        """Issue #12."""
        old, new, drive = self.build_old_computer(), self.tmp / "new", self.tmp / "drive"
        bundle = self.pack(old, drive)
        new.mkdir()
        with as_home(new), quiet(), mock.patch.object(rm.shutil, "disk_usage", lambda p: mock.Mock(free=1000)):
            with self.assertRaises(SystemExit) as e:
                rm.cmd_unpack(argparse.Namespace(bundle=str(bundle), dest_for=[]))
        self.assertIn("Not enough free space", str(e.exception.code))
        self.assertFalse([p for p in new.rglob("*.wav")], "nothing was copied")

    def test_unpack_twice_copies_nothing_new(self):
        old, new, drive = self.build_old_computer(), self.tmp / "new", self.tmp / "drive"
        bundle = self.pack(old, drive)
        new.mkdir()
        with as_home(new), quiet():
            rm.cmd_unpack(argparse.Namespace(bundle=str(bundle), dest_for=[]))
            before = sorted(p for p in new.rglob("*") if p.is_file() and p.suffix != ".txt" and p.suffix != ".json")
            rm.cmd_unpack(argparse.Namespace(bundle=str(bundle), dest_for=[]))
            after = sorted(p for p in new.rglob("*") if p.is_file() and p.suffix != ".txt" and p.suffix != ".json")
        self.assertEqual(before, after)

    def test_rehearsal_passes_on_a_good_setup(self):
        old = self.build_old_computer()
        with as_home(old), quiet():
            code = rm.cmd_rehearse(argparse.Namespace(to=str(old / "rehearsal"), include=[], skip=[]))
            self.assertEqual(rm.home(), old, "after rehearsing, the real home folder is back (issue #16)")
            self.assertIsNone(rm.HIDE)
        self.assertEqual(code, 0)
        report = (old / "rehearsal" / "Freighter Rehearsal" / "rehearsal-report.txt").read_text()
        self.assertIn("LOST in the move: 0", report)
        self.assertIn("Song.als: /nowhere/gone.wav", report, "already-missing samples are listed (issue #15)")
        self.assertIn("still found after the move: 3", report)
        self.assertTrue(self.kick.is_file(), "the real files must not be touched")

    def test_rehearsal_tests_the_fl_repair_even_while_fl_is_open(self):
        """It repairs its own copies, which FL can't have open, so an open FL mustn't skip that part."""
        old = self.build_old_computer()
        with as_home(old), quiet(), mock.patch.object(rm, "fl_is_running", lambda: True):
            code = rm.cmd_rehearse(argparse.Namespace(to=str(old / "rehearsal"), include=[], skip=[]))
        self.assertEqual(code, 0)
        repaired = list((old / "rehearsal" / "Freighter Rehearsal").rglob("Freighter backup/Beat.flp"))
        self.assertEqual(len(repaired), 1, "the FL project copy was repaired")

    def test_a_leftover_rehearsal_is_never_packed(self):
        """Issue #18: a rehearsal folder left on the Desktop must not be packed by the next move."""
        old = self.build_old_computer()
        with as_home(old), quiet():
            rm.cmd_rehearse(argparse.Namespace(to=str(old / "Desktop" / "my rehearsal"), include=[], skip=[]))
            items = rm.pack_items([], set())
        for it in items:
            self.assertNotIn("my rehearsal", it["source_path"])
        with as_home(old):
            self.assertFalse([p for p in rm.find_projects([old], ".als") if "my rehearsal" in str(p)])

    def test_reports_and_rehearsals_default_to_music_freighter(self):
        with as_home(self.tmp):
            self.assertEqual(rs.reports_dir(), self.tmp / "Music" / "Freighter" / "Reports")

    def test_rehearsal_catches_a_moved_sample_with_the_wrong_contents(self):
        """Issue #65: the rehearsal compares contents, so a mix-up can't pass just because a file exists."""
        old = self.build_old_computer()

        def damage(_args):   # stands in for any bug that puts the wrong audio where the snare should be
            moved = next((Path(os.environ["FREIGHTER_HOME"])).rglob("snare.wav"))
            moved.write_bytes(b"\0" * moved.stat().st_size)
        with as_home(old), quiet(), mock.patch.object(rm, "cmd_fix_fl", side_effect=damage):
            code = rm.cmd_rehearse(argparse.Namespace(to=str(old / "rehearsal"), include=[], skip=[]))
        report = (old / "rehearsal" / "Freighter Rehearsal" / "rehearsal-report.txt").read_text()
        self.assertEqual(code, 2)
        self.assertIn("LOST in the move: 1", report)
        self.assertIn("snare.wav", report)

    def test_rehearsal_checks_fl_user_data_samples(self):
        """%FLStudioUserData% samples (half of a real setup's references) used to be skipped as FL's own sounds."""
        old = self.tmp / "old"
        rec = write(old / "Documents" / "Image-Line" / "FL Studio" / "Projects" / "Beat" / "Audio" / "take 1.wav",
                    b"my recording")
        make_flp(old / "Documents" / "Image-Line" / "FL Studio" / "Projects" / "Beat" / "Beat.flp",
                 ["%FLStudioUserData%/Projects/Beat/Audio/take 1.wav", "%FLStudioFactoryData%/Data/x.wav"])
        with as_home(old), quiet():
            code = rm.cmd_rehearse(argparse.Namespace(to=str(old / "rehearsal"), include=[], skip=[]))
        report = (old / "rehearsal" / "Freighter Rehearsal" / "rehearsal-report.txt").read_text()
        self.assertEqual(code, 0)
        self.assertIn("still found after the move: 1", report)
        self.assertTrue(rec.is_file())

    def test_rehearsal_restores_folders_even_when_it_fails(self):
        old = self.build_old_computer()
        with as_home(old), quiet(), mock.patch.object(rm, "cmd_fix_ableton", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                rm.cmd_rehearse(argparse.Namespace(to=str(old / "rehearsal"), include=[], skip=[]))
            self.assertEqual(rm.home(), old)
            self.assertIsNone(rm.HIDE)


class Menu(TempDirCase):
    def test_a_crash_keeps_the_window_open_and_saves_a_report(self):
        answers = iter(["2", "", "", "q"])     # option 2, all projects, Enter after the error, quit
        with as_home(self.tmp), quiet(), \
                mock.patch("builtins.input", lambda *_: next(answers)), \
                mock.patch.object(rm, "cmd_pack", side_effect=ValueError("disk on fire")):
            code = rm.interactive_menu()
        self.assertEqual(code, 0, "the menu came back after the error instead of closing")
        report = (self.tmp / "Music" / "Freighter" / "freighter-crash.txt").read_text()
        self.assertIn(f"Freighter {rm.__version__}", report)
        self.assertIn("ValueError: disk on fire", report)
        self.assertIn("Menu choice: 2", report)

    def test_the_menu_asks_which_projects(self):
        """Issue #76: Enter means all projects; a time range is asked again until it makes sense."""
        seen = []
        answers = iter(["2", "banana", "Mar 2024 - Mar 2026", "", "q"])
        with as_home(self.tmp), quiet(), mock.patch("builtins.input", lambda *_: next(answers)), \
                mock.patch.object(rm, "cmd_pack", lambda a: seen.append((a.since, a.until)) or 0):
            rm.interactive_menu()
        self.assertEqual(seen, [("2024-03", "2026-03")])

    def test_next_step_commands_match_how_freighter_was_started(self):
        """The built program has no manifest.py; tell people the command that exists (issue #21)."""
        with mock.patch.object(rm.sys, "frozen", True, create=True), mock.patch.object(rs, "IS_WIN", True):
            self.assertEqual(rm.tool_cmd("manifest", "--compare"), "manifest.exe --compare")
        with mock.patch.object(rm.sys, "frozen", True, create=True), mock.patch.object(rs, "IS_WIN", False):
            self.assertEqual(rm.tool_cmd("manifest"), "./manifest")
        with mock.patch.object(rs, "IS_WIN", False):
            self.assertEqual(rm.tool_cmd("freighter", "check-fl"), "python3 freighter.py check-fl")

    def test_closed_input_quits(self):
        def closed(*_):
            raise EOFError
        with quiet(), mock.patch("builtins.input", closed):
            self.assertEqual(rm.interactive_menu(), 0)


class CrossOSMove(TempDirCase):
    """Issue #20: a bundle packed on Windows unpacks and repairs here, and so does one packed on a Mac.
    The bundles are built exactly as pack writes them, so both directions run on every CI machine."""

    def bundle(self, source_os, items, name=None):
        b = self.tmp / (name or f"Freighter-OLD{source_os.upper()}-2026-01-01")
        manifest = {"bundle_version": rm.BUNDLE_VERSION, "freighter_version": rm.__version__,
                    "created": "2026-01-01T12:00:00", "source_host": f"OLD{source_os.upper()}", "source_os": source_os,
                    "source_home": items[0]["home"], "items": []}
        for it in items:
            manifest["items"].append({k: it[k] for k in ("id", "label", "source_path", "kind", "plugin_data") if k in it})
            taken, lines = set(), []
            for rel, data in it["files"].items():
                stored = rm.unique_stored(rel, taken)
                write(b / "data" / it["id"] / stored, data)
                lines.append(json.dumps({"rel": rel, "stored": stored, "size": len(data),
                                         "sha256": hashlib.sha256(data).hexdigest()}))
            write(b / "index" / f"{it['id']}.jsonl", ("\n".join(lines) + "\n").encode())
        write(b / "manifest.json", json.dumps(manifest).encode())
        write(b / rm.COMPLETE, json.dumps({"errors": 0}).encode())   # as a finished pack leaves it
        return b

    def test_presets_from_another_kind_of_computer_are_pointed_out(self):
        """Issue #31: across Windows and Mac, presets can't go back in place, so the unpack says where they are."""
        other = "windows" if rm.THIS_OS == "mac" else "mac"
        h = "C:\\Users\\pat" if other == "windows" else "/Users/pat"
        sep = "\\" if other == "windows" else "/"
        b = self.bundle(other, [{"id": "plugin-1", "kind": "extra", "label": "Plugin presets and data: Xfer", "home": h,
                                 "plugin_data": True, "source_path": sep.join([h, "Documents", "Xfer"]),
                                 "files": {"Serum Presets/User/Bass.fxp": b"preset"}}])
        new = self.tmp / "new"
        new.mkdir()
        with as_home(new), quiet():
            rm.cmd_unpack(argparse.Namespace(bundle=str(b), dest_for=[]))
        u = rm.LAST["unpack"]
        self.assertTrue(u["cross_os"])
        self.assertEqual(len(u["plugin_data"]), 1)
        landed = Path(u["plugin_data"][0]["new"])
        self.assertEqual((landed / "Serum Presets" / "User" / "Bass.fxp").read_bytes(), b"preset")
        self.assertIn(new / "Music" / "Freighter", landed.parents)

    def projects(self, kick_ref, snare_ref):
        stage = self.tmp / "stage"
        als = make_als(stage / "Song.als", [kick_ref]).read_bytes()
        raw = gzip.decompress(make_als(stage / "Raw.als", [kick_ref]).read_bytes())   # saved uncompressed
        flp = make_flp(stage / "Beat.flp", [snare_ref]).read_bytes()
        return als, raw, flp

    def move(self, source_os, h, sep, ul, dest_for=()):
        kit = sep.join([h, "Desktop", "DrumKits"])
        kick_old = sep.join([kit, "Kicks", "Kick 01 & Co.wav"])
        snare_old = sep.join([kit, "Snares", "snare.wav"])
        # Live on Windows writes forward slashes; FL writes backslashes
        als, raw, flp = self.projects(kick_old.replace("\\", "/"), snare_old)
        b = self.bundle(source_os, [
            {"id": "fl_user", "kind": "known", "label": "FL", "home": h,
             "source_path": sep.join([h, "Documents", "Image-Line"]),
             "files": {"FL Studio/Projects/Beat/Beat.flp": flp}},
            {"id": "ableton_user_library", "kind": "known", "label": "UL", "home": h, "source_path": ul,
             "files": {"Song Project/Song.als": als, "Raw Project/Raw.als": raw}},
            {"id": "used1", "kind": "extra", "label": "Samples: DrumKits", "home": h, "source_path": kit,
             "files": {"Kicks/Kick 01 & Co.wav": b"kick", "Snares/snare.wav": b"snare"}},
        ])
        new = self.tmp / "new"
        new.mkdir()
        with as_home(new), quiet():
            rm.cmd_unpack(argparse.Namespace(bundle=str(b), dest_for=list(dest_for)))
            rm.cmd_fix_ableton(argparse.Namespace(pathmap=None, folder=[], dry_run=False))
            rm.cmd_fix_fl(argparse.Namespace(pathmap=None, folder=[], include_backups=False, dry_run=False))
            user_library = rm.known_roots(rm.THIS_OS, new)["ableton_user_library"][1]
        return new, user_library

    def assert_repaired(self, new, user_library):
        for name, compressed in (("Song Project/Song.als", True), ("Raw Project/Raw.als", False)):
            p = user_library / name
            self.assertTrue(p.is_file(), f"{name} landed in this computer's User Library")
            text, gz = rm.read_als(p)
            self.assertEqual(gz, compressed, "repair keeps the file's compression")
            refs = [html.unescape(m.group(2)) for m in rm.PATH_RE.finditer(text)]
            self.assertEqual(len(refs), 1)
            self.assertTrue(Path(refs[0]).is_file(), f"{name} points at the moved kick: {refs[0]}")
            self.assertEqual(Path(refs[0]).read_bytes(), b"kick")
            self.assertIn("&amp;", text, "the & in the file name is escaped in the XML")
        fl = (new / "Music" / "Freighter" / "fl-repair-report.txt").read_text(encoding="utf-8")
        self.assertIn("fixed=1 missing=0", fl)
        beat = new / "Documents" / "Image-Line" / "FL Studio" / "Projects" / "Beat" / "Beat.flp"
        now = rm.flp_samples(beat)[1][0]
        self.assertEqual(Path(now).read_bytes(), b"snare", "the FL project itself now points at the moved snare")
        self.assertTrue((beat.parent / "Freighter backup" / "Beat.flp").is_file(), "the original is kept")

    def test_windows_to_here(self):
        h = "C:\\Users\\Producer"
        self.assert_repaired(*self.move("windows", h, "\\", h + "\\Documents\\Ableton\\User Library"))

    def test_mac_to_here(self):
        h = "/Users/producer"
        self.assert_repaired(*self.move("mac", h, "/", h + "/Music/Ableton/User Library"))

    def test_same_named_folders_from_different_places_stay_apart(self):
        """Issue #58: Desktop\\Samples and D:\\Samples used to land in one folder, so repair could pick the wrong kick."""
        h = "C:\\Users\\Producer"
        stage = self.tmp / "stage"
        als = make_als(stage / "Song.als", ["D:/Samples/Kicks/kick.wav"]).read_bytes()
        b = self.bundle("windows", [
            {"id": "ableton_user_library", "kind": "known", "label": "UL", "home": h,
             "source_path": h + "\\Documents\\Ableton\\User Library", "files": {"Song Project/Song.als": als}},
            {"id": "used-desk", "kind": "extra", "label": "Samples: Samples", "home": h,
             "source_path": h + "\\Desktop\\Samples", "files": {"Kicks/kick.wav": b"desktop kick"}},
            {"id": "used-ddrv", "kind": "extra", "label": "Samples: Samples", "home": h,
             "source_path": "D:\\Samples", "files": {"Kicks/kick.wav": b"d drive kick"}},
        ])
        new = self.tmp / "new"
        new.mkdir()
        with as_home(new), quiet():
            rm.cmd_unpack(argparse.Namespace(bundle=str(b), dest_for=[], keep_together=True))
            rm.cmd_fix_ableton(argparse.Namespace(pathmap=None, folder=[], dry_run=False))
            song = rm.known_roots(rm.THIS_OS, new)["ableton_user_library"][1] / "Song Project" / "Song.als"
        landing = new / "Music" / "Freighter" / "OLDWINDOWS"
        self.assertEqual(sorted(p.name for p in landing.iterdir() if p.is_dir()), ["Samples", "Samples (D drive)"])
        ref = html.unescape(rm.PATH_RE.search(rm.read_als(song)[0]).group(2))
        self.assertEqual(Path(ref).read_bytes(), b"d drive kick", "repair points at D:'s kick, not the Desktop's")

    def test_a_top_up_bundle_keeps_the_first_bundles_paths(self):
        h = "/Users/producer"
        first = self.bundle("mac", [{"id": "used-a", "kind": "extra", "label": "A", "home": h,
                                     "source_path": h + "/Desktop/Kits", "files": {"k.wav": b"k"}}], name="Freighter-1")
        second = self.bundle("mac", [{"id": "used-b", "kind": "extra", "label": "B", "home": h,
                                      "source_path": h + "/Desktop/Loops", "files": {"l.wav": b"l"}}], name="Freighter-2")
        new = self.tmp / "new"
        new.mkdir()
        with as_home(new), quiet():
            rm.cmd_unpack(argparse.Namespace(bundle=str(first), dest_for=[]))
            rm.cmd_unpack(argparse.Namespace(bundle=str(second), dest_for=[]))
            maps = rm.load_pathmaps(None)
        olds = sorted(e["old"] for m in maps for e in m["entries"])
        self.assertEqual(olds, [h + "/Desktop/Kits", h + "/Desktop/Loops"], "the second unpack kept the first's map")
        self.assertEqual(rm.locate(h + "/Desktop/Kits/k.wav", maps)[0], "moved")

    def test_renamed_files_are_found_by_their_record_on_windows(self):
        """Issue #63: kick?.wav and kick_.wav clash on a Windows drive; repair must not hand kick_.wav's projects
        the other file's audio."""
        h = "/Users/producer"
        b = self.bundle("mac", [{"id": "used-k", "kind": "extra", "label": "Kits", "home": h,
                                 "source_path": h + "/Desktop/Kits",
                                 "files": {"kick?.wav": b"question kick", "kick_.wav": b"underscore kick"}}])
        new = self.tmp / "new"
        new.mkdir()
        with as_home(new), quiet(), mock.patch.object(rm, "THIS_OS", "windows"):
            rm.cmd_unpack(argparse.Namespace(bundle=str(b), dest_for=[]))
            maps = rm.load_pathmaps(None)
            got = {name: rm.locate(f"{h}/Desktop/Kits/{name}", maps) for name in ("kick_.wav", "kick?.wav")}
        self.assertEqual(got["kick_.wav"][0], "moved")
        self.assertEqual(got["kick_.wav"][1].read_bytes(), b"underscore kick")
        self.assertEqual(got["kick?.wav"][1].read_bytes(), b"question kick")

    # ---- issue #67: same-places restore

    def test_same_os_moves_put_folders_back_where_they_were(self):
        """Desktop folders go back to the Desktop (following OneDrive or not), not to Music/Freighter."""
        old_home = str(self.tmp / "oldhome")
        kits = os.path.join(old_home, "OneDrive", "Desktop", "Kits")
        b = self.bundle(rm.THIS_OS, [{"id": "used-k", "kind": "extra", "label": "Kits", "home": old_home,
                                      "source_path": kits, "files": {"kick.wav": b"kick"}}])
        m = json.loads((b / "manifest.json").read_text())
        m["user_folders"] = {"Desktop": os.path.join(old_home, "OneDrive", "Desktop"),
                             "Documents": os.path.join(old_home, "Documents"), "Music": os.path.join(old_home, "Music")}
        (b / "manifest.json").write_text(json.dumps(m))
        new = self.tmp / "new"
        new.mkdir()
        with as_home(new), quiet():
            rm.cmd_unpack(argparse.Namespace(bundle=str(b), dest_for=[]))
            maps = rm.load_pathmaps(None)
            status, where = rm.locate(os.path.join(kits, "kick.wav"), maps)
        self.assertEqual((new / "Desktop" / "Kits" / "kick.wav").read_bytes(), b"kick", "back on this Desktop")
        self.assertFalse((new / "Music" / "Freighter" / b.name.split("-")[1] / "Kits").exists())
        self.assertEqual(where, new / "Desktop" / "Kits" / "kick.wav")

    def test_other_drives_come_back_only_if_this_computer_has_them(self):
        m = {"source_os": rm.THIS_OS, "source_home": str(self.tmp / "oldhome"), "user_folders": {}}
        here = str(self.tmp / "SSD" / "Samples")                     # a place that exists on "this" computer
        (self.tmp / "SSD").mkdir()
        self.assertEqual(rm.same_place(m, here), Path(here))
        gone = "Q:\\Samples" if rm.THIS_OS == "windows" else "/Volumes/NoSuchDrive/Samples"
        self.assertIsNone(rm.same_place(m, gone), "a drive this computer doesn't have: use the Freighter folder")
        self.assertEqual(rm.same_place(m, gone, rehearsal_root=self.tmp / "r").parts[-1], "Samples",
                         "a rehearsal recreates other drives inside its own folder")
        self.assertTrue(str(rm.same_place(m, gone, rehearsal_root=self.tmp / "r")).startswith(str(self.tmp / "r")))
        other_os = dict(m, source_os="mac" if rm.THIS_OS == "windows" else "windows")
        self.assertIsNone(rm.same_place(other_os, here), "cross-OS moves still use the Freighter folder")

    def test_keep_together_uses_the_freighter_folder(self):
        old_home = str(self.tmp / "oldhome")
        b = self.bundle(rm.THIS_OS, [{"id": "used-k", "kind": "extra", "label": "Kits", "home": old_home,
                                      "source_path": os.path.join(old_home, "Desktop", "Kits"),
                                      "files": {"kick.wav": b"kick"}}])
        new = self.tmp / "new"
        new.mkdir()
        with as_home(new), quiet():
            rm.cmd_unpack(argparse.Namespace(bundle=str(b), dest_for=[], keep_together=True))
        self.assertTrue(list((new / "Music" / "Freighter").rglob("kick.wav")))
        self.assertFalse((new / "Desktop" / "Kits").exists())

    def test_dest_for_sends_an_item_elsewhere(self):
        h = "C:\\Users\\Producer"
        elsewhere = self.tmp / "Samples drive" / "Kits"
        new, ul = self.move("windows", h, "\\", h + "\\Documents\\Ableton\\User Library",
                            dest_for=[f"used1={elsewhere}"])
        self.assertEqual((elsewhere / "Kicks" / "Kick 01 & Co.wav").read_bytes(), b"kick")
        self.assert_repaired(new, ul)

if __name__ == "__main__":
    unittest.main()


class ProgressGuess(unittest.TestCase):
    def test_time_left_counts_only_files_actually_copied(self):
        seen, clock = [], [100.0]
        rm.PROGRESS.listener = lambda *a: seen.append(a)
        try:
            with mock.patch.object(rm.time, "time", lambda: clock[0]), quiet():
                p = rm.Progress(1000, "Copying", 4)
                p.add(0, files=0)
                p.add(500, copied=False)     # already in place from an earlier run: takes no time
                clock[0] += 5
                p.add(100)                   # really copied: 100 bytes in 5 seconds
        finally:
            rm.PROGRESS.listener = None
        label, done, total, extra = seen[-1]
        self.assertEqual((label, done, total), ("Copying", 600, 1000))
        self.assertEqual((extra["files"], extra["files_total"]), (2, 4))
        self.assertAlmostEqual(extra["eta"], 20.0)   # 400 bytes left at 20 bytes/s, not 400 at 120 bytes/s

    def test_no_guess_until_there_is_something_to_go_on(self):
        with quiet():
            p = rm.Progress(1000, "Copying", 2)
            p.add(10)
        self.assertIsNone(p.eta())

    def test_time_left_reads_naturally(self):
        self.assertEqual(rm.human_time(42), "about 40 seconds")
        self.assertEqual(rm.human_time(720), "about 12 minutes")
        self.assertEqual(rm.human_time(3600), "about 1 hour")
        self.assertEqual(rm.human_time(7800), "about 2 hours 10 minutes")


class PluginPresets(TempDirCase):
    """Issue #31: plugin presets and plugin data move too."""

    def plan(self, home, makers=frozenset(), system=()):
        with as_home(home), quiet(), mock.patch.object(rm, "plugin_makers", lambda: set(makers)), \
                mock.patch.object(rm, "system_preset_places", lambda: list(system)):
            return rm.plan_move()

    def packed(self, plan):
        return {Path(it["source_path"]).name: it for it in plan["items"]}

    def test_folders_with_preset_files_move(self):
        home = self.tmp / "home"
        write(home / "Documents" / "Xfer" / "Serum Presets" / "User" / "Bass.fxp", b"p" * 100)
        write(home / "Music" / "Vital" / "User" / "Presets" / "Lead.vital", b"v" * 100)
        write(home / "Documents" / "Zoom" / "meeting.mp4", b"z" * 100)            # not plugin data
        write(home / "Music" / "Freighter" / "old" / "Bass.fxp", b"p")            # Freighter's own folder
        packed = self.packed(self.plan(home))
        self.assertIn("Xfer", packed)
        self.assertIn("Vital", packed)
        self.assertTrue(packed["Xfer"]["plugin_data"])
        self.assertEqual(packed["Xfer"]["label"], "Plugin presets and data: Xfer")
        self.assertNotIn("Zoom", packed)
        self.assertNotIn("Freighter", packed)

    def test_a_folder_named_after_an_installed_plugins_maker_moves(self):
        home = self.tmp / "home"
        write(home / "Documents" / "Drumolution" / "Kits" / "Techno" / "kick.wav", b"k" * 100)
        write(home / "Documents" / "Holiday Photos" / "a.jpg", b"j")
        packed = self.packed(self.plan(home, makers={"drumolution"}))
        self.assertIn("Drumolution", packed)
        self.assertNotIn("Holiday Photos", packed)

    def test_shared_preset_folders_are_searched_too(self):
        home, system = self.tmp / "home", self.tmp / "Library" / "Audio" / "Presets"
        write(system / "Xfer Records" / "Serum 2 Presets" / "Pad.SerumPreset", b"s" * 100)
        write(home / "Documents" / "notes.txt", b"n")
        packed = self.packed(self.plan(home, system=[system]))
        self.assertIn("Xfer Records", packed)

    def test_big_plugin_content_is_offered_not_packed(self):
        home = self.tmp / "home"
        lib = write(home / "Documents" / "Heavy Synths" / "Library" / "Strings.fxp", b"s" * 5000).parents[1]
        with mock.patch.object(rm, "PLUGIN_DATA_BIG", 1000):
            plan = self.plan(home)
        self.assertNotIn("Heavy Synths", self.packed(plan))
        self.assertEqual(plan["big_plugin_data"], [{"path": str(lib), "label": "Heavy Synths", "bytes": 5000}])
        with as_home(home), quiet(), mock.patch.object(rm, "PLUGIN_DATA_BIG", 1000):
            taken = rm.plan_move(includes=[str(lib)])           # ticked in the app, or --include on the command line
        self.assertIn("Heavy Synths", self.packed(taken))
        self.assertEqual(taken["big_plugin_data"], [])

    def test_presets_go_back_to_the_same_place(self):
        old, new, drive = self.tmp / "old", self.tmp / "new", self.tmp / "drive"
        write(old / "Documents" / "Xfer" / "Serum Presets" / "User" / "Bass.fxp", b"p" * 100)
        drive.mkdir()
        with as_home(old), quiet():
            rm.cmd_pack(argparse.Namespace(to=str(drive), list=False, include=[], skip=[], include_programs=False))
        bundle = next(drive.glob("Freighter-*"))
        new.mkdir()
        with as_home(new), quiet():
            rm.cmd_unpack(argparse.Namespace(bundle=str(bundle), dest_for=[]))
        self.assertEqual((new / "Documents" / "Xfer" / "Serum Presets" / "User" / "Bass.fxp").read_bytes(), b"p" * 100)
        u = rm.LAST["unpack"]
        self.assertFalse(u["cross_os"])
        self.assertEqual([Path(p["new"]) for p in u["plugin_data"]], [new / "Documents" / "Xfer"])


class AbletonRacks(TempDirCase):
    """Issue #32: racks and clips saved in Live's browser point at samples too."""

    def test_a_drum_rack_moves_its_samples_and_is_repaired(self):
        old, new, drive = self.tmp / "old", self.tmp / "new", self.tmp / "drive"
        kick = write(old / "Desktop" / "MyKicks" / "kick.wav", b"kick" * 50)
        ul = rm.known_roots(rm.THIS_OS, old)["ableton_user_library"][1]
        rack = make_als(ul / "Presets" / "Instruments" / "Drum Rack" / "My Kit.adg", [str(kick)])
        clip = make_als(ul / "Clips" / "Groove.alc", [str(kick)])
        drive.mkdir()
        with as_home(old), quiet():
            plan = rm.plan_move()
            self.assertIn(str(old / "Desktop" / "MyKicks"), [it["source_path"] for it in plan["items"]],
                          "a sample only a rack uses still moves")
            rm.cmd_pack(argparse.Namespace(to=str(drive), list=False, include=[], skip=[], include_programs=False))
        bundle = next(drive.glob("Freighter-*"))
        shutil.rmtree(old)                                   # the old computer is gone
        new.mkdir()
        with as_home(new), quiet(), mock.patch.object(rm, "fl_is_running", lambda: False):
            rm.cmd_unpack(argparse.Namespace(bundle=str(bundle), dest_for=[], keep_together=True))
            summary = rm.repair_projects()
            landing_ul = Path(next(e["new"] for e in rm.LAST["unpack"]["entries"] if "User Library" in e["label"]))
            for moved in (landing_ul / "Presets" / "Instruments" / "Drum Rack" / "My Kit.adg",
                          landing_ul / "Clips" / "Groove.alc"):
                ref = rm.als_sample_refs(rm.read_als(moved)[0])[0]
                self.assertEqual(Path(ref).read_bytes(), b"kick" * 50, moved.name)
                backup = moved.parent / "Freighter backup" / (moved.name + ".original")
                self.assertTrue(backup.is_file(), "the original is kept, hidden from Live's browser")
                self.assertFalse((moved.parent / "Freighter backup" / moved.name).exists())
            self.assertEqual(summary["repaired"], 2)
            rm.undo_unpack()                                  # undo puts nothing back wrong and leaves no clutter
        self.assertFalse(landing_ul.exists())

    def test_lives_installed_packs_never_move(self):
        home = self.tmp / "home"
        packs = (home / ("Music" if rm.THIS_OS == "mac" else "Documents") / "Ableton" / "Factory Packs"
                 / "Drum Essentials")
        write(packs / "Samples" / "Kick.wav", b"k" * 100)
        make_als(packs / "Demo Project" / "Demo.als", [str(packs / "Samples" / "Kick.wav")])
        make_als(packs / "Presets" / "Kit.adg", [str(packs / "Samples" / "Kick.wav")])
        write(home / "Desktop" / "notes.txt", b"n")
        with as_home(home), quiet():
            plan = rm.plan_move()
        self.assertFalse([it for it in plan["items"] if "Factory Packs" in it["source_path"]], plan["items"])
        self.assertTrue(rm.in_program_place(packs / "Samples" / "Kick.wav"))


class KeepAwake(unittest.TestCase):
    def test_the_computer_stays_awake_while_copying_and_is_let_go_after(self):
        calls = []

        class Proc:
            def terminate(self):
                calls.append("let go")

            def wait(self, timeout=None):
                return 0
        with mock.patch.object(rm.rs, "IS_WIN", False), mock.patch.object(rm.rs, "IS_MAC", True), \
                mock.patch.object(rm.subprocess, "Popen", lambda cmd, **kw: calls.append(cmd[:2]) or Proc()):
            with rm.keep_awake():
                calls.append("copying")
        self.assertEqual(calls, [["caffeinate", "-i"], "copying", "let go"])

    def test_it_never_stops_a_move(self):
        def broken(*a, **kw):
            raise OSError("no caffeinate here")
        with mock.patch.object(rm.rs, "IS_WIN", False), mock.patch.object(rm.rs, "IS_MAC", True), \
                mock.patch.object(rm.subprocess, "Popen", broken):
            with rm.keep_awake():
                done = True
        self.assertTrue(done)


class BlockedWrites(TempDirCase):
    def test_a_refused_write_explains_what_to_allow(self):
        old, new, drive = self.tmp / "old", self.tmp / "new", self.tmp / "drive"
        write(old / "Documents" / "Xfer" / "Serum Presets" / "Bass.fxp", b"p" * 10)
        drive.mkdir()
        with as_home(old), quiet():
            rm.cmd_pack(argparse.Namespace(to=str(drive), list=False, include=[], skip=[], include_programs=False))
        new.mkdir()

        def refuse(src, dst, direct=False):
            raise PermissionError(13, "Permission denied")
        with as_home(new), quiet(), mock.patch.object(rm, "copy_and_hash", refuse):
            rm.cmd_unpack(argparse.Namespace(bundle=str(next(drive.glob("Freighter-*"))), dest_for=[]))
        u = rm.LAST["unpack"]
        self.assertTrue(u["bad"])
        self.assertEqual(u["blocked_hint"], rm.blocked_hint())
        self.assertIn("Controlled folder access", rm.blocked_hint("windows"))
        self.assertIn("Privacy & Security", rm.blocked_hint("mac"))


class TimeRange(TempDirCase):
    """Issue #76: bring only the projects saved in a time range, and the whole kits they use."""

    @staticmethod
    def saved(p, year, month):
        t = dt.datetime(year, month, 15).timestamp()
        os.utime(p, (t, t))
        return p

    def test_ranges_people_type(self):
        r = rm.parse_range("Mar 2024 - Mar 2026")
        self.assertEqual((r.since_text, r.until_text), ("2024-03", "2026-03"))
        self.assertTrue(r.contains(dt.datetime(2024, 3, 1).timestamp()))
        self.assertTrue(r.contains(dt.datetime(2026, 3, 31, 23, 59).timestamp()))
        self.assertFalse(r.contains(dt.datetime(2026, 4, 1).timestamp()))
        self.assertFalse(r.contains(dt.datetime(2024, 2, 29, 12).timestamp()))
        self.assertEqual(rm.parse_range("2024-03 to 2026-03").label(), "Mar 2024 – Mar 2026")
        self.assertEqual(rm.parse_range("since 2025-01").until, None)
        self.assertEqual(rm.parse_range("until March 2026").until_text, "2026-03")
        for bad in ("2026-03 to 2024-03", "banana", "2024-13 to 2025-01", "2024-02-30 to 2024-03"):
            with self.assertRaises(ValueError):
                rm.parse_range(bad)

    def test_kit_parts_and_kits(self):
        for part in ("US_DG_One_Shots", "LC_CNTRL_Bass Loops", "Kicks", "808s", "Hi Hats", "FX", "MIDI", "Other FX"):
            self.assertTrue(rm.kit_part(part), part)
        for kit in ("UNDRGRND Sounds - Deep Grooves - Wav", "Loop Cult - CONTROL - Sample Pack", "House", "OPIUM",
                    "Ultimate Drums Vol 1", "Bass Kit", "Drum Kit 2", "Other Kit"):
            self.assertFalse(rm.kit_part(kit), kit)

    def test_a_used_sample_brings_its_kit_and_nothing_above_it(self):
        lib = self.tmp / "Library"
        a = write(lib / "House" / "UndrGrndSounds Tech House" / "US_TH_One_Shots" / "Kicks" / "kick.wav", b"k")
        write(lib / "House" / "Another House Kit" / "Snares" / "snare.wav", b"s")
        write(lib / "Trap" / "Opium Kit" / "808s" / "808.wav", b"8")
        write(lib / "Trap" / "Rage Kit" / "Hats" / "hat.wav", b"h")
        loose = write(lib / "House" / "stray.wav", b"x")                       # sits in a genre folder
        rm.KIT_CONTAINER_CACHE.clear()
        self.assertEqual(rm.kit_folder(a, lib), lib / "House" / "UndrGrndSounds Tech House")
        self.assertIsNone(rm.kit_folder(loose, lib), "a sample in a genre folder comes on its own")
        one = self.tmp / "DrumKits"                                            # one kit laid out by sound type
        k = write(one / "Kicks" / "kick.wav", b"k")
        write(one / "Snares" / "snare.wav", b"s")
        self.assertEqual(rm.kit_folder(k, one), one)

    def test_a_time_range_move(self):
        home = self.tmp / "home"
        lib = home / "Desktop" / "Samples Library"
        used = write(lib / "House" / "Tech House Kit" / "One Shots" / "Kicks" / "kick.wav", b"k" * 100)
        write(lib / "House" / "Tech House Kit" / "Loops" / "groove.wav", b"g" * 100)   # same kit: comes too
        self.saved(make_flp(lib / "House" / "Tech House Kit" / "Demo" / "Demo.flp", []), 2018, 1)
        write(lib / "House" / "Other House Kit" / "Kicks" / "kick2.wav", b"o" * 100)  # another kit: stays
        old_only = write(lib / "Trap" / "Old Kit" / "Kicks" / "old.wav", b"x" * 100)
        write(lib / "Trap" / "Rage Kit" / "Hats" / "hat.wav", b"h" * 100)
        projects = home / "Documents" / "Image-Line" / "FL Studio" / "Projects"
        new = self.saved(make_flp(projects / "New Song.flp", [str(used)]), 2025, 6)
        old = self.saved(make_flp(projects / "Old Song.flp", [str(old_only)]), 2021, 2)
        tmpl = self.saved(make_flp(projects / "Templates" / "Start.flp", []), 2019, 1)
        ul = rm.known_roots(rm.THIS_OS, home)["ableton_user_library"][1]
        old_set = self.saved(make_als(ul.parent / "Projects" / "Old Jam Project" / "Old Jam.als", []), 2020, 5)
        write(old_set.parent / "Samples" / "Recorded" / "take.wav", b"r" * 100)
        with as_home(home), quiet(), mock.patch.object(rm, "fl_settings",
                                                       lambda: {"user_data": None, "search_paths": [str(lib)], "recent": []}):
            plan = rm.plan_move(rng=rm.parse_range("2024-03 to 2026-03"))
            whole = rm.plan_move()
        paths = {it["source_path"]: it for it in plan["items"]}
        kit = lib / "House" / "Tech House Kit"
        self.assertIn(str(kit), paths, "the kit a project uses comes whole")
        self.assertEqual(paths[str(kit)]["label"], "Kit: Tech House Kit")
        self.assertEqual(paths[str(kit)]["files"], 3, "with its own (old) demo project: kits come complete")
        for not_packed in (lib, lib / "House", lib / "Trap" / "Old Kit", lib / "House" / "Other House Kit"):
            self.assertNotIn(str(not_packed), paths)
        self.assertEqual([o["path"] for o in plan["offered_libraries"]], [str(lib)], "the library is offered")
        fl = paths[str(home / "Documents" / "Image-Line")]
        names = {Path(ap).name for _rel, ap, _n in fl["_files"]}
        self.assertIn("New Song.flp", names)
        self.assertIn("Start.flp", names, "templates always come")
        self.assertNotIn("Old Song.flp", names, "a project saved before the range stays behind")
        self.assertFalse([1 for it in plan["items"] for _rel, ap, _n in it["_files"] if "Old Jam" in str(ap)],
                         "an Ableton project folder saved before the range stays behind")
        self.assertEqual((plan["projects"]["in_range"], plan["projects"]["total"]), (1, 3))
        self.assertEqual(plan["range"]["label"], "Mar 2024 – Mar 2026")
        self.assertLess(plan["total"], whole["total"])

    def test_a_time_range_move_end_to_end(self):
        old, new, drive = self.tmp / "old", self.tmp / "new", self.tmp / "drive"
        kit = old / "Desktop" / "Kits" / "House" / "Tech House Kit"
        kick = write(kit / "Kicks" / "kick.wav", b"k" * 100)
        write(kit / "Loops" / "groove.wav", b"g" * 100)
        write(old / "Desktop" / "Kits" / "House" / "Other Kit" / "Snares" / "s.wav", b"s" * 100)
        projects = old / "Documents" / "Image-Line" / "FL Studio" / "Projects"
        self.saved(make_flp(projects / "Beat" / "Beat.flp", [str(kick)]), 2025, 6)
        self.saved(make_flp(projects / "Old" / "Old.flp", [str(kick)]), 2020, 1)
        drive.mkdir()
        with as_home(old), quiet():
            rm.cmd_pack(argparse.Namespace(to=str(drive), list=False, include=[], skip=[], include_programs=False,
                                           since="2024-03", until="2026-03"))
            # and the same range rehearses
            code = rm.cmd_rehearse(argparse.Namespace(to=str(self.tmp / "reh"), include=[], skip=[],
                                                      since="2024-03", until="2026-03"))
        self.assertEqual(code, 0)
        self.assertEqual(rm.LAST["rehearse"]["totals"]["kept"], 1)
        bundle = next(drive.glob("Freighter-*"))
        manifest = json.loads((bundle / "manifest.json").read_text())
        self.assertEqual(manifest["projects_range"]["label"], "Mar 2024 – Mar 2026")
        self.assertIn("Old.flp", (bundle / "projects-left-out.txt").read_text())
        self.assertEqual(rm.bundle_summary(bundle)["range"], "Mar 2024 – Mar 2026")
        shutil.rmtree(old)
        new.mkdir()
        with as_home(new), quiet(), mock.patch.object(rm, "fl_is_running", lambda: False):
            rm.cmd_unpack(argparse.Namespace(bundle=str(bundle), dest_for=[], keep_together=True))
            rm.cmd_fix_fl(argparse.Namespace(pathmap=None, folder=[], include_backups=False, dry_run=False))
            u = rm.LAST["unpack"]
        self.assertEqual(u["range"], "Mar 2024 – Mar 2026")
        self.assertTrue(any("stayed on the old computer" in n for n in u["notes"]))
        beat = next(new.rglob("Beat.flp"))
        self.assertEqual(Path(rm.flp_samples(beat)[1][0]).read_bytes(), b"k" * 100, "the project opens with its kit")
        self.assertTrue(list(new.rglob("groove.wav")), "the whole kit came")
        self.assertFalse(list(new.rglob("s.wav")), "another kit in the same genre folder didn't")
        self.assertFalse(list(new.rglob("Old.flp")), "the old project stayed behind")

    def test_a_folder_that_only_wraps_one_genre_is_stepped_through(self):
        kits = self.tmp / "Kits"
        k = write(kits / "House" / "Tech House Kit" / "Kicks" / "kick.wav", b"k")
        write(kits / "House" / "Other Kit" / "Snares" / "s.wav", b"s")
        rm.KIT_CONTAINER_CACHE.clear()
        self.assertEqual(rm.kit_folder(k, kits), kits / "House" / "Tech House Kit")
        wrapped = self.tmp / "Pack Download"                     # a pack unzipped inside a wrapper folder
        w = write(wrapped / "Cool Pack - Wav" / "Kicks" / "kick.wav", b"k")
        write(wrapped / "Cool Pack - Wav" / "Loops" / "loop.wav", b"l")
        self.assertEqual(rm.kit_folder(w, wrapped), wrapped / "Cool Pack - Wav")


class SoundLibraries(TempDirCase):
    """Issue #71: Kontakt and friends are found wherever they live, offered, and reconnected after the move."""

    def studio(self):
        home = self.tmp / "home"
        una = write(home / "Documents" / "Native Instruments" / "Libraries" / "Una Corda" / "Una Corda.nicnt", b"n")
        write(una.parent / "Samples" / "piano.ncw", b"p" * 3000)
        steam = home / "Desktop" / "Libraries" / "STEAM"
        write(steam / "Omnisphere" / "Settings Library" / "pad.prt_omn", b"o" * 2000)
        write(home / "Music" / "Spitfire Audio" / "BBC Symphony Orchestra" / "Samples" / "violins.wav", b"v" * 1000)
        write(home / "Desktop" / "Libraries" / "UVI" / "Vintage Vault.ufs", b"u" * 500)
        write(home / "Documents" / "EastWest" / "Hollywood Strings" / "Instruments" / "Violins.ewi", b"e")
        write(home / "Documents" / "Toontrack" / "Superior3" / "Core.sdx", b"t")
        write(home / "Documents" / "Native Instruments" / "User Content" / "my preset.nksf", b"x")
        return home, una.parent, steam

    def test_each_makers_libraries_are_found(self):
        home, una, steam = self.studio()
        with as_home(home):
            libs = {Path(lib["path"]).name: lib for lib in rm.find_libraries(ni={})}
        self.assertEqual(libs["Una Corda"]["maker"], "Native Instruments")
        self.assertEqual(libs["STEAM"]["maker"], "Spectrasonics")
        self.assertEqual(libs["STEAM"]["name"], "Spectrasonics STEAM folder")
        self.assertEqual(libs["BBC Symphony Orchestra"]["maker"], "Spitfire Audio")
        self.assertEqual(libs["UVI"]["maker"], "UVI")
        self.assertEqual(libs["Hollywood Strings"]["maker"], "EastWest")
        self.assertEqual(libs["Superior3"]["maker"], "Toontrack")
        self.assertNotIn("User Content", libs, "a preset folder isn't a sound library")

    def test_native_instruments_mac_paths(self):
        self.assertEqual(rm.hfs_path("Nope Disk:Libraries:Strings:"), Path("/Volumes/Nope Disk/Libraries/Strings"))
        self.assertIsNone(rm.hfs_path(""))

    def test_libraries_are_offered_not_packed_and_carved_out_of_packed_folders(self):
        home, una, steam = self.studio()
        with as_home(home), quiet():
            plan = rm.plan_move()
            ni = next(it for it in plan["items"] if it["id"] == "ni_user")
            self.assertFalse([1 for _r, ap, _n in ni["_files"] if "Una Corda" in str(ap)],
                             "a library inside a packed folder stays out unless ticked")
            self.assertTrue([1 for _r, ap, _n in ni["_files"] if "my preset" in str(ap)], "the rest of it still comes")
            offered = {lib["name"]: lib for lib in plan["libraries"]}
            self.assertEqual(offered["Una Corda"]["bytes"], 3001)
            self.assertFalse(offered["Una Corda"]["moved"])
            taken = rm.plan_move(includes=[str(una)])
        item = next(it for it in taken["items"] if it.get("library"))
        self.assertEqual(item["label"], "Sound library: Una Corda")
        self.assertEqual(item["files"], 2)
        ni = next(it for it in taken["items"] if it["id"] == "ni_user")
        self.assertFalse([1 for _r, ap, _n in ni["_files"] if "Una Corda" in str(ap)],
                         "a ticked library comes once, as its own item, not again inside the NI folder")
        self.assertTrue(next(lib for lib in taken["libraries"] if lib["name"] == "Una Corda")["moved"])

    def test_after_unpacking_each_library_says_how_to_reconnect_it(self):
        home, una, steam = self.studio()
        drive, new = self.tmp / "drive", self.tmp / "new"
        drive.mkdir()
        with as_home(home), quiet():
            rm.cmd_pack(argparse.Namespace(to=str(drive), list=False, include=[str(una)], skip=[],
                                           include_programs=False))
        bundle = next(drive.glob("Freighter-*"))
        new.mkdir()
        with as_home(new), quiet():
            rm.cmd_unpack(argparse.Namespace(bundle=str(bundle), dest_for=[]))
        libs = {lib["name"]: lib for lib in rm.LAST["unpack"]["libraries"]}
        moved = libs["Una Corda"]
        self.assertTrue(moved["moved"])
        self.assertTrue((Path(moved["new"]) / "Samples" / "piano.ncw").is_file())
        self.assertIn("Native Access", moved["steps"][0])
        self.assertIn(moved["new"], " ".join(moved["steps"]), "the steps name where it is now")
        self.assertFalse(libs["Spectrasonics STEAM folder"]["moved"])
        self.assertIn("Spectrasonics", libs["Spectrasonics STEAM folder"]["steps"][0])


class DriveCheck(TempDirCase):
    """Issue #77: read the drive back before leaving the old computer."""

    def test_a_damaged_copy_is_caught_removed_and_copied_again(self):
        old, drive = self.tmp / "old", self.tmp / "drive"
        kick = write(old / "Desktop" / "Kits" / "kick.wav", b"k" * 1000)
        snare = write(old / "Desktop" / "Kits" / "snare.wav", b"s" * 1000)
        make_flp(old / "Documents" / "Image-Line" / "FL Studio" / "Projects" / "Beat.flp", [str(kick), str(snare)])
        drive.mkdir()
        pack = argparse.Namespace(to=str(drive), list=False, include=[], skip=[], include_programs=False)
        with as_home(old), quiet():
            rm.cmd_pack(pack)
            bundle = next(drive.glob("Freighter-*"))
            good = rm.verify_bundle(bundle)
        self.assertEqual(good["bad_count"], 0)
        self.assertIn("read back and match", (bundle / "drive-check.txt").read_text())
        copies = {p.name: p for p in (bundle / "data").rglob("*.wav")}
        copies["kick.wav"].write_bytes(b"X" * 1000)            # same size, wrong content: a bad drive or cable
        copies["snare.wav"].unlink()
        with as_home(old), quiet():
            r = rm.verify_bundle(bundle)
            self.assertEqual(r["bad_count"], 2)
            self.assertEqual(sorted(b["file"] for b in r["bad"]), ["kick.wav", "snare.wav"])
            self.assertFalse(copies["kick.wav"].exists(), "the damaged copy was removed")
            self.assertTrue(rm.bundle_summary(bundle)["problems"], "so the bundle shows as unfinished")
            self.assertEqual(rm.cmd_verify(argparse.Namespace(bundle=str(drive))), 2, "found from the drive too")
            rm.cmd_pack(pack)                                    # pack again: only those are copied again
            again = rm.verify_bundle(bundle)
        self.assertEqual(again["bad_count"], 0)
        self.assertEqual(copies["kick.wav"].read_bytes(), b"k" * 1000)
        self.assertEqual(rm.bundle_summary(bundle)["problems"], [])


class ProjectHealth(TempDirCase):
    """Issue #78: every project's samples and plugins, problems first."""

    def test_each_projects_status(self):
        home = self.tmp / "home"
        kick = write(home / "Desktop" / "Kits" / "kick.wav", b"k")
        projects = home / "Documents" / "Image-Line" / "FL Studio" / "Projects"
        make_flp(projects / "Ready.flp", [str(kick), "%FLStudioFactoryData%/Data/Patches/x.wav"])
        make_flp(projects / "Lost.flp", [str(kick), str(home / "Downloads" / "gone.wav")])
        make_plugin_flp(projects / "Synth.flp", [("Serum 2", "Xfer Records", "")])
        song = home / "Music" / "Song Project"
        write(song / "Samples" / "Imported" / "vox.wav", b"v")
        als = make_als(song / "Song.als", ["/Users/someone-else/Music/Song Project/Samples/Imported/vox.wav"])
        text = gzip.decompress(als.read_bytes()).decode().replace(
            '<RelativePath Value="" />', '<RelativePath Value="Samples/Imported/vox.wav" />')
        als.write_bytes(gzip.compress(text.encode()))          # Live finds it relative to the project
        make_als(home / "Music" / "Old Project" / "Old.als", [], creator="Ableton Live 10.1.13")
        with as_home(home):
            h = rm.project_health(installed=[])
        by = {e["name"]: e for e in h["projects"]}
        self.assertEqual(by["Ready.flp"]["status"], "ready")
        self.assertEqual(by["Song.als"]["status"], "ready", "found through its relative path")
        self.assertEqual(by["Lost.flp"]["status"], "attention")
        self.assertEqual(by["Lost.flp"]["missing_samples"], [str(home / "Downloads" / "gone.wav")])
        self.assertEqual(by["Synth.flp"]["plugins_needed"], ["Serum 2"])
        self.assertEqual(by["Old.als"]["status"], "unchecked")
        self.assertEqual((h["ready"], h["total"], h["unchecked"]), (2, 4, 1))
        self.assertIn(h["projects"][0]["name"], ("Lost.flp", "Synth.flp"), "problems first")
        with as_home(home):
            serum = [{"name": "Serum 2", "vendor": "Xfer Records", "key": "serum2"}]
            self.assertEqual({e["name"]: e["status"] for e in rm.project_health(installed=serum)["projects"]}["Synth.flp"],
                             "ready", "once the plugin is installed")
        self.assertIn("2 of 4 projects are ready", "\n".join(rm.health_lines(h)))
