"""Tests for the browser app's local server. Run: python3 -m unittest discover -s tests -v"""
import contextlib
import http.client
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app  # noqa: E402
import freighter as rm  # noqa: E402


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


class Server(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="freighter-app-test-"))
        self.app, self.server, self.url = app.serve()
        self.stdout = mock.patch.object(sys, "stdout", self.app.router)
        self.stdout.start()

    def tearDown(self):
        self.stdout.stop()
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def request(self, method, path, body=None, token=True, host=None, origin=None):
        c = http.client.HTTPConnection("127.0.0.1", self.app.port, timeout=10)
        headers = {"Host": host or f"127.0.0.1:{self.app.port}"}
        if token:
            headers["X-Freighter-Token"] = self.app.token if token is True else token
        if origin:
            headers["Origin"] = origin
        data = json.dumps(body).encode() if body is not None else None
        if data is not None:
            headers["Content-Type"] = "application/json"
        c.request(method, path, body=data, headers=headers)
        r = c.getresponse()
        raw = r.read()
        c.close()
        try:
            return r.status, json.loads(raw)
        except ValueError:
            return r.status, raw.decode("utf-8", "replace")

    def wait(self, job_id, timeout=60):
        end = time.time() + timeout
        while time.time() < end:
            status, view = self.request("GET", f"/api/jobs/{job_id}")
            self.assertEqual(status, 200)
            if view["state"] != "running":
                return view
            time.sleep(0.05)
        self.fail("job didn't finish")

    # ---- locked down

    def test_the_page_needs_the_secret_from_freighters_window(self):
        self.assertEqual(self.request("GET", "/", token=False)[0], 403)
        self.assertEqual(self.request("GET", "/?t=wrong", token=False)[0], 403)
        status, page = self.request("GET", f"/?t={self.app.token}", token=False)
        self.assertEqual(status, 200)
        self.assertIn("freighter", page)
        self.assertNotIn("{{version}}", page)

    def test_api_refuses_missing_or_wrong_token(self):
        self.assertEqual(self.request("GET", "/api/ping", token=False)[0], 403)
        self.assertEqual(self.request("GET", "/api/ping", token="nope")[0], 403)
        self.assertEqual(self.request("POST", "/api/jobs", {"kind": "move_list"}, token=False)[0], 403)
        self.assertEqual(self.request("GET", "/api/ping")[0], 200)

    def test_other_websites_and_other_host_names_are_refused(self):
        self.assertEqual(self.request("GET", "/api/ping", origin="https://evil.example")[0], 403)
        self.assertEqual(self.request("GET", "/api/ping", host=f"evil.example:{self.app.port}")[0], 403,
                         "a site whose name points at this computer (DNS rebinding) is refused")
        self.assertEqual(self.request("GET", "/api/ping", origin=f"http://127.0.0.1:{self.app.port}")[0], 200)

    def test_unknown_jobs_are_refused(self):
        self.assertEqual(self.request("POST", "/api/jobs", {"kind": "rm -rf"})[0], 400)

    # ---- jobs

    def test_move_list_job(self):
        home = self.tmp / "home"
        kick = home / "Desktop" / "Kits" / "kick.wav"
        kick.parent.mkdir(parents=True)
        kick.write_bytes(os.urandom(1000))
        fl = home / "Documents" / "Image-Line" / "FL Studio" / "Projects"
        fl.mkdir(parents=True)
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from test_freighter import make_flp
        make_flp(fl / "Beat.flp", [str(kick), str(home / "gone.wav")])
        with as_home(home):
            status, started = self.request("POST", "/api/jobs", {"kind": "move_list"})
            self.assertEqual(status, 200)
            view = self.wait(started["id"])
        self.assertEqual(view["state"], "done", view.get("error"))
        r = view["result"]
        paths = [Path(it["path"]) for it in r["items"]]
        self.assertIn(home / "Documents" / "Image-Line", paths)
        self.assertIn(home / "Desktop" / "Kits", paths)
        self.assertEqual(r["missing_count"], 1)
        self.assertGreater(r["total"], 1000)
        self.assertTrue(view["progress"]["label"].startswith("Measuring"))

    def test_printed_output_reaches_the_page_not_the_terminal(self):
        def chatty(job):
            print("Scanning plugin folders...")
            print("Found 3.", flush=True)
            return {}
        with mock.patch.dict(app.JOBS, {"chatty": chatty}), mock.patch.object(self.app.router, "base") as terminal:
            status, started = self.request("POST", "/api/jobs", {"kind": "chatty"})
            view = self.wait(started["id"])
        self.assertEqual(view["log"], ["Scanning plugin folders...", "Found 3."])
        terminal.write.assert_not_called()

    def test_one_job_at_a_time_and_cancel(self):
        def slow(job):
            for i in range(10_000):
                rm.report_progress("Copying", i, 10_000)
                time.sleep(0.01)
            return {"finished": True}
        with mock.patch.dict(app.JOBS, {"slow": slow}):
            status, started = self.request("POST", "/api/jobs", {"kind": "slow"})
            self.assertEqual(status, 200)
            self.assertEqual(self.request("POST", "/api/jobs", {"kind": "slow"})[0], 409)
            time.sleep(0.1)
            self.assertEqual(self.request("POST", f"/api/jobs/{started['id']}/cancel", {})[0], 200)
            view = self.wait(started["id"])
        self.assertEqual(view["state"], "cancelled")
        self.assertIsNone(view["result"])

    def test_a_failing_job_reports_instead_of_crashing(self):
        def broken(job):
            raise ValueError("disk on fire")
        with as_home(self.tmp), mock.patch.dict(app.JOBS, {"broken": broken}):
            status, started = self.request("POST", "/api/jobs", {"kind": "broken"})
            view = self.wait(started["id"])
        self.assertEqual(view["state"], "failed")
        self.assertIn("disk on fire", view["error"])
        self.assertTrue(view["crash"] and Path(view["crash"]).is_file())
        self.assertEqual(self.request("GET", "/api/ping")[0], 200, "the server keeps running")

    def test_a_double_click_starts_one_job(self):
        import concurrent.futures
        release = __import__("threading").Event()

        def slow(job):
            release.wait(5)
            return {}
        with mock.patch.dict(app.JOBS, {"slow": slow}):
            with concurrent.futures.ThreadPoolExecutor(8) as pool:
                codes = list(pool.map(lambda _: self.request("POST", "/api/jobs", {"kind": "slow"})[0], range(8)))
            release.set()
        self.assertEqual(codes.count(200), 1, codes)
        self.assertEqual(codes.count(409), 7, codes)

    def test_bad_bodies_are_refused(self):
        c = http.client.HTTPConnection("127.0.0.1", self.app.port, timeout=10)
        c.putrequest("POST", "/api/jobs", skip_host=True)
        c.putheader("Host", f"127.0.0.1:{self.app.port}")
        c.putheader("X-Freighter-Token", self.app.token)
        c.putheader("Content-Length", "-5")
        c.endheaders()
        self.assertEqual(c.getresponse().status, 400)
        c.close()
        self.assertEqual(self.request("POST", "/api/jobs", ["move_list"])[0], 400)

    def test_cancel_isnt_swallowed_by_catch_all_handlers(self):
        go = __import__("threading").Event()

        def careful(job):
            go.wait(5)
            try:
                rm.report_progress("Copying", 1, 10)
            except Exception:       # code that carries on after ordinary errors must still stop on cancel
                pass
            return {"finished": True}
        with mock.patch.dict(app.JOBS, {"careful": careful}):
            job = self.app.start_job("careful")
            job.cancel_requested = True
            go.set()
            job.thread.join(5)
        self.assertEqual(job.state, "cancelled")

    def test_idle_timer_waits_for_the_page(self):
        self.assertIsNone(app.App().last_seen)
        self.request("GET", f"/?t={self.app.token}", token=False)
        self.assertIsNotNone(self.app.last_seen)

    # ---- the old- and new-computer screens (issues #27, #28)

    def test_job_options_are_checked(self):
        self.assertEqual(self.request("POST", "/api/jobs", {"kind": "pack", "to": str(self.tmp / "nope")})[0], 400)
        self.assertEqual(self.request("POST", "/api/jobs", {"kind": "pack", "to": str(self.tmp), "skip": "x"})[0], 400)
        self.assertEqual(self.request("POST", "/api/jobs", {"kind": "pack", "to": str(self.tmp), "include": "x"})[0], 400)
        self.assertEqual(self.request("POST", "/api/jobs", {"kind": "pack", "to": str(self.tmp),
                                                            "include": [str(self.tmp / "nope")]})[0], 400)
        self.assertEqual(self.request("POST", "/api/jobs", {"kind": "unpack", "bundle": str(self.tmp)})[0], 400,
                         "a folder without manifest.json isn't a bundle")

    def test_pack_then_unpack_and_repair_through_the_app(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from test_freighter import make_flp
        old, new, drive = self.tmp / "old", self.tmp / "new", self.tmp / "drive"
        kick = old / "Desktop" / "Kits" / "kick.wav"
        kick.parent.mkdir(parents=True)
        kick.write_bytes(b"kick" * 100)
        (old / "Desktop" / "Kits" / "unused.wav").write_bytes(b"u" * 50)
        make_flp(old / "Documents" / "Image-Line" / "FL Studio" / "Projects" / "Beat" / "Beat.flp", [str(kick)])
        drive.mkdir()
        with as_home(old):
            status, started = self.request("POST", "/api/jobs", {"kind": "pack", "to": str(drive), "skip": []})
            self.assertEqual(status, 200)
            packed = self.wait(started["id"])
        self.assertEqual(packed["state"], "done", packed.get("error"))
        self.assertEqual(packed["result"]["errors"], 0)
        self.assertTrue(Path(packed["result"]["bundle"]).is_dir())
        status, f = self.request("POST", "/api/folder", {"path": str(drive)})
        self.assertEqual([b["path"] for b in f["bundles"]], [packed["result"]["bundle"]], "the bundle is found")
        self.assertEqual(f["bundles"][0]["problems"], [])
        shutil.rmtree(old)                      # the old computer is gone
        new.mkdir()
        with as_home(new), mock.patch.object(rm, "fl_is_running", lambda: False):
            status, started = self.request("POST", "/api/jobs", {"kind": "unpack", "bundle": packed["result"]["bundle"]})
            self.assertEqual(status, 200)
            done = self.wait(started["id"])
        self.assertEqual(done["state"], "done", done.get("error"))
        u, rep = done["result"]["unpack"], done["result"]["repair"]
        self.assertEqual(u["problems"], [])
        self.assertEqual(rep["repaired"], 1)
        self.assertEqual(rep["samples_fixed"], 1)
        beat = new / "Documents" / "Image-Line" / "FL Studio" / "Projects" / "Beat" / "Beat.flp"
        self.assertEqual(Path(rm.flp_samples(beat)[1][0]).read_bytes(), b"kick" * 100)
        # and the whole unpack can be undone from the app (issue #73)
        with as_home(new):
            status, p = self.request("POST", "/api/unpack-preview", {"bundle": packed["result"]["bundle"]})
            self.assertEqual(status, 200)
            status, h = self.request("GET", "/api/home")    # the home screen offers repair and undo, even later
            self.assertEqual([x["host"] for x in h["unpacks"]], [u["source_host"]])
            self.assertGreaterEqual(h["unpacks"][0]["files"], 2)
            status, started = self.request("POST", "/api/jobs", {"kind": "undo", "host": u["source_host"]})
            undone = self.wait(started["id"])
            self.assertEqual(self.request("GET", "/api/home")[1]["unpacks"], [], "nothing left to undo")
        self.assertEqual(undone["state"], "done", undone.get("error"))
        self.assertFalse(beat.exists())
        self.assertFalse((new / "Desktop" / "Kits").exists())

    def test_rehearse_then_delete_the_rehearsal_through_the_app(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from test_freighter import make_flp
        home = self.tmp / "home"
        kick = home / "Desktop" / "Kits" / "kick.wav"
        kick.parent.mkdir(parents=True)
        kick.write_bytes(b"kick" * 100)
        beat = home / "Documents" / "Image-Line" / "FL Studio" / "Projects" / "Beat.flp"
        make_flp(beat, [str(kick)])
        before = beat.read_bytes()
        with as_home(home), mock.patch.object(rm, "fl_is_running", lambda: False):
            status, where = self.request("GET", "/api/rehearsal")
            self.assertEqual(Path(where["default"]), (home / "Music" / "Freighter" / "Rehearsal").resolve())
            self.assertGreater(where["free"], 0)
            status, started = self.request("POST", "/api/jobs", {"kind": "rehearse", "to": None, "skip": []})
            self.assertEqual(status, 200, started)
            done = self.wait(started["id"])
            self.assertEqual(done["state"], "done", done.get("error"))
            r = done["result"]
            self.assertTrue(r["passed"])
            self.assertEqual(r["totals"]["kept"], 1)
            self.assertEqual(r["totals"]["lost"], 0)
            self.assertTrue(done["stage"].startswith("4/4"), "the page shows which step it's on")
            self.assertEqual(os.environ["FREIGHTER_HOME"], str(home), "the real home is back afterwards")
            status, h = self.request("GET", "/api/home")
            self.assertEqual(h["rehearsals"], [r["folder"]], "the home screen offers to delete it")
            status, started = self.request("POST", "/api/jobs", {"kind": "rehearse_cleanup", "folder": r["folder"]})
            self.assertEqual(status, 200, started)
            cleaned = self.wait(started["id"])
            self.assertEqual(cleaned["state"], "done", cleaned.get("error"))
            self.assertGreater(cleaned["result"]["freed"], 400)
            self.assertFalse(Path(r["folder"]).exists())
            self.assertEqual(self.request("GET", "/api/home")[1]["rehearsals"], [])
        self.assertEqual(kick.read_bytes(), b"kick" * 100, "real files are only read")
        self.assertEqual(beat.read_bytes(), before)

    def test_only_rehearsal_folders_can_be_deleted(self):
        plain = self.tmp / "Rehearsal"          # the right name, but Freighter didn't make it
        plain.mkdir()
        (plain / "song.wav").write_bytes(b"x")
        for folder in (str(self.tmp), str(plain), str(self.tmp / "nope")):
            status, r = self.request("POST", "/api/jobs", {"kind": "rehearse_cleanup", "folder": folder})
            self.assertEqual(status, 400, folder)
        marked = self.tmp / "Samples"           # the marker, but not a rehearsal's name
        marked.mkdir()
        (marked / rm.REHEARSAL_MARKER).write_text("x")
        with self.assertRaises(SystemExit):
            rm.remove_rehearsal(marked)
        self.assertTrue((plain / "song.wav").exists())
        self.assertTrue(marked.exists())
        self.assertEqual(self.request("POST", "/api/jobs", {"kind": "rehearse", "to": str(self.tmp / "nope")})[0], 400)
        self.assertEqual(self.request("POST", "/api/jobs", {"kind": "rehearse", "skip": "x"})[0], 400)

    def test_rehearse_clean_from_the_command_line(self):
        base = rm.rehearsal_folder(self.tmp)
        (base / "drive").mkdir(parents=True)
        (base / rm.REHEARSAL_MARKER).write_text("x")
        (base / "drive" / "copy.wav").write_bytes(b"c" * 10)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(rm.cmd_rehearse(rm.argparse.Namespace(to=str(self.tmp), include=[], skip=[], clean=True)), 0)
        self.assertFalse(base.exists())
        self.assertTrue(self.tmp.exists())

    def test_crate_preview_build_and_remove(self):
        """Issue #34: Crate in the app. The library is links; the originals stay."""
        kits = self.tmp / "Samples" / "UNDRGRND Sounds - Deep House Kit"
        (kits / "Kicks").mkdir(parents=True)
        (kits / "Kicks" / "kick.wav").write_bytes(b"k" * 100)
        (kits / "Kicks" / "kick copy.wav").write_bytes(b"k" * 100)       # a duplicate
        self.assertEqual(self.request("POST", "/api/jobs", {"kind": "crate_preview", "folders": []})[0], 400)
        with as_home(self.tmp / "home"):
            _, started = self.request("POST", "/api/jobs", {"kind": "crate_preview", "folders": [str(self.tmp / "Samples")]})
            preview = self.wait(started["id"])
            self.assertEqual(preview["state"], "done", preview.get("error"))
            self.assertEqual((preview["result"]["unique"], preview["result"]["duplicates"]), (1, 1))
            self.assertEqual(preview["result"]["genres"][0][0], "House")
            _, started = self.request("POST", "/api/jobs", {"kind": "crate_build", "folders": [str(self.tmp / "Samples")]})
            built = self.wait(started["id"])
            self.assertEqual(built["result"]["made"], 1)
            library = Path(built["result"]["library"])
            self.assertTrue(list(library.rglob("kick*.wav")))
            _, started = self.request("POST", "/api/jobs", {"kind": "crate_undo"})
            self.assertEqual(self.wait(started["id"])["state"], "done")
        self.assertFalse(library.exists())
        self.assertTrue((kits / "Kicks" / "kick.wav").is_file(), "originals untouched")

    def test_a_time_range_move_list(self):
        """Issue #76: the move list for the projects saved in a time range, with libraries offered."""
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from test_freighter import make_flp
        home = self.tmp / "home"
        kick = home / "Desktop" / "Kits" / "House" / "Tech House Kit" / "Kicks" / "kick.wav"
        kick.parent.mkdir(parents=True)
        kick.write_bytes(b"k" * 100)
        (home / "Desktop" / "Kits" / "House" / "Other Kit").mkdir()
        projects = home / "Documents" / "Image-Line" / "FL Studio" / "Projects"
        new, old = make_flp(projects / "New.flp", [str(kick)]), make_flp(projects / "Old.flp", [])
        t = time.mktime((2020, 1, 15, 12, 0, 0, 0, 0, -1))
        os.utime(old, (t, t))
        for bad in ({"since": "banana"}, {"since": "2026-03", "until": "2024-03"}, {"since": 5}):
            status, r = self.request("POST", "/api/jobs", dict(kind="move_list", **bad))
            self.assertEqual(status, 400, bad)
        with as_home(home):
            status, started = self.request("POST", "/api/jobs", {"kind": "move_list", "since": "2024-03"})
            self.assertEqual(status, 200, started)
            view = self.wait(started["id"])
        self.assertEqual(view["state"], "done", view.get("error"))
        r = view["result"]
        self.assertEqual((r["projects"]["in_range"], r["projects"]["total"]), (1, 2))
        self.assertEqual(r["range"]["label"], "since Mar 2024")
        self.assertIn("Kit: Tech House Kit", [it["label"] for it in r["items"]])
        self.assertEqual(self.request("POST", "/api/jobs", {"kind": "pack", "to": str(self.tmp), "until": "nope"})[0], 400)

    def test_typed_folders(self):
        """Explorer's "Copy as path" adds quotes; a bare drive letter means the top of that drive."""
        self.assertEqual(str(app.typed_folder("E:", windows=True)), "E:\\")
        self.assertEqual(str(app.typed_folder(' "E:" ', windows=True)), "E:\\")
        self.assertEqual(app.typed_folder('"/Volumes/My Drive" ', windows=False), Path("/Volumes/My Drive"))
        self.assertEqual(app.typed_folder("~/Music", windows=False), Path.home() / "Music")

    def test_maker_links(self):
        """Issue #30: the maker's own site when known (the longest matching name wins), otherwise a web search."""
        self.assertEqual(app.maker_link("Waves Audio Ltd."), "https://www.waves.com/")
        self.assertEqual(app.maker_link("Wavesfactory"), "https://www.wavesfactory.com/")
        self.assertEqual(app.maker_link("Xfer Records"), "https://xferrecords.com/")
        self.assertIsNone(app.maker_link("Somebody Unknown"))
        self.assertIsNone(app.maker_link(None))
        self.assertGreaterEqual(len(app.MAKER_LINKS), 50)
        self.assertTrue(all(u.startswith("https://") for u in app.MAKER_LINKS.values()))
        self.assertEqual(app.search_link("Kit Maker", "Drumolution"),
                         "https://duckduckgo.com/?q=Drumolution+Kit+Maker+plugin+download")

    def test_drives_are_listed(self):
        status, d = self.request("GET", "/api/drives")
        self.assertEqual(status, 200)
        self.assertTrue(d["drives"], "at least this computer's own disk")
        self.assertTrue(all({"name", "path", "free", "total", "bundles"} <= set(x) for x in d["drives"]))

    def test_quit_stops(self):
        self.assertEqual(self.request("POST", "/api/quit", {})[0], 200)
        self.assertTrue(self.app.stopped.is_set())


if __name__ == "__main__":
    unittest.main()
