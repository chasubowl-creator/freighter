#!/usr/bin/env python3
"""
_Freighter app: Freighter in your web browser. Start it with `freighter app`.

It runs a small web server that only this computer can reach (127.0.0.1, a random port) and opens the
browser on it. Every request must carry a secret that's only in the address the browser was opened with,
and requests from other websites are refused, so a web page can't drive Freighter behind your back.
Nothing leaves your computer. Needs only Python 3.8+.
"""

import argparse
import csv
import hmac
import http.server
import io
import json
import secrets
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import crate  # noqa: E402
import freighter as rm  # noqa: E402
import manifest as rs  # noqa: E402
from freighter_version import __version__  # noqa: E402

IDLE_SECONDS = 180   # stop when the page hasn't been heard from for this long (and nothing is running)
MAX_LOG_LINES = 400


# --------------------------------------------------------------------------- jobs

class OutputRouter(io.TextIOBase):
    """Sends print() output from a job's thread into that job's log; everything else goes on as usual."""

    def __init__(self, base):
        self.base, self.local = base, threading.local()

    def write(self, s):
        job = getattr(self.local, "job", None)
        if job is None:
            return self.base.write(s)
        job.add_output(s)
        return len(s)

    def flush(self):
        self.base.flush()


class Job:
    def __init__(self, kind, fn, router, params=None):
        self.id = secrets.token_hex(6)
        self.kind, self.fn, self.router, self.params = kind, fn, router, params or {}
        self.state, self.progress, self.result, self.error, self.crash = "running", None, None, None, None
        self.lines, self.partial, self.cancel_requested = [], "", False
        self.stage = None   # the latest "== 2/4  Unpack…" heading a long command printed
        self.files = {}   # things the page may ask to open, by name -> local path
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()
        return self

    def _run(self):
        self.router.local.job = self
        rm.PROGRESS.listener = self.on_progress
        try:
            self.result = self.fn(self)
            self.state = "done"   # last: the page stops polling as soon as it sees this
        except rm.Cancelled:
            self.state = "cancelled"
        except SystemExit as e:   # the commands stop with a message when something's wrong
            self.error = str(e.code) if e.code not in (None, 0) else "Stopped."
            self.state = "failed"
        except Exception as e:
            report = rm.write_crash_report(f"app: {self.kind}")
            traceback.print_exc(file=self.router.base)
            self.crash = str(report) if report else None
            self.error = f"{type(e).__name__}: {e}"
            self.state = "failed"   # last: the page stops polling as soon as it sees this
        finally:
            rm.PROGRESS.listener = None
            self.router.local.job = None

    def on_progress(self, label, done, total, extra=None):
        self.progress = dict(extra or {}, label=label, done=done, total=total)
        if self.cancel_requested:
            raise rm.Cancelled()

    def add_output(self, s):
        with self.lock:
            text = self.partial + s
            *done, self.partial = text.split("\n")
            self.lines.extend(line.rstrip() for line in done if line.strip())
            for line in done:
                if line.startswith("== "):
                    self.stage = line[3:].strip()
            del self.lines[:-MAX_LOG_LINES]

    def view(self):
        with self.lock:
            return {"id": self.id, "kind": self.kind, "state": self.state, "progress": self.progress, "stage": self.stage,
                    "log": self.lines[-12:], "result": self.result, "error": self.error, "crash": self.crash}


def job_move_list(job):
    """What a move would take from this computer: the same list as `freighter pack --list`."""
    plan = rm.plan_move()
    items, total, left_behind = plan["items"], plan["total"], plan["left_behind"]
    missing, installed, cloud = plan["missing"], plan["installed"], plan["cloud_only"]
    gone = rm.missing_lines(missing)
    return {
        "items": [{"id": it["id"], "label": it["label"], "path": it["source_path"], "bytes": it["bytes"],
                   "files": it["files"], "kind": it["kind"]} for it in items],
        "total": total,
        "missing": gone[:200], "missing_count": len(gone), "missing_projects": len(missing),
        "left_behind": len(left_behind or []),
        "installed": len(installed),
        "cloud": {"files": len(cloud), "bytes": sum(n for _, n in cloud)},
    }


def job_plugins(job):
    """Scan this computer's plugins (the _Manifest report) and summarise it."""
    html_path, csv_path = rs.run_scan(quick=True)
    job.files["report"] = Path(html_path)
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    makers = {}
    for r in rows:
        makers.setdefault(r.get("vendor") or "Unknown maker", set()).add(r.get("name", ""))
    flagged = [{"name": r.get("name", ""), "vendor": r.get("vendor", ""), "format": r.get("format", ""),
                "flags": r.get("flags", "")} for r in rows if r.get("flags")]
    return {
        "count": len(rows),
        "plugins": len({(r.get("vendor"), r.get("name")) for r in rows}),
        "makers": sorted(([m, len(n)] for m, n in makers.items()), key=lambda x: (-x[1], x[0].lower()))[:60],
        "maker_count": len(makers),
        "flagged": flagged[:100],
    }


def job_pack(job):
    """Pack this computer onto a drive or folder, leaving out the items the person unticked."""
    p = job.params
    rm.LAST.pop("pack", None)
    rm.cmd_pack(argparse.Namespace(to=p["to"], list=False, include=[], skip=list(p.get("skip", [])),
                                   include_programs=False))
    return rm.LAST.get("pack")


def job_unpack(job):
    """Unpack a bundle, then repair every project so it points at the moved samples."""
    p = job.params
    rm.LAST.pop("unpack", None)
    rm.cmd_unpack(argparse.Namespace(bundle=p["bundle"], dest_for=[], keep_together=bool(p.get("keep_together")),
                                     anyway=bool(p.get("anyway"))))
    rm.report_progress("Repairing projects", 0, 0)
    repair = rm.repair_projects()
    return {"unpack": rm.LAST.get("unpack"), "repair": dict(repair, missing=repair["missing"][:100],
                                                            missing_count=len(repair["missing"]))}


# where to get each maker's plugins (their own site; installers often come through the maker's app)
MAKER_LINKS = {
    "xfer records": "https://xferrecords.com/", "native instruments": "https://www.native-instruments.com/",
    "waves": "https://www.waves.com/", "arturia": "https://www.arturia.com/", "fabfilter": "https://www.fabfilter.com/",
    "u-he": "https://u-he.com/", "spitfire audio": "https://www.spitfireaudio.com/", "valhalla dsp": "https://valhalladsp.com/",
    "izotope": "https://www.izotope.com/", "kilohearts": "https://kilohearts.com/", "image-line": "https://www.image-line.com/",
    "plugin alliance": "https://www.plugin-alliance.com/", "output": "https://output.com/", "soundtoys": "https://www.soundtoys.com/",
    "vital audio": "https://vital.audio/", "spectrasonics": "https://www.spectrasonics.net/", "toontrack": "https://www.toontrack.com/",
    "universal audio": "https://www.uaudio.com/", "slate digital": "https://slatedigital.com/", "eastwest": "https://www.soundsonline.com/",
    "nicky romero": "https://kickstart-plugin.com/", "cableguys": "https://www.cableguys.com/", "xln audio": "https://www.xlnaudio.com/",
    "lennardigital": "https://www.lennardigital.com/", "reveal sound": "https://revealsound.com/",
}


def maker_link(vendor):
    v = (vendor or "").lower()
    return next((url for name, url in MAKER_LINKS.items() if name in v), None)


def job_readiness(job):
    """Which plugins this computer's projects need, most useful first (issue #70), ready for a checklist."""
    maps = rm.load_pathmaps(None)
    roots = rm.project_search_roots(maps, [])
    rm.report_progress("Reading your projects", 0, 0)
    projects = {str(p): rm.project_plugins(p) for p in rm.all_projects(roots)}
    rm.report_progress("Scanning installed plugins", 0, 0)
    r = rm.plugin_readiness(projects, rs.scan([]))
    # makers the projects didn't name: take them from the old computer's plugin list, copied over on unpack
    known = {}
    for f in (rm.home() / "Music" / "Freighter").glob("*/plugins-*.csv"):
        try:
            with open(f, newline="", encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    for k in rs.match_keys(row.get("name", ""), row.get("vendor", ""), row.get("key", "")):
                        known.setdefault(k, row.get("vendor", ""))
        except OSError:
            pass
    missing = []
    for e in r["missing"]:
        vendor = e["vendor"] or next((known[k] for k in rs.match_keys(e["name"], "") if known.get(k)), "")
        missing.append({"name": e["name"], "vendor": vendor, "link": maker_link(vendor), "opens": e["opens"],
                        "projects": [Path(x).name for x in e["projects"]]})
    return {"total": r["total"], "ready": len(r["ready"]), "missing": missing, "warnings": r["warnings"],
            "versions": [{"daw": d, "version": v, "example": n} for d, (v, n) in sorted(r["versions"].items())],
            "on_old_computer": not any((rm.home() / "Music" / "Freighter").glob("*/pathmap.json"))}


def clean_params(kind, body):
    """The options a job may take, checked. Returns (params, error)."""
    if kind == "pack":
        to = body.get("to")
        skip = body.get("skip", [])
        if not isinstance(to, str) or not Path(to).expanduser().is_dir():
            return None, "Choose a drive or folder that exists."
        if not isinstance(skip, list) or not all(isinstance(x, str) for x in skip):
            return None, "bad request"
        return {"to": str(Path(to).expanduser()), "skip": skip}, None
    if kind == "unpack":
        b = body.get("bundle")
        if not isinstance(b, str) or not (Path(b).expanduser() / "manifest.json").is_file():
            return None, "That isn't a Freighter bundle (it has no manifest.json)."
        return {"bundle": str(Path(b).expanduser()), "keep_together": bool(body.get("keep_together")),
                "anyway": bool(body.get("anyway"))}, None
    if kind in ("crate_preview", "crate_build"):
        folders, by = body.get("folders"), body.get("by", "genre")
        if not isinstance(folders, list) or not folders or len(folders) > 20 or \
                not all(isinstance(f, str) and Path(f).expanduser().is_dir() for f in folders):
            return None, "Choose one or more sample folders that exist."
        if by not in ("genre", "type"):
            return None, "bad request"
        return {"folders": [str(Path(f).expanduser()) for f in folders], "by": by}, None
    if kind == "rehearse":
        to, skip = body.get("to"), body.get("skip", [])
        if to is not None and (not isinstance(to, str) or not Path(to).expanduser().is_dir()):
            return None, "Choose a drive or folder that exists."
        if not isinstance(skip, list) or not all(isinstance(x, str) for x in skip):
            return None, "bad request"
        return {"to": str(Path(to).expanduser()) if to else None, "skip": skip}, None
    if kind == "rehearse_cleanup":
        f = body.get("folder")
        if not isinstance(f, str) or not rm.is_rehearsal(Path(f).expanduser()):
            return None, "That isn't a Freighter rehearsal folder."
        return {"folder": str(Path(f).expanduser().resolve())}, None
    if kind == "undo":
        host = body.get("host")
        if host is not None and (not isinstance(host, str) or "/" in host or "\\" in host or host in (".", "..")):
            return None, "bad request"
        return {"host": host}, None
    return {}, None


LAST_CRATE = {}   # the last Crate plan, so building right after a preview doesn't read every file again


def crate_items(folders):
    key = tuple(folders)
    if LAST_CRATE.get("key") != key:
        LAST_CRATE.update(key=key, items=crate.plan(list(folders)))
    return LAST_CRATE["items"]


def job_crate_preview(job):
    """How _Crate would sort these sample folders (issue #34). Changes nothing."""
    LAST_CRATE.clear()
    items = crate_items(job.params["folders"])
    library = crate.default_library()
    job.files["report"] = crate.write_report(items, library.parent / "Freighter Crate preview", "_Crate preview")
    unique = [it for it in items if not it["duplicate_of"]]
    genres, types = {}, {}
    for it in unique:
        g = genres.setdefault(it.get("genre") or "Other", {"packs": set(), "sounds": 0})
        g["packs"].add(it["pack"])
        g["sounds"] += 1
        top = it["category"].split("/")[0]
        types[top] = types.get(top, 0) + 1
    return {"files": len(items), "unique": len(unique), "duplicates": len(items) - len(unique),
            "guessed": sum(1 for it in unique if it["how"].startswith("sound:")),
            "genres": sorted(([g, len(v["packs"]), v["sounds"]] for g, v in genres.items()), key=lambda x: -x[2]),
            "types": sorted(types.items(), key=lambda x: -x[1]), "library": str(library),
            "library_exists": library.exists()}


def job_crate_build(job):
    """Build the sorted library out of links (no extra space; originals untouched)."""
    items = crate_items(job.params["folders"])
    library = crate.default_library()
    made, links, copies, skipped = crate.build(items, library, False, job.params["by"])
    crate.write_report(items, library, "_Crate library")
    job.files["library"] = library
    return {"made": made, "links": links, "copies": copies, "skipped": skipped, "library": str(library)}


def job_crate_undo(job):
    """Remove the library Crate built; originals are never touched."""
    return {"removed": crate.undo(crate.default_library()), "library": str(crate.default_library())}


def job_undo(job):
    """Undo the unpack from one computer (or all): remove what it created, put repaired projects back."""
    return rm.undo_unpack(job.params.get("host"))


def job_rehearse(job):
    """A whole practice move on this computer (the menu's option 3): pack, unpack into a stand-in new computer,
    repair, then check every sample. The real files are only read."""
    p = job.params
    rm.LAST.pop("rehearse", None)
    rm.cmd_rehearse(argparse.Namespace(to=p.get("to"), include=[], skip=list(p.get("skip", []))))
    r = rm.LAST["rehearse"]
    job.files["report"] = r["report"]
    return dict(r, lost=r["lost"][:200], already=r["already"][:200], unchecked=r["unchecked"][:100])


def job_rehearse_cleanup(job):
    """Delete a rehearsal's files."""
    folder = job.params["folder"]
    return {"freed": rm.remove_rehearsal(folder), "folder": folder}


def leftover_rehearsals():
    """Rehearsals whose files are still on disk: the usual place, and wherever the last one in this session went."""
    found = []
    for f in (rm.rehearsal_folder(), rm.LAST.get("rehearse_folder")):
        if f and str(f) not in found and rm.is_rehearsal(f):
            found.append(str(f))
    return found


def job_repair(job):
    """Repair projects again (e.g. after closing FL Studio)."""
    r = rm.repair_projects()
    return dict(r, missing=r["missing"][:100], missing_count=len(r["missing"]))


JOBS = {"move_list": job_move_list, "plugins": job_plugins, "pack": job_pack, "unpack": job_unpack,
        "readiness": job_readiness, "repair": job_repair, "undo": job_undo,
        "rehearse": job_rehearse, "rehearse_cleanup": job_rehearse_cleanup,
        "crate_preview": job_crate_preview, "crate_build": job_crate_build, "crate_undo": job_crate_undo}


# --------------------------------------------------------------------------- server

class App:
    def __init__(self):
        self.token = secrets.token_urlsafe(24)
        # last_seen stays None until the page first loads, so a slow copy-paste of the address can't time out
        self.jobs, self.last_seen, self.port, self.server = {}, None, None, None
        self.lock = threading.Lock()
        self.stopped = threading.Event()   # set by the page's Quit button
        self.router = sys.stdout if isinstance(sys.stdout, OutputRouter) else OutputRouter(sys.stdout)

    @property
    def origins(self):
        return {f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}"}

    def busy(self):
        with self.lock:
            return any(j.state == "running" for j in list(self.jobs.values()))

    def start_job(self, kind, params=None):
        """Start a job unless one is running. The check and the start happen together, so a double click can't
        start two."""
        with self.lock:
            if any(j.state == "running" for j in self.jobs.values()):
                return None
            job = Job(kind, JOBS[kind], self.router, params)
            self.jobs[job.id] = job
            return job.start()


class Handler(http.server.BaseHTTPRequestHandler):
    app = None            # set by serve()
    server_version = "Freighter"
    sys_version = ""

    def log_message(self, *args):
        pass

    def send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else (
            json.dumps(body) if ctype.startswith("application/json") else body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; "
                         "style-src 'unsafe-inline'; img-src data:; connect-src 'self'; frame-ancestors 'none'; "
                         "base-uri 'none'; form-action 'none'")
        self.end_headers()
        self.wfile.write(data)

    def host_ok(self):
        # refuses DNS-rebinding: a hostile site whose name points at 127.0.0.1 still sends its own Host
        return self.headers.get("Host", "") in {o[len("http://"):] for o in self.app.origins}

    def api_ok(self):
        token = self.headers.get("X-Freighter-Token", "")
        origin = self.headers.get("Origin")
        return hmac.compare_digest(token.encode(), self.app.token.encode()) and (origin is None or origin in self.app.origins)

    def do_GET(self):
        if not self.host_ok():
            return self.send(403, {"error": "forbidden"})
        url = urlsplit(self.path)
        if url.path == "/":
            given = (parse_qs(url.query).get("t") or [""])[0]
            if not hmac.compare_digest(given.encode(), self.app.token.encode()):
                return self.send(403, LOCKED_PAGE, "text/html; charset=utf-8")
            self.app.last_seen = time.time()
            return self.send(200, PAGE.replace("{{version}}", __version__), "text/html; charset=utf-8")
        if not url.path.startswith("/api/") or not self.api_ok():
            return self.send(403 if url.path.startswith("/api/") else 404, {"error": "forbidden"})
        self.app.last_seen = time.time()
        if url.path == "/api/ping":
            return self.send(200, {"version": __version__, "busy": self.app.busy()})
        if url.path == "/api/crate-suggestions":
            # FL's browser folders are where producers keep their sample libraries
            return self.send(200, {"folders": [f for f in rm.fl_settings()["search_paths"] if Path(f).is_dir()],
                                   "library_exists": crate.default_library().exists()})
        if url.path == "/api/home":
            # what's already on this computer: unpacks that can be repaired or undone, rehearsals to delete
            return self.send(200, {"unpacks": rm.earlier_unpacks(), "rehearsals": leftover_rehearsals()})
        if url.path == "/api/rehearsal":
            # where a rehearsal would go by default, the room there, and rehearsals still taking up space
            default = rm.rehearsal_folder()
            return self.send(200, {"default": str(default), "free": rm.shutil.disk_usage(rm.existing_parent(default)).free,
                                   "leftovers": leftover_rehearsals()})
        if url.path == "/api/drives":
            return self.send(200, {"drives": rm.list_drives(), "os": rm.THIS_OS})
        if url.path.startswith("/api/jobs/"):
            job = self.app.jobs.get(url.path.split("/")[3])
            return self.send(200, job.view()) if job else self.send(404, {"error": "no such job"})
        return self.send(404, {"error": "not found"})

    def do_POST(self):
        if not self.host_ok() or not self.api_ok():
            return self.send(403, {"error": "forbidden"})
        self.app.last_seen = time.time()
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n < 0 or n > 65536:
                raise ValueError("bad length")
            body = json.loads(self.rfile.read(n) or b"{}") if n else {}
            if not isinstance(body, dict):
                raise ValueError("not an object")
        except ValueError:
            return self.send(400, {"error": "bad request"})
        path = urlsplit(self.path).path
        if path == "/api/jobs":
            kind = body.get("kind")
            if kind not in JOBS:
                return self.send(400, {"error": "unknown job"})
            params, problem = clean_params(kind, body)
            if problem:
                return self.send(400, {"error": problem})
            job = self.app.start_job(kind, params)
            return self.send(200, {"id": job.id}) if job else self.send(409, {"error": "Something is already running."})
        parts = path.split("/")
        if len(parts) == 5 and parts[2] == "jobs" and parts[4] == "cancel":
            job = self.app.jobs.get(parts[3])
            if not job:
                return self.send(404, {"error": "no such job"})
            job.cancel_requested = True
            return self.send(200, {"ok": True})
        if len(parts) == 5 and parts[2] == "jobs" and parts[4] == "open":
            job = self.app.jobs.get(parts[3])
            target = job and job.files.get(body.get("file"))
            if not target or not Path(target).exists():
                return self.send(404, {"error": "nothing to open"})
            webbrowser.open(Path(target).resolve().as_uri())
            return self.send(200, {"ok": True})
        if path == "/api/folder":
            # a folder typed in by hand: does it exist, how much room, and any bundles in or at it
            f = body.get("path")
            if not isinstance(f, str) or not f.strip():
                return self.send(400, {"error": "bad request"})
            folder = Path(f.strip().strip('"')).expanduser()
            if not folder.is_dir():
                return self.send(200, {"exists": False, "path": str(folder)})
            here = rm.bundle_summary(folder)
            return self.send(200, {"exists": True, "path": str(folder), "free": rm.shutil.disk_usage(folder).free,
                                   "fs": rm.filesystem(folder),
                                   "bundles": [here] if here else [b for b in (rm.bundle_summary(x)
                                                                               for x in rm.find_bundles(folder)) if b]})
        if path == "/api/unpack-preview":
            # where everything would go, before anything is copied
            params, problem = clean_params("unpack", body)
            if problem:
                return self.send(400, {"error": problem})
            rm.LAST.pop("unpack_preview", None)
            try:
                rm.cmd_unpack(argparse.Namespace(bundle=params["bundle"], dest_for=[], preview=True,
                                                 keep_together=params["keep_together"]))
            except SystemExit as e:
                return self.send(400, {"error": str(e.code)})
            return self.send(200, {"plan": rm.LAST.get("unpack_preview", [])})
        if path == "/api/quit":
            self.app.stopped.set()   # before answering, so whoever sees the answer sees Freighter stopping
            return self.send(200, {"ok": True})
        return self.send(404, {"error": "not found"})


def serve(app=None, port=0):
    """Start the server in the background. Returns (app, server, address to open)."""
    app = app or App()
    handler = type("BoundHandler", (Handler,), {"app": app})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.daemon_threads = True
    app.port, app.server = server.server_address[1], server
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return app, server, f"http://127.0.0.1:{app.port}/?t={app.token}"


def run(open_browser=True):
    """`freighter app`: serve until the page is closed (or Ctrl+C)."""
    app = App()
    sys.stdout = app.router
    app, server, url = serve(app)
    print(f"\n  _Freighter {__version__} is open in your web browser.")
    print("  If it didn't open, copy this address into your browser:\n")
    print(f"    {url}\n")
    print("  Keep this window open while you use Freighter. Closing it stops Freighter.")
    if open_browser:
        webbrowser.open(url)
    try:
        while not app.stopped.wait(2):
            if app.last_seen is not None and not app.busy() and time.time() - app.last_seen > IDLE_SECONDS:
                print("  The Freighter page was closed, so Freighter stopped.")
                break
    except KeyboardInterrupt:
        pass
    server.shutdown()
    return 0


# --------------------------------------------------------------------------- the page

from app_page import LOCKED_PAGE, PAGE  # noqa: E402  (its own file: it's long)


if __name__ == "__main__":
    sys.exit(run())
