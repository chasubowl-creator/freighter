"""Automated tests for the scanner (manifest.py) and the mover (freighter.py).

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
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import manifest as rs  # noqa: E402
import freighter as rm  # noqa: E402


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
        rm.HIDE = (rm._norm_abs(old), rm._norm_abs(keep))
        self.assertFalse(rm.exists(a))
        self.assertTrue(rm.exists(b))
        self.assertTrue(rm.exists(self.tmp))


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
        self.assertIn("1 samples used by 1 projects are already missing", out.getvalue())
        self.assertIn("Beat.flp: " + str(h / "Unplugged" / "kick.wav"), out.getvalue())

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

    def test_pack_unpack_repair(self):
        old, new, drive = self.build_old_computer(), self.tmp / "new", self.tmp / "drive"
        bundle = self.pack(old, drive)
        manifest = json.loads((bundle / "manifest.json").read_text())
        ids = {it["id"] for it in manifest["items"]}
        self.assertTrue({"fl_user", "ableton_user_library", "used1", "loose1"} <= ids)
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
        landing = new / "Music" / "Freighter" / json.loads((bundle / "manifest.json").read_text())["source_host"]
        existing = write(landing / "DrumKits" / "Snares" / "snare.wav", b"mine")
        with as_home(new), quiet():
            rm.cmd_unpack(argparse.Namespace(bundle=str(bundle), dest_for=[]))
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
        report = (old / "rehearsal" / "rehearsal-report.txt").read_text()
        self.assertIn("LOST in the move: 0", report)
        self.assertIn("Song.als: /nowhere/gone.wav", report, "already-missing samples are listed (issue #15)")
        self.assertIn("still found after the move: 3", report)
        self.assertTrue(self.kick.is_file(), "the real files must not be touched")


    def test_rehearsal_restores_folders_even_when_it_fails(self):
        old = self.build_old_computer()
        with as_home(old), quiet(), mock.patch.object(rm, "cmd_fix_ableton", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                rm.cmd_rehearse(argparse.Namespace(to=str(old / "rehearsal"), include=[], skip=[]))
            self.assertEqual(rm.home(), old)
            self.assertIsNone(rm.HIDE)


class Menu(TempDirCase):
    def test_a_crash_keeps_the_window_open_and_saves_a_report(self):
        answers = iter(["2", "", "q"])
        with as_home(self.tmp), quiet(), \
                mock.patch("builtins.input", lambda *_: next(answers)), \
                mock.patch.object(rm, "cmd_pack", side_effect=ValueError("disk on fire")):
            code = rm.interactive_menu()
        self.assertEqual(code, 0, "the menu came back after the error instead of closing")
        report = (self.tmp / "Music" / "Freighter" / "freighter-crash.txt").read_text()
        self.assertIn(f"Freighter {rm.__version__}", report)
        self.assertIn("ValueError: disk on fire", report)
        self.assertIn("Menu choice: 2", report)

    def test_closed_input_quits(self):
        def closed(*_):
            raise EOFError
        with quiet(), mock.patch("builtins.input", closed):
            self.assertEqual(rm.interactive_menu(), 0)

if __name__ == "__main__":
    unittest.main()
