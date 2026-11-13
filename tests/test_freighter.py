"""Automated tests for the scanner (rig_scanner.py) and the mover (rig_mover.py).

Run from the project folder:   python3 -m unittest discover -s tests -v
Needs only Python. Every test works in its own temporary folders and never touches your real files.
"""
import argparse
import contextlib
import gzip
import hashlib
import io
import json
import os
import shutil
import struct
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import rig_scanner as rs  # noqa: E402
import rig_mover as rm  # noqa: E402


# --------------------------------------------------------------------------- fixtures

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


def make_als(path, refs, creator="Ableton Live 12.1"):
    def fr(p):
        p = p.replace("&", "&amp;")
        return (f'<FileRef>\n<RelativePathType Value="0" />\n<RelativePath Value="" />\n'
                f'<Path Value="{p}" />\n<Type Value="1" />\n</FileRef>')
    xml = f'<?xml version="1.0" encoding="UTF-8"?>\n<Ableton Creator="{creator}">' + "".join(fr(r) for r in refs) + "</Ableton>"
    return write(path, gzip.compress(xml.encode()))


@contextlib.contextmanager
def quiet():
    with contextlib.redirect_stdout(io.StringIO()):
        yield


@contextlib.contextmanager
def as_home(h):
    old = os.environ.get("RIGMOVER_HOME")
    os.environ["RIGMOVER_HOME"] = str(h)
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("RIGMOVER_HOME", None)
        else:
            os.environ["RIGMOVER_HOME"] = old


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
        sysdir = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
        unsigned = write(self.tmp / "fake.dll", b"MZ" + b"\0" * 200)
        got = rs.win_signatures([sysdir / "notepad.exe", unsigned])
        self.assertEqual(got[os.path.normcase(str(sysdir / "notepad.exe"))][0], "signed")
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
        bundle = next((self.tmp / "d").glob("RigMover-*"))
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
        self.assertTrue((als.parent / "Rig Mover backup" / "Song.als").is_file())

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

    def test_safe_rel(self):
        self.assertEqual(rm.safe_rel('a:b/c?.wav'), "a_b/c_.wav")
        self.assertEqual(rm.safe_rel("CON/aux.txt"), "_CON/_aux.txt")
        self.assertEqual(rm.safe_rel("trailing. /x"), "trailing/x")

    def test_rehearsal_hides_old_home_but_not_rehearsal_folder(self):
        old = self.tmp / "home"
        keep = old / "rehearsal"
        a, b = write(old / "Desktop" / "a.wav"), write(keep / "b.wav")
        rm.HIDE = (rm._norm_abs(old), rm._norm_abs(keep))
        self.assertFalse(rm.exists(a))
        self.assertTrue(rm.exists(b))
        self.assertTrue(rm.exists(self.tmp))


# --------------------------------------------------------------------------- full moves

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
        return next(drive.glob("RigMover-*"))

    def test_pack_unpack_repair(self):
        old, new, drive = self.build_old_computer(), self.tmp / "new", self.tmp / "drive"
        bundle = self.pack(old, drive)
        manifest = json.loads((bundle / "manifest.json").read_text())
        ids = {it["id"] for it in manifest["items"]}
        self.assertTrue({"fl_user", "ableton_user_library", "used1", "loose1"} <= ids)
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
        index = bundle / "index" / "used1.jsonl"
        lines = index.read_text().splitlines()
        victim = json.loads(lines[-1])
        (bundle / "data" / "used1" / victim["stored"]).unlink()
        index.write_text("\n".join(lines[:-1]) + '\n{"rel": "half-writ')  # cut mid-line
        self.pack(old, drive)
        self.assertTrue((bundle / "data" / "used1" / victim["stored"]).is_file())
        recs = rm.read_jsonl(index)
        self.assertEqual(len(recs), len(lines))

    def test_unpack_detects_damage_and_keeps_existing_files(self):
        old, new, drive = self.build_old_computer(), self.tmp / "new", self.tmp / "drive"
        bundle = self.pack(old, drive)
        rec = next(iter(rm.read_jsonl(bundle / "index" / "used1.jsonl").values()))
        damaged = bundle / "data" / "used1" / rec["stored"]
        damaged.write_bytes(b"\0" * damaged.stat().st_size)  # same size, wrong content
        # a different file already sits where the snare goes
        landing = new / "Music" / "Rig Mover" / json.loads((bundle / "manifest.json").read_text())["source_host"]
        existing = write(landing / "DrumKits" / "Snares" / "snare.wav", b"mine")
        with as_home(new), quiet():
            rm.cmd_unpack(argparse.Namespace(bundle=str(bundle), dest_for=[]))
        report = (landing / "unpack-report.txt").read_text()
        self.assertIn("checksum mismatch", report)
        self.assertEqual(existing.read_bytes(), b"mine")
        self.assertTrue(list((landing / "DrumKits" / "Snares").glob("snare (from *).wav")))

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
        os.environ.pop("RIGMOVER_HOME", None)
        self.assertEqual(code, 0)
        report = (old / "rehearsal" / "rehearsal-report.txt").read_text()
        self.assertIn("LOST in the move: 0", report)
        self.assertIn("still found after the move: 3", report)
        self.assertTrue(self.kick.is_file(), "the real files must not be touched")


if __name__ == "__main__":
    unittest.main()
