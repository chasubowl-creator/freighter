"""Tests for the browser app's local server. Run: python3 -m unittest discover -s tests -v"""
import contextlib
import http.client
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

    def test_quit_stops(self):
        self.assertEqual(self.request("POST", "/api/quit", {})[0], 200)
        self.assertTrue(self.app.stopped.is_set())


if __name__ == "__main__":
    unittest.main()
