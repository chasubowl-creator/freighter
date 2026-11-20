#!/usr/bin/env python3
"""
_Freighter app: Freighter in your web browser. Start it with `freighter app`.

It runs a small web server that only this computer can reach (127.0.0.1, a random port) and opens the
browser on it. Every request must carry a secret that's only in the address the browser was opened with,
and requests from other websites are refused, so a web page can't drive Freighter behind your back.
Nothing leaves your computer. Needs only Python 3.8+.
"""

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
    def __init__(self, kind, fn, router):
        self.id = secrets.token_hex(6)
        self.kind, self.fn, self.router = kind, fn, router
        self.state, self.progress, self.result, self.error, self.crash = "running", None, None, None, None
        self.lines, self.partial, self.cancel_requested = [], "", False
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
            self.state = "done"
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

    def on_progress(self, label, done, total):
        self.progress = {"label": label, "done": done, "total": total}
        if self.cancel_requested:
            raise rm.Cancelled()

    def add_output(self, s):
        with self.lock:
            text = self.partial + s
            *done, self.partial = text.split("\n")
            self.lines.extend(line.rstrip() for line in done if line.strip())
            del self.lines[:-MAX_LOG_LINES]

    def view(self):
        with self.lock:
            return {"id": self.id, "kind": self.kind, "state": self.state, "progress": self.progress,
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


JOBS = {"move_list": job_move_list, "plugins": job_plugins}


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

    def start_job(self, kind):
        """Start a job unless one is running. The check and the start happen together, so a double click can't
        start two."""
        with self.lock:
            if any(j.state == "running" for j in self.jobs.values()):
                return None
            job = Job(kind, JOBS[kind], self.router)
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
            job = self.app.start_job(kind)
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

LOCKED_PAGE = """<!doctype html><meta charset=utf-8><title>Freighter</title>
<body style="font:16px/1.5 -apple-system,'Segoe UI',sans-serif;background:#0f1114;color:#eef0f2;padding:40px">
<p>This page only opens from Freighter's own window. Start Freighter again and use the address it shows.</p>"""

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Freighter</title>
<style>
:root{--bg:#0f1114;--panel:#171a1f;--panel2:#1d2127;--line:#272c33;--ink:#eef0f2;--muted:#9aa1ab;--blue:#3d8bff;
--blue2:#2f6fd6;--warn:#f0b44b;--bad:#ff6b6b;--ok:#4cc38a}
*{box-sizing:border-box}html,body{margin:0;background:var(--bg);color:var(--ink)}
body{font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
header{display:flex;align-items:center;gap:12px;padding:14px 20px;border-bottom:1px solid var(--line)}
.logo{font-weight:700;letter-spacing:.2px}.logo b{color:var(--blue)}
.ver{color:var(--muted);font-size:12px}header .sp{flex:1}
main{max-width:980px;margin:0 auto;padding:28px 16px 64px}
h1{font-size:30px;line-height:1.15;margin:6px 0 6px}h2{font-size:19px;margin:28px 0 10px}
.sub{color:var(--muted);margin:0 0 22px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px;display:flex;flex-direction:column;gap:8px}
.card h3{margin:0;font-size:16px}.card h3 b{color:var(--blue)}.card p{margin:0;color:var(--muted);font-size:14px;flex:1}
button{font:inherit;border-radius:7px;border:1px solid var(--line);background:var(--panel2);color:var(--ink);padding:8px 14px;cursor:pointer}
button.primary{background:var(--blue);border-color:var(--blue);color:#fff;font-weight:600}
button.primary:hover{background:var(--blue2)}button:disabled{opacity:.45;cursor:default}
button.link{background:none;border:none;color:var(--blue);padding:0}
.soon{font-size:12px;color:var(--muted)}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px;margin:12px 0}
.bar{height:8px;background:var(--panel2);border-radius:5px;overflow:hidden;margin:10px 0}
.bar i{display:block;height:100%;width:0;background:var(--blue);transition:width .3s}
.bar.indef i{width:30%;animation:slide 1.2s infinite ease-in-out}
@keyframes slide{0%{margin-left:-30%}100%{margin-left:100%}}
.log{color:var(--muted);font-size:12.5px;white-space:pre-wrap;max-height:130px;overflow:auto}
.big{font-size:34px;font-weight:700}.muted{color:var(--muted)}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{text-align:left;padding:8px 6px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:500;font-size:12.5px}th.num{text-align:right}td.num{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
td .path{color:var(--muted);font-size:12px;word-break:break-all}
.note{border-left:3px solid var(--warn);background:var(--panel);padding:10px 14px;border-radius:4px;margin:10px 0}
.note.bad{border-color:var(--bad)}.note ul{margin:6px 0 0;padding-left:18px;color:var(--muted);font-size:13px;word-break:break-all}
.err{color:var(--bad)}.okc{color:var(--ok)}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.back{margin-bottom:6px}
@media (max-width:600px){h1{font-size:24px}.big{font-size:28px}}
</style></head>
<body>
<header><span class="logo mono"><b>_</b>freighter</span><span class="ver">{{version}} · preview</span><span class="sp"></span>
<button id="quit" title="Stop Freighter">Quit</button></header>
<main id="view"></main>
<script>
"use strict";
const TOKEN = new URLSearchParams(location.search).get("t") || "";
const $view = document.getElementById("view");

function el(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "onclick") e.addEventListener("click", v);
    else if (k === "class") e.className = v;
    else if (v !== false && v != null) e.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat()) if (k != null && k !== false) e.append(k instanceof Node ? k : String(k));
  return e;
}
function plural(n, one, many) { return `${Number(n).toLocaleString()} ${n === 1 ? one : (many || one + "s")}`; }
function size(n) {
  const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0; n = Number(n) || 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return (i ? n.toFixed(1) : n.toFixed(0)) + " " + u[i];
}
async function api(path, body) {
  const r = await fetch(path, {method: body ? "POST" : "GET", cache: "no-store",
    headers: {"X-Freighter-Token": TOKEN, "Content-Type": "application/json"},
    body: body ? JSON.stringify(body) : undefined});
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || ("Error " + r.status));
  return data;
}
setInterval(() => api("/api/ping").catch(() => {}), 5000);
document.getElementById("quit").onclick = async () => {
  await api("/api/quit", {}).catch(() => {});
  document.body.replaceChildren(el("main", {}, el("h1", {}, "Freighter has stopped."),
    el("p", {class: "sub"}, "You can close this tab.")));
};

function show(...kids) { $view.replaceChildren(...kids); window.scrollTo(0, 0); }
function backButton() { return el("button", {class: "link back", onclick: home}, "← Back"); }

/* Runs a job, shows its progress, and calls render(result) when it's done. */
async function runJob(kind, title, intro, render) {
  const bar = el("div", {class: "bar indef"}, el("i"));
  const label = el("div", {class: "muted"}, "Starting…");
  const log = el("div", {class: "log mono"});
  const cancel = el("button", {}, "Cancel");
  const box = el("div", {class: "panel"}, label, bar, log, el("div", {class: "row"}, cancel));
  show(backButton(), el("h1", {}, title), el("p", {class: "sub"}, intro), box);
  let id;
  try { id = (await api("/api/jobs", {kind})).id; }
  catch (e) { label.replaceChildren(el("span", {class: "err"}, e.message)); cancel.remove(); return; }
  cancel.onclick = () => { cancel.disabled = true; cancel.textContent = "Stopping…"; api(`/api/jobs/${id}/cancel`, {}); };
  while (true) {
    await new Promise(r => setTimeout(r, 400));
    let j;
    try { j = await api(`/api/jobs/${id}`); } catch (e) { label.textContent = "Lost contact with Freighter. Is its window still open?"; return; }
    if (j.progress) {
      const p = j.progress, pct = p.total ? Math.min(100, 100 * p.done / p.total) : 0;
      bar.classList.toggle("indef", !p.total);
      bar.firstChild.style.width = pct.toFixed(1) + "%";
      label.textContent = p.label.startsWith("Measuring") ? p.label + "…" : `${p.label}: ${pct.toFixed(0)}%`;
    } else if (j.log.length) label.textContent = j.log[j.log.length - 1];
    log.textContent = j.log.join("\n");
    if (j.state === "running") continue;
    if (j.state === "done") return render(j.result, id);
    box.replaceChildren(el("p", {class: j.state === "cancelled" ? "muted" : "err"},
      j.state === "cancelled" ? "Stopped. Nothing was changed." : j.error || "Something went wrong."),
      j.crash ? el("p", {class: "muted"}, "Details were saved in " + j.crash + ". Please send that file with a bug report.") : null,
      el("div", {class: "row"}, el("button", {onclick: () => runJob(kind, title, intro, render)}, "Try again")));
    return;
  }
}

function home() {
  const card = (name, text, action) => el("div", {class: "card"},
    el("h3", {}, el("b", {}, "_"), name), el("p", {}, text), action);
  const soon = (cmd) => el("div", {class: "soon"}, "Coming to this window soon. For now: ", el("span", {class: "mono"}, cmd));
  show(
    el("h1", {}, "Move your studio to a new computer."),
    el("p", {class: "sub"}, "Projects, presets and every sample your projects use. Freighter only reads your files here, and nothing leaves your computer."),
    el("div", {class: "cards"},
      card("old computer", "See what a move would take: your FL Studio and Ableton folders, projects, and every sample folder they use.",
        el("div", {class: "row"}, el("button", {class: "primary", onclick: moveList}, "See what would move"))),
      card("new computer", "Unpack a bundle from the drive, put everything where your DAW expects it, and repair projects.",
        soon("freighter menu → 5 and 6")),
      card("manifest", "Scan this computer's plugins: what's installed, by which maker, and anything that looks wrong.",
        el("div", {class: "row"}, el("button", {class: "primary", onclick: plugins}, "Scan my plugins"))),
      card("crate", "Sort drum kits and sample packs into one library by genre, pack and sound type.",
        soon("freighter menu → 7"))
    )
  );
}

function moveList() {
  runJob("move_list", "What a move would take", "Looking through your music folders and every project for the files they use…", (r) => {
    const rows = r.items.map(it => el("tr", {},
      el("td", {}, it.label, el("div", {class: "path mono"}, it.path)),
      el("td", {class: "num"}, it.files.toLocaleString()),
      el("td", {class: "num"}, size(it.bytes))));
    const notes = [];
    if (r.missing_count) notes.push(el("div", {class: "note bad"},
      el("b", {}, `${plural(r.missing_count, "sample")} used by ${plural(r.missing_projects, "project")} ${r.missing_count === 1 ? "is" : "are"} already missing on this computer`),
      el("div", {class: "muted"}, "They can't be moved. If they're on a drive that isn't plugged in, plug it in and look again."),
      el("ul", {}, r.missing.slice(0, 30).map(m => el("li", {class: "mono"}, m))),
      r.missing_count > 30 ? el("div", {class: "muted"}, `… and ${r.missing_count - 30} more`) : null));
    if (r.cloud.files) notes.push(el("div", {class: "note"},
      el("b", {}, `${plural(r.cloud.files, "file")} (${size(r.cloud.bytes)}) only ${r.cloud.files === 1 ? "lives" : "live"} in OneDrive's cloud`),
      el("div", {class: "muted"}, "Packing downloads them first, so stay online. To speed it up, right-click the folder in File Explorer and choose \"Always keep on this device\".")));
    if (r.installed) notes.push(el("div", {class: "note"},
      el("b", {}, `${plural(r.installed, "sample")} ${r.installed === 1 ? "comes" : "come"} with installed programs or plugins`),
      el("div", {class: "muted"}, "For example Ableton's Core Library or FL Studio's own sounds. They aren't moved: installing those programs on the new computer brings them back.")));
    if (r.left_behind) notes.push(el("div", {class: "note"},
      el("b", {}, `${plural(r.left_behind, "program or installer", "programs or installers")} will be left behind`),
      el("div", {class: "muted"}, "Reinstall software from its maker. Installers tucked into sample folders are a common way malware spreads.")));
    show(backButton(), el("h1", {}, "What a move would take"),
      el("div", {class: "panel row"}, el("div", {}, el("div", {class: "big"}, size(r.total)),
        el("div", {class: "muted"}, `${plural(r.items.length, "folder")} to move. Your drive needs at least this much free space.`))),
      ...notes,
      el("h2", {}, "What would move"),
      el("table", {}, el("thead", {}, el("tr", {}, el("th", {}, "What"), el("th", {class: "num"}, "Files"), el("th", {class: "num"}, "Size"))),
        el("tbody", {}, rows)),
      el("p", {class: "muted"}, "Packing onto a drive is coming to this window next. For now use ",
        el("span", {class: "mono"}, "freighter menu"), ", option 4."));
  });
}

function plugins() {
  runJob("plugins", "Your plugins", "Reading plugin information files. Plugins are never loaded or run.", (r, id) => {
    const makers = r.makers.map(([m, n]) => el("tr", {}, el("td", {}, m), el("td", {class: "num"}, n)));
    const flagged = r.flagged.map(f => el("tr", {}, el("td", {}, f.name, el("div", {class: "path"}, `${f.vendor} · ${f.format}`)),
      el("td", {}, f.flags.split(" | ").map(x => el("div", {}, x)))));
    show(backButton(), el("h1", {}, "Your plugins"),
      el("div", {class: "panel row"},
        el("div", {style: "flex:1"}, el("div", {class: "big"}, r.plugins.toLocaleString()),
          el("div", {class: "muted"}, `${r.plugins === 1 ? "plugin" : "plugins"} from ${plural(r.maker_count, "maker")} (${plural(r.count, "file")} across formats)`)),
        el("button", {class: "primary", onclick: () => api(`/api/jobs/${id}/open`, {file: "report"})}, "Open the full report")),
      r.flagged.length ? el("div", {class: "note bad"}, el("b", {}, `${plural(r.flagged.length, "plugin")} ${r.flagged.length === 1 ? "needs" : "need"} a look`),
        el("div", {class: "muted"}, "Broken signatures, old 32-bit or Intel-only versions, or duplicates. Details are in the full report.")) :
        el("div", {class: "note"}, el("b", {class: "okc"}, "Nothing looks wrong.")),
      r.flagged.length ? el("table", {}, el("thead", {}, el("tr", {}, el("th", {}, "Plugin"), el("th", {}, "What's wrong"))), el("tbody", {}, flagged)) : null,
      el("h2", {}, "By maker"),
      el("table", {}, el("thead", {}, el("tr", {}, el("th", {}, "Maker"), el("th", {class: "num"}, "Plugins"))), el("tbody", {}, makers)));
  });
}

home();
</script>
</body></html>
"""


if __name__ == "__main__":
    sys.exit(run())
