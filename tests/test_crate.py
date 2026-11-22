"""Tests for _Crate, the sample sorter. Run: python3 -m unittest discover -s tests -v"""
import math
import os
import random
import shutil
import struct
import sys
import tempfile
import unittest
from unittest import mock
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import crate  # noqa: E402


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


def write(p, data=b"x"):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def write_wav(p, samples, rate=44100):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(max(-1, min(1, s)) * 30000)) for s in samples))
    return p


def tone(freq, seconds, rate=44100):
    n = int(seconds * rate)
    return [math.sin(2 * math.pi * freq * i / rate) * math.exp(-3 * i / n) for i in range(n)]


def noise(seconds, rate=44100, seed=1):
    r = random.Random(seed)
    n = int(seconds * rate)
    return [r.uniform(-1, 1) * math.exp(-8 * i / n) for i in range(n)]


class Names(unittest.TestCase):
    def cat(self, rel):
        root = Path("/packs")
        return crate.classify(root / rel, root)[0]

    def test_real_world_one_shot_names(self):
        cases = {
            "bd_dropped.wav": "Drums/Kicks",
            "alucard sn @thecxdy.wav": "Drums/Snares",
            "clp_scorch.wav": "Drums/Claps",
            "Cxdy - Falcon (HH).wav": "Drums/Hi-Hats/Closed",
            "caffeine open hat @thecxdy.wav": "Drums/Hi-Hats/Open",
            "LC_CNTRL_OpenHat_04.wav": "Drums/Hi-Hats/Open",
            "expo 808 @thecxdy.wav": "Drums/808s",
            "phant0m rim @thecxdy.wav": "Drums/Rims",
            "ride_cymbal1.wav": "Drums/Cymbals",
            "prc_bongodrm.wav": "Drums/Percussion",
            "US_OH_Percussions_livy.wav": "Drums/Percussion",
            "Cxdy - Transport (Trans).wav": "FX",
            "LC_CNTRL_SynthBass_D#_02.wav": "Bass",
            "US_OH_Chords_mobile_Em.wav": "Keys & Chords",
            "b0rn TAG 2021 .wav": "Vocals",
            "Kick - Bass Drum Thing.wav": "Drums/Kicks",
        }
        for name, want in cases.items():
            self.assertEqual(self.cat(name), want, name)

    def test_loops(self):
        self.assertEqual(self.cat("US_DG_Drum_123_Good_PERC.wav"), "Loops/Drums")
        self.assertEqual(self.cat("US_DTH_Drum_124_Road_HAT.wav"), "Loops/Drums")    # a hat LOOP, not a hat
        self.assertEqual(self.cat("US_DG_Bass_123_zed_E.wav"), "Loops/Bass")
        self.assertEqual(self.cat("US_DTH_Pad_124_Room_A#.wav"), "Loops/Melodic")
        self.assertEqual(self.cat("[PBS] Official Trap Clp_2 - Loop_2.wav"), "Loops/Drums")
        self.assertEqual(self.cat("baltimore break.mp3"), "Loops/Drums")

    def test_folder_names_help(self):
        self.assertEqual(self.cat("HOUSE/snare/thing_01.wav"), "Drums/Snares")
        self.assertEqual(self.cat("Pack/Bass Loops/US_X_thing.wav"), "Loops/Bass")
        # a pack *name* with "Loop" in it doesn't make its one-shots loops
        self.assertEqual(self.cat("Loop Cult - CONTROL/LC_CNTRL_Vox Fxs/LC_CNTRL_Vox_Fx_01.wav"), "Vocals")
        # "One Shots" two folders up beats a stray number in the name
        self.assertEqual(self.cat("Pack/One Shots/Drum Hits/Kick 120.wav"), "Drums/Kicks")

    def test_bpm_and_key(self):
        self.assertEqual(crate.find_bpm("US_DG_Bass_123_zed_E"), 123)
        self.assertEqual(crate.find_bpm("[96] b0rn _Fool_ Loop"), 96)
        self.assertIsNone(crate.find_bpm("expo 808"))
        self.assertIsNone(crate.find_bpm("Snare_02"))
        self.assertEqual(crate.find_key("US_DTH_Pad_124_Room_A#"), "A#")
        self.assertEqual(crate.find_key("US_OH_Chords_mobile_Em"), "Em")
        self.assertEqual(crate.find_key("LC_CNTRL_140_Fm_Melodic"), "Fm")
        self.assertIsNone(crate.find_key("clp_scorch"))


class Genres(unittest.TestCase):
    def test_from_pack_names(self):
        cases = {
            "UNDRGRND Sounds - Deep Tech House - Wav": "Tech House",
            "UNDRGRND Sounds - Organic House - Wav": "House",
            "deep_house_drum_samples": "House",
            "UNRGND Sounds - Classic UK Garage Presets": "UK Garage",
            "JERZY CLUB": "Jersey Club",
            "OPIUM": "Trap",
            "THE SAUCE - METROOO": "Trap",
            "Boom Bap Essentials": "Hip-Hop",
            "UK Drill Kit": "Drill",
        }
        for pack, want in cases.items():
            self.assertEqual(crate.genre_of(pack), (want, "name"), pack)

    def test_tempo_guess_and_fallback(self):
        self.assertEqual(crate.genre_of("Deep Grooves", [123] * 20), ("House", "tempo"))
        self.assertEqual(crate.genre_of("CONTROL", [134] * 20), ("Techno", "tempo"))
        self.assertEqual(crate.genre_of("Mystery Kit", [140, None]), ("Other", "none"))   # too few tempos to guess


class Sound(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="crate-test-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_guesses_from_the_audio(self):
        kick = write_wav(self.tmp / "untitled_1.wav", tone(55, 0.4))
        boom = write_wav(self.tmp / "untitled_2.wav", tone(40, 1.5))
        hat = write_wav(self.tmp / "untitled_3.wav", noise(0.12))
        self.assertEqual(crate.classify(kick, self.tmp)[0], "Drums/Kicks")
        self.assertEqual(crate.classify(boom, self.tmp)[0], "Drums/808s")
        self.assertIn(crate.classify(hat, self.tmp)[0], ("Drums/Hi-Hats/Closed", "Drums/Snares"))
        self.assertTrue(crate.classify(kick, self.tmp)[1].startswith("sound:"))

    def test_unreadable_audio_is_unsorted_not_a_crash(self):
        bad = write(self.tmp / "mystery.wav", b"RIFF....not really")
        self.assertEqual(crate.classify(bad, self.tmp)[0], "Unsorted")


class BuildAndUndo(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="crate-test-"))
        self.src = self.tmp / "Desktop"
        self.kick = write(self.src / "KitA" / "bd_one.wav", os.urandom(800))
        write(self.src / "KitB" / "copy of kick.wav", self.kick.read_bytes())       # duplicate content
        write(self.src / "KitA" / "snare.wav", os.urandom(700))
        write(self.src / "KitB" / "snare.wav", os.urandom(600))                       # same name, different sound
        write(self.src / "KitA" / "riff.mid", b"MThd" + os.urandom(40))
        write(self.src / "KitA" / "Kit.asd", b"ableton analysis")                     # not audio: ignored

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_build_links_dedupes_and_undo_cleans_up(self):
        lib = self.tmp / "Library"
        items = crate.plan([self.src])
        self.assertEqual(len(items), 5)
        self.assertEqual(sum(1 for i in items if i["duplicate_of"]), 1)
        made, links, copies, skipped = crate.build(items, lib, copy=False)
        self.assertEqual((made, skipped), (4, 1))
        self.assertTrue((lib / "Other" / "KitA" / "Drums" / "Kicks").is_dir(), "default layout: Genre/Pack/type")
        kicks = list((lib / "Other" / "KitA" / "Drums" / "Kicks").iterdir())
        self.assertEqual(len(kicks), 1)
        self.assertTrue(os.path.samefile(kicks[0], self.kick) or links == 0)
        self.assertTrue((lib / "Other" / "KitB" / "Drums" / "Snares" / "snare.wav").is_file())
        self.assertTrue((lib / "Other" / "KitA" / "MIDI" / "riff.mid").is_file())
        # building again adds nothing new
        self.assertEqual(crate.build(items, lib, copy=False)[0], 0)
        before = sorted(p.name for p in self.src.rglob("*") if p.is_file())
        crate.undo(lib)
        self.assertFalse(lib.exists(), "the library folder is removed when it's empty")
        self.assertEqual(sorted(p.name for p in self.src.rglob("*") if p.is_file()), before, "originals untouched")

    def test_by_type_layout(self):
        lib = self.tmp / "Library"
        crate.build(crate.plan([self.src]), lib, copy=False, by="type")
        snares = sorted(p.name for p in (lib / "Drums" / "Snares").iterdir())
        self.assertEqual(snares, ["snare (KitB).wav", "snare.wav"])
        self.assertTrue((lib / "MIDI" / "KitA" / "riff.mid").is_file())
        crate.undo(lib)

    def test_refuses_to_copy_without_space(self):
        """Issue #12: a copying build checks the space first and changes nothing."""
        lib = self.tmp / "Library"
        with mock.patch.object(crate.shutil, "disk_usage", lambda p: mock.Mock(free=1000)):
            with self.assertRaises(SystemExit) as e:
                crate.build(crate.plan([self.src]), lib, copy=True)
        self.assertIn("Not enough free space", str(e.exception.code))
        self.assertFalse(lib.exists())

    def test_copy_mode(self):
        lib = self.tmp / "Library"
        made, links, copies, _ = crate.build(crate.plan([self.src]), lib, copy=True, by="type")
        self.assertEqual((links, copies), (0, made))
        k = next((lib / "Drums" / "Kicks").iterdir())
        self.assertFalse(os.path.samefile(k, self.kick))
        crate.undo(lib)
        self.assertTrue(self.kick.exists())


if __name__ == "__main__":
    unittest.main()
