"""The page the Freighter app shows in the browser (see app.py)."""

LOCKED_PAGE = """<!doctype html><meta charset=utf-8><title>Freighter</title>
<body style="font:16px/1.5 -apple-system,'Segoe UI',sans-serif;background:#0f1114;color:#eef0f2;padding:40px">
<p>This page only opens from Freighter's own window. Start Freighter again and use the address it shows.</p>"""

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Freighter</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='7' fill='%23171a1f'/%3E%3Crect x='6' y='20' width='20' height='5' rx='2.5' fill='%233d8bff'/%3E%3C/svg%3E">
<style>
:root{--bg:#0f1114;--panel:#171a1f;--panel2:#1d2127;--line:#272c33;--ink:#eef0f2;--muted:#9aa1ab;--blue:#3d8bff;
--blue2:#2f6fd6;--warn:#f0b44b;--bad:#ff6b6b;--ok:#4cc38a}
*{box-sizing:border-box}html,body{margin:0;background:var(--bg);color:var(--ink)}
body{font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
header{display:flex;align-items:center;gap:12px;padding:14px 20px;border-bottom:1px solid var(--line)}
.logo{font-weight:700;letter-spacing:.2px}.logo b{color:var(--blue)}
.ver{color:var(--muted);font-size:12px}header .sp{flex:1}
a.hdr{font-size:13px;color:var(--muted);text-decoration:none}a.hdr:hover{color:var(--ink)}
a.btn{display:inline-block;border-radius:7px;padding:8px 14px;background:var(--blue);color:#fff;font-weight:600;text-decoration:none}
a.btn:hover{background:var(--blue2)}
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
.small{font-size:12.5px}.warnc{color:var(--warn)}
.steps{display:flex;gap:6px;list-style:none;padding:0;margin:4px 0 14px;flex-wrap:wrap;counter-reset:s}
.steps li{counter-increment:s;font-size:12.5px;color:var(--muted);padding:4px 10px;border:1px solid var(--line);border-radius:20px}
.steps li::before{content:counter(s) ". "}.steps li.now{color:var(--ink);border-color:var(--blue)}.steps li.done{color:var(--ok)}
.choices{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:10px}
.choice{text-align:left;display:flex;flex-direction:column;gap:3px;padding:14px;background:var(--panel)}
.choice:hover{border-color:var(--blue)}.choice.dim{opacity:.6}.choice-title{font-weight:600}
select{font:inherit;background:var(--panel2);color:var(--ink);border:1px solid var(--line);border-radius:7px;padding:5px 6px}
input[type=text]{font:inherit;background:var(--panel2);color:var(--ink);border:1px solid var(--line);border-radius:7px;padding:8px 10px;flex:1;min-width:220px}
input[type=checkbox],input[type=radio]{accent-color:var(--blue);width:16px;height:16px;vertical-align:-3px}
td.tick{width:28px}.actions{margin-top:18px}button.small{padding:3px 10px;font-size:12.5px}
label.radio{display:block;margin:8px 0}.note details,.plugin details{margin-top:6px}summary{cursor:pointer;color:var(--muted)}
ul.plain{list-style:none;padding:0;margin:8px 0}ul.plain>li{padding:10px 0;border-bottom:1px solid var(--line)}
ul.checks{list-style:none;padding:0}ul.checks li{padding:4px 0}a{color:var(--blue)}
ul.plain>li>.row{margin-top:6px}.stage{font-weight:600}.stage:empty{display:none}
.choice,.mono.small{min-width:0;overflow-wrap:anywhere}
@media (max-width:600px){h1{font-size:24px}.big{font-size:28px}}
</style></head>
<body>
<header><span class="logo mono"><b>_</b>freighter</span><span class="ver">{{version}}</span><span class="sp"></span>
<a class="hdr" href="{{report}}" target="_blank" rel="noopener noreferrer" title="Opens a short form on GitHub">Report a problem</a>
<button id="quit" title="Stop Freighter">Quit</button></header>
<main id="view"></main>
<script>
"use strict";
const TOKEN = new URLSearchParams(location.search).get("t") || "";
const $view = document.getElementById("view");

/* ---------- small helpers ---------- */
function el(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "onclick") e.addEventListener("click", v);
    else if (k === "onchange") e.addEventListener("change", v);
    else if (k === "oninput") e.addEventListener("input", v);
    else if (k === "class") e.className = v;
    else if (k === "checked" || k === "disabled" || k === "value") e[k] = v;
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
function baseName(p) { return String(p).split(/[\\/]/).filter(Boolean).pop() || p; }   // "E:\\" stays "E:"
function timeLeft(s) {   // a rough guess, so rounded: "about 12 minutes"
  if (s < 60) return `about ${Math.max(5, Math.round(s / 5) * 5)} seconds`;
  const m = Math.round(s / 60);
  if (m < 60) return `about ${plural(m, "minute")}`;
  const h = Math.floor(m / 60), r = m % 60;
  return `about ${plural(h, "hour")}` + (r ? ` ${plural(r, "minute")}` : "");
}
function when(iso) { try { return new Date(iso).toLocaleString([], {dateStyle: "medium", timeStyle: "short"}); } catch (e) { return iso; } }
async function api(path, body) {
  const r = await fetch(path, {method: body ? "POST" : "GET", cache: "no-store",
    headers: {"X-Freighter-Token": TOKEN, "Content-Type": "application/json"},
    body: body ? JSON.stringify(body) : undefined});
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || ("Error " + r.status));
  return data;
}
const store = {   // per-viewer conveniences only (ticked plugins); never needed for anything to work
  get(k, d) { try { return JSON.parse(localStorage.getItem("freighter." + k)) ?? d; } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem("freighter." + k, JSON.stringify(v)); } catch (e) {} },
};
setInterval(() => api("/api/ping").catch(() => {}), 5000);
document.getElementById("quit").onclick = async () => {
  await api("/api/quit", {}).catch(() => {});
  document.body.replaceChildren(el("main", {}, el("h1", {}, "Freighter has stopped."),
    el("p", {class: "sub"}, "You can close this tab.")));
};

function show(...kids) {   // optional pieces come in as null/false: leave them out (replaceChildren would print "null")
  $view.replaceChildren(...kids.flat().filter(k => k != null && k !== false)); window.scrollTo(0, 0);
}
function backTo(fn, label) { return el("button", {class: "link back", onclick: fn}, "← " + (label || "Back")); }
function steps(names, at) {
  return el("ol", {class: "steps"}, names.map((n, i) => el("li", {class: i < at ? "done" : i === at ? "now" : ""}, n)));
}
function note(kind, title, ...body) { return el("div", {class: "note " + (kind || "")}, el("b", {}, title), ...body); }
function copyButton(text) {
  const b = el("button", {class: "small"}, "Copy");
  b.onclick = async () => { try { await navigator.clipboard.writeText(text); b.textContent = "Copied"; } catch (e) { b.textContent = "Select and copy it"; } };
  return b;
}

/* Runs a job, shows its progress, and calls render(result) when it's done. */
async function runJob(kind, params, head, render, opts) {
  opts = opts || {};
  const bar = el("div", {class: "bar indef"}, el("i"));
  const stage = el("div", {class: "stage"});   // "Step 2 of 4: …" for commands with stages (the rehearsal)
  const label = el("div", {class: "muted"}, "Starting…");
  const log = el("div", {class: "log mono"});
  const cancel = el("button", {}, "Cancel");
  const box = el("div", {class: "panel"}, stage, label, bar, log, opts.noCancel ? null : el("div", {class: "row"}, cancel));
  show(...head, box);
  let id;
  try { id = (await api("/api/jobs", Object.assign({kind}, params || {}))).id; }
  catch (e) { label.replaceChildren(el("span", {class: "err"}, e.message)); cancel.remove(); return; }
  cancel.onclick = () => { cancel.disabled = true; cancel.textContent = "Stopping…"; api(`/api/jobs/${id}/cancel`, {}); };
  while (true) {
    await new Promise(r => setTimeout(r, 400));
    let j;
    try { j = await api(`/api/jobs/${id}`); } catch (e) { label.textContent = "Lost contact with Freighter. Is its window still open?"; return; }
    stage.textContent = j.stage ? j.stage.replace(/^(\d+)\/(\d+)\s+/, "Step $1 of $2: ") : "";
    if (j.progress) {
      const p = j.progress, pct = p.total ? Math.min(100, 100 * p.done / p.total) : 0;
      bar.classList.toggle("indef", !p.total);
      bar.firstChild.style.width = pct.toFixed(1) + "%";
      label.textContent = !p.total ? p.label + "…" :
        (p.label === "Copying" || p.label === "Checking" ? `${p.label}: ${pct.toFixed(0)}% (${size(p.done)} of ${size(p.total)}` +
          (p.files_total ? `, ${p.files.toLocaleString()} of ${plural(p.files_total, "file")}` : "") + ")" +
          (p.eta != null && p.done < p.total ? ` · ${timeLeft(p.eta)} left` : "") : `${p.label}: ${pct.toFixed(0)}%`);
    } else if (j.log.length) label.textContent = j.log[j.log.length - 1];
    log.textContent = j.log.join("\n");
    if (j.state === "running") continue;
    if (j.state === "done") return render(j.result, id);
    box.replaceChildren(...[el("p", {class: j.state === "cancelled" ? "muted" : "err"},
      j.state === "cancelled" ? (opts.cancelled || "Stopped. Nothing was changed.") : j.error || "Something went wrong."),
      j.crash ? el("p", {class: "muted"}, "Details were saved in ", el("span", {class: "mono"}, j.crash),
        ". Please report it, and drag that file into the form.") : null,
      j.crash ? el("div", {class: "row"},
        el("a", {class: "btn", href: document.querySelector("a.hdr").href, target: "_blank", rel: "noopener noreferrer"}, "Report this problem"),
        el("button", {onclick: () => api(`/api/jobs/${id}/open`, {file: "crash"}).catch(() => {})}, "Show the file")) : null,
      el("div", {class: "row"}, el("button", {onclick: () => runJob(kind, params, head, render, opts)}, "Try again"),
        el("button", {onclick: home}, "Home"))].filter(Boolean));
    return;
  }
}

/* ---------- home ---------- */
function home() {
  const card = (name, text, ...actions) => el("div", {class: "card"},
    el("h3", {}, el("b", {}, "_"), name), el("p", {}, text), el("div", {class: "row"}, ...actions));
  const here = el("div");
  api("/api/home").then(r => here.replaceChildren(...onThisComputer(r))).catch(() => {});
  show(
    el("h1", {}, "Move your studio to a new computer."),
    el("p", {class: "sub"}, "Projects, presets and every sample your projects use. Freighter never changes your original files, and nothing leaves your computer."),
    el("div", {class: "cards"},
      card("old computer", "Pack your FL Studio and Ableton folders, projects and the samples they use onto a drive. Or rehearse the whole move here first.",
        el("button", {class: "primary", onclick: () => moveList("pack")}, "Start packing"),
        el("button", {onclick: () => moveList("rehearse")}, "Rehearse first")),
      card("new computer", "Unpack a bundle from the drive, put everything where it belongs, and repair your projects.",
        el("button", {class: "primary", onclick: pickBundle}, "Start unpacking")),
      card("plugins", "See which plugins your projects need, and which to install first on this computer.",
        el("button", {class: "primary", onclick: readiness}, "Check my plugins"),
        el("button", {onclick: pluginReport}, "Full plugin report")),
      card("crate", "Sort drum kits and sample packs into one library by genre, pack and sound type. Takes no extra space.",
        el("button", {class: "primary", onclick: crateStart}, "Sort my samples"))
    ),
    here,
    updateCheck()
  );
}

/* Never automatic: Freighter only asks GitHub when someone clicks (issue #40). */
function updateCheck() {
  const out = el("span", {});
  const b = el("button", {class: "link"}, "Check for updates");
  b.onclick = async () => {
    b.disabled = true; out.textContent = " Asking GitHub…";
    try {
      const r = await api("/api/update");
      out.replaceChildren(r.error ? el("span", {class: "err"}, " " + r.error)
        : r.newer ? el("span", {}, ` Freighter ${r.latest} is out. `, el("a", {href: r.url, target: "_blank", rel: "noopener noreferrer"}, "Download it"))
        : el("span", {class: "muted"}, !r.latest ? " No releases yet." : r.release_build ? ` You have the newest: ${r.latest}.` : ` The newest release is ${r.latest}.`));
    } catch (e) { out.textContent = " " + e.message; }
    b.disabled = false;
  };
  return el("p", {class: "muted small", style: "margin-top:36px"}, b, out,
    el("span", {class: "muted"}, " · only contacts GitHub when you click"));
}

/* Unpacks that can still be repaired or undone, and rehearsals still taking up space: they outlast the app. */
function onThisComputer(r) {
  if (!r.unpacks.length && !r.rehearsals.length) return [];
  return [el("h2", {}, "On this computer"), el("ul", {class: "plain"},
    r.unpacks.map(u => el("li", {},
      el("b", {}, `Unpacked from ${u.host}`), el("span", {class: "muted"}, ` · ${when(u.when)} · ${plural(u.files, "file")}`),
      el("div", {class: "row"}, el("button", {class: "small", onclick: repairAgain}, "Repair projects again"),
        el("button", {class: "small", onclick: () => undoUnpack(u.host)}, "Undo this unpack")))),
    r.rehearsals.map(f => el("li", {},
      el("b", {}, "Rehearsal files"), el("span", {class: "muted"}, " · copies from a practice move"),
      el("div", {class: "muted mono small"}, f),
      el("div", {class: "row"}, el("button", {class: "small", onclick: () => deleteRehearsal(f)}, "Delete them")))))];
}

/* ---------- old computer: 1 what moves, 2 pick a drive, 3 pack ---------- */
const OLD_STEPS = ["What moves", "Pick a drive", "Pack"];
const REH_STEPS = ["What moves", "Where", "Rehearse"];
let move = null;   // {items, total, skip:Set, mode:"pack"|"rehearse", ...}

function moveList(mode, range) {   // range: {since: "2024-03", until: "2026-03"} for a time-range move (issue #76)
  const reh = mode === "rehearse";
  runJob("move_list", range || {}, [backTo(home), steps(reh ? REH_STEPS : OLD_STEPS, 0), el("h1", {}, reh ? "Rehearse a move" : "What a move would take"),
    el("p", {class: "sub"}, range ? "Looking for the projects saved in that time, and the kits they use…" : "Looking through your music folders and every project for the files they use…")], (r) => {
    move = Object.assign(r, {skip: new Set(), include: new Set(), mode: reh ? "rehearse" : "pack", rangeParams: range || null});
    chooseItems();
  });
}

function rangeText(label) { return /^(since|until)/.test(label) ? label : "between " + label.replace(" – ", " and "); }

function rangePanel(r) {
  const now = new Date(), lastYear = now.getFullYear();
  const years = (r.projects && r.projects.years && r.projects.years.length) ? r.projects.years : [lastYear, lastYear];
  const firstYear = Math.min(years[0], lastYear);
  const cur = r.rangeParams || {};
  const pick = (t, fallback) => t ? [Number(t.slice(0, 4)), Number(t.slice(5, 7))] : fallback;
  const [fy, fm] = pick(cur.since, [Math.max(firstYear, lastYear - 2), now.getMonth() + 1]);
  const [ty, tm] = pick(cur.until, [lastYear, now.getMonth() + 1]);
  const names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const monthSel = (v) => el("select", {"aria-label": "Month"}, names.map((m, i) => el("option", {value: i + 1, selected: i + 1 === v}, m)));
  const yearList = []; for (let y = Math.min(firstYear, fy); y <= Math.max(lastYear, ty); y++) yearList.push(y);
  const yearSel = (v) => el("select", {"aria-label": "Year"}, yearList.map(y => el("option", {value: y, selected: y === v}, y)));
  const fromM = monthSel(fm), fromY = yearSel(fy), toM = monthSel(tm), toY = yearSel(ty);
  const all = el("input", {type: "radio", name: "which", checked: !r.rangeParams});
  const some = el("input", {type: "radio", name: "which", checked: !!r.rangeParams});
  [fromM, fromY, toM, toY].forEach(x => x.addEventListener("change", () => { some.checked = true; }));
  const msg = el("span", {class: "err small"});
  const pad = (n) => String(n).padStart(2, "0");
  const go = el("button", {onclick: () => {
    if (all.checked) return moveList(r.mode, null);
    const since = `${fromY.value}-${pad(fromM.value)}`, until = `${toY.value}-${pad(toM.value)}`;
    if (until < since) { msg.textContent = "The end comes before the start."; return; }
    moveList(r.mode, {since, until});
  }}, "Show this move");
  return el("div", {class: "panel"}, el("b", {}, "Which projects?"),
    el("label", {class: "radio"}, all, " All of them"),
    el("label", {class: "radio"}, some, " Only projects last saved from ", fromM, " ", fromY, " to ", toM, " ", toY),
    el("div", {class: "row"}, go, msg),
    r.range ? el("p", {class: "muted", style: "margin:10px 0 0"},
      `${r.projects.in_range} of ${plural(r.projects.total, "project")} ${r.projects.in_range === 1 ? "was" : "were"} saved ${rangeText(r.range.label)}. ` +
      "The others stay on this computer. Each sample these projects use brings its whole kit, never the folder of other kits above it.") : null);
}

function optional() { return (move.big || []).concat(move.offered || [], move.libraries || []); }   // left out unless ticked
function inside(path, folder) {   // same folder, or inside it ("\\" on Windows, "/" on a Mac)
  return path === folder || path.startsWith(folder.replace(/[\\/]$/, "") + (folder.includes("\\") ? "\\" : "/"));
}
function chosenBytes() {   // what's ticked: the items, plus any big plugin content or library the person chose to take
  const taken = optional().filter(b => move.include.has(b.path));
  return move.items.filter(it => !move.skip.has(it.id) && !taken.some(b => inside(it.path, b.path)))   // a ticked library holds its kits
      .reduce((a, it) => a + it.bytes, 0) + taken.reduce((a, b) => a + b.bytes, 0);
}

function chooseItems() {
  const r = move;
  const totalEl = el("div", {class: "big"});
  const countEl = el("div", {class: "muted"});
  const reh = r.mode === "rehearse";
  const next = el("button", {class: "primary", onclick: reh ? rehearseWhere : pickDrive}, reh ? "Next: where to rehearse" : "Next: pick a drive");
  const refresh = () => {
    const chosen = r.items.filter(it => !r.skip.has(it.id)).length + r.include.size;
    totalEl.textContent = size(chosenBytes());
    countEl.textContent = `${plural(chosen, "folder")} of ${r.items.length + optional().length} chosen. ` +
      (reh ? "A rehearsal needs about twice this much free space." : "Your drive needs at least this much free space.");
    next.disabled = !chosen;
  };
  const rows = r.items.map(it => {
    const box = el("input", {type: "checkbox", checked: !r.skip.has(it.id),
      onchange: (e) => { e.target.checked ? r.skip.delete(it.id) : r.skip.add(it.id); refresh(); }});
    return el("tr", {}, el("td", {class: "tick"}, box),
      el("td", {}, el("label", {}, it.label), el("div", {class: "path mono"}, it.path)),
      el("td", {class: "num"}, it.files.toLocaleString()), el("td", {class: "num"}, size(it.bytes)));
  });
  const optRow = (b, what) => el("tr", {},
    el("td", {class: "tick"}, el("input", {type: "checkbox", checked: r.include.has(b.path),
      onchange: (e) => { e.target.checked ? r.include.add(b.path) : r.include.delete(b.path); refresh(); }})),
    el("td", {}, el("label", {}, what), el("div", {class: "path mono"}, b.path)),
    el("td", {class: "num"}, ""), el("td", {class: "num"}, size(b.bytes)));
  const bigRows = (r.big || []).map(b => optRow(b, `Plugin content: ${b.label}`));
  const libRows = (r.offered || []).map(b => optRow(b, b.label.replace(/^FL Studio browser folder: /, "Sample library: ")));
  const soundRows = (r.libraries || []).map(b => optRow(b, `${b.name} (${b.maker})`));
  const notes = [];
  if (r.missing_count) notes.push(note("bad",
    `${plural(r.missing_count, "sample")} used by ${plural(r.missing_projects, "project")} ${r.missing_count === 1 ? "is" : "are"} already missing on this computer`,
    el("div", {class: "muted"}, "They can't be moved. If they're on a drive that isn't plugged in, plug it in and start again."),
    el("details", {}, el("summary", {}, "Show them"), el("ul", {}, r.missing.slice(0, 50).map(m => el("li", {class: "mono"}, m))))));
  if (r.cloud.files) notes.push(note("", `${plural(r.cloud.files, "file")} (${size(r.cloud.bytes)}) only ${r.cloud.files === 1 ? "lives" : "live"} in OneDrive's cloud`,
    el("div", {class: "muted"}, "Packing downloads them first, so stay online. To speed it up, right-click the folder in File Explorer and choose \"Always keep on this device\".")));
  if (r.installed) notes.push(note("", `${plural(r.installed, "sample")} ${r.installed === 1 ? "comes" : "come"} with installed programs`,
    el("div", {class: "muted"}, "For example FL Studio's or Ableton's own sounds. Installing those programs on the new computer brings them back.")));
  if (r.left_behind) notes.push(note("", `${plural(r.left_behind, "program or installer", "programs or installers")} will be left behind`,
    el("div", {class: "muted"}, "Reinstall software from its maker. Installers tucked into sample folders are a common way malware spreads.")));
  show(backTo(home), steps(reh ? REH_STEPS : OLD_STEPS, 0), el("h1", {}, reh ? "What the rehearsal moves" : "What a move would take"),
    el("div", {class: "panel"}, totalEl, countEl),
    rangePanel(r),
    reh ? null : el("p", {class: "muted small"}, "First time? ",
      el("button", {class: "link", onclick: () => { r.mode = "rehearse"; chooseItems(); }}, "Rehearse first"),
      ": a practice move on this computer that checks every sample, without changing your files."),
    ...notes,
    el("h2", {}, "What moves"),
    el("p", {class: "muted"}, "Untick anything you don't want to take. Your originals stay on this computer either way."),
    el("table", {}, el("thead", {}, el("tr", {}, el("th", {}), el("th", {}, "What"), el("th", {class: "num"}, "Files"), el("th", {class: "num"}, "Size"))),
      el("tbody", {}, rows)),
    bigRows.length ? el("h2", {}, "Big plugin content, left out unless you tick it") : null,
    bigRows.length ? el("p", {class: "muted"}, "These are probably sound libraries. They usually come back faster by installing them again from the maker's app (Native Access, Spitfire, Arturia…). Tick one to take it anyway.") : null,
    bigRows.length ? el("table", {}, el("tbody", {}, bigRows)) : null,
    soundRows.length ? el("h2", {}, "Sound libraries, left out unless you tick them") : null,
    soundRows.length ? el("p", {class: "muted"}, "Kontakt, Omnisphere, Spitfire and other libraries your plugins play. They can be big; most makers' apps can also install them again on the new computer. After the move, Freighter shows how to reconnect each one you take.") : null,
    soundRows.length ? el("table", {}, el("tbody", {}, soundRows)) : null,
    libRows.length ? el("h2", {}, "Whole sample libraries, left out with a time range") : null,
    libRows.length ? el("p", {class: "muted"}, "Only the kits your projects use come from these. Tick one to take all of it.") : null,
    libRows.length ? el("table", {}, el("tbody", {}, libRows)) : null,
    el("div", {class: "row actions"}, next));
  refresh();
}

async function pickDrive() {
  const need = chosenBytes();
  const list = el("div", {class: "choices"}, el("p", {class: "muted"}, "Looking for drives…"));
  const other = el("input", {type: "text", class: "mono", placeholder: "Or type a folder, e.g. E:\\ or /Volumes/My Drive"});
  const otherMsg = el("div", {class: "muted small"});
  show(backTo(chooseItems), steps(OLD_STEPS, 1), el("h1", {}, "Pick a drive"),
    el("p", {class: "sub"}, `Plug in the drive you'll carry to the new computer. It needs about ${size(need)} free.`),
    list,
    el("h2", {}, "Somewhere else"),
    el("div", {class: "row"}, other, el("button", {onclick: async () => {
      otherMsg.textContent = "";
      try {
        const f = await api("/api/folder", {path: other.value});
        if (!f.exists) { otherMsg.textContent = "That folder doesn't exist."; return; }
        confirmPack({name: baseName(f.path), path: f.path, free: f.free, fs: f.fs}, need);
      } catch (e) { otherMsg.textContent = e.message; }
    }}, "Use this folder")), otherMsg,
    el("div", {class: "row actions"}, el("button", {onclick: pickDrive}, "Look again")));
  let drives;
  try { drives = (await api("/api/drives")).drives; } catch (e) { list.replaceChildren(el("p", {class: "err"}, e.message)); return; }
  list.replaceChildren(...drives.map(d => {
    const warn = driveWarning(d, need);
    return el("button", {class: "choice" + (warn && warn.block ? " dim" : ""), onclick: () => confirmPack(d, need)},
      el("div", {class: "choice-title"}, d.name),
      el("div", {class: "muted"}, `${size(d.free)} free of ${size(d.total)}` + (d.fs ? ` · ${d.fs.toUpperCase()}` : "")),
      warn ? el("div", {class: warn.block ? "err small" : "warnc small"}, warn.text) : null);
  }));
  if (!drives.length) list.replaceChildren(el("p", {class: "muted"}, "No drives found. Plug one in and choose Look again."));
  else if (!drives.some(d => d.removable !== false)) list.prepend(el("p", {class: "muted", style: "grid-column:1/-1;margin:0"},
    "No external drive found yet. Plug in the drive you'll carry to the new computer, then choose Look again."));
}

function driveWarning(d, need) {
  const fs = (d.fs || "").toLowerCase();
  if (d.free < need) return {block: true, text: `Not enough room: needs ${size(need)}.`};
  if (["fat32", "fat", "msdos", "vfat"].includes(fs)) return {block: false, text: "FAT32 can't hold files over 4 GB. Freighter checks before copying; exFAT is better."};
  if (fs === "ntfs" && navigator.platform.startsWith("Mac")) return {block: true, text: "A Mac can only read NTFS drives. Format it as exFAT first."};
  if (d.removable === false) return {block: false, text: "This is the computer's own disk: you'd still need to copy the bundle to a drive."};
  return null;
}

function confirmPack(d, need) {
  const warn = driveWarning(d, need);
  show(backTo(pickDrive), steps(OLD_STEPS, 1), el("h1", {}, "Ready to pack"),
    el("div", {class: "panel"},
      el("div", {}, el("b", {}, size(need)), " from this computer onto ", el("b", {}, d.name)),
      el("div", {class: "muted mono small"}, d.path),
      warn ? el("div", {class: warn.block ? "err" : "warnc"}, warn.text) : null),
    el("p", {class: "muted"}, "Freighter only reads your files here and copies them into a new folder on the drive. You can stop at any time; starting again picks up where it stopped."),
    el("div", {class: "row actions"}, el("button", {class: "primary", onclick: () => pack(d)}, "Pack")));
}

function pack(d) {
  runJob("pack", Object.assign({to: d.path, skip: [...move.skip], include: [...move.include]}, move.rangeParams || {}),
    [steps(OLD_STEPS, 2), el("h1", {}, "Packing"), el("p", {class: "sub"}, `Copying onto ${d.name} and checking every file as it goes.`)],
    (r) => packed(r, d),
    {cancelled: "Stopped. Your files are safe; start packing again and it picks up where it stopped."});
}

function packed(r, d) {
  const notes = [];
  if (r.errors) notes.push(note("bad", `${plural(r.errors, "file")} couldn't be copied`,
    el("div", {class: "muted"}, "They're listed in pack-errors.txt on the drive. Pack again to retry; everything else is kept.")));
  if (r.missing) notes.push(note("", `${plural(r.missing, "sample")} ${r.missing === 1 ? "was" : "were"} already missing on this computer`,
    el("div", {class: "muted"}, "Listed in already-missing.txt on the drive.")));
  show(steps(OLD_STEPS, 3), el("h1", {}, r.errors ? "Packed, with problems" : "Packed"),
    el("div", {class: "panel"}, el("div", {class: "big"}, size(r.bytes)),
      el("div", {class: "muted"}, `${plural(r.files, "file")} in ${plural(r.items, "folder")}, each checked, on ${d.name}.`),
      r.range ? el("div", {class: "muted"}, `Projects saved ${rangeText(r.range)}: ${r.projects.in_range} of ${r.projects.total}. The others stay on this computer (listed in projects-left-out.txt on the drive).`) : null,
      el("div", {class: "muted mono small"}, r.bundle)),
    ...notes,
    el("h2", {}, "Check the drive"),
    el("p", {class: "muted"}, "Freighter reads every file back from the drive and compares it with the original, to catch a bad drive or cable while this computer still has your files. For the best check, eject the drive, plug it back in, then:"),
    el("div", {class: "row"}, el("button", {class: "primary", onclick: () => checkDrive(r.bundle, d)}, "Check the drive")),
    el("h2", {}, "Before you wipe or sell this computer"),
    el("p", {class: "muted"}, "Free up your plugin licenses so they can move with you:"),
    licenseList(),
    el("h2", {}, "Before you unplug the drive"),
    el("p", {class: "muted"}, navigator.platform.startsWith("Win")
      ? "Eject it first (the USB icon by the clock, then Eject), so every file is fully written."
      : "Eject it first (the eject button next to it in Finder), so every file is fully written."),
    el("h2", {}, "Next, on the new computer"),
    el("ol", {}, el("li", {}, "Install FL Studio or Ableton, and Freighter."),
      el("li", {}, "Plug in this drive, open Freighter and choose ", el("b", {}, "Start unpacking"), "."),
      el("li", {}, "Freighter repairs your projects and shows which plugins to install first.")),
    note("", "Keep this computer as it is for now",
      el("div", {class: "muted"}, "Don't wipe or sell it until your projects open and play on the new one. Your files here weren't changed.")),
    el("div", {class: "row actions"}, el("button", {onclick: home}, "Home")));
}

function checkDrive(bundle, d) {   // issue #77
  runJob("verify", {bundle}, [steps(OLD_STEPS, 3), el("h1", {}, "Checking the drive"),
      el("p", {class: "sub"}, "Reading every file back from the drive and comparing it with the original.")], (r) => {
    const ok = !r.bad_count;
    show(steps(OLD_STEPS, 3), el("h1", {class: ok ? "okc" : "err"}, ok ? "The drive is good" : `${plural(r.bad_count, "copy", "copies")} on the drive ${r.bad_count === 1 ? "was" : "were"} damaged`),
      el("div", {class: "panel"}, el("div", {class: "big"}, `${(r.files - r.bad_count).toLocaleString()} of ${r.files.toLocaleString()}`),
        el("div", {class: "muted"}, ok ? `files read back and match the originals (${size(r.bytes)}). It's safe to take this drive to the new computer.`
          : "files read back and match. The damaged copies were removed from the drive: pack again and Freighter copies just those, then check again.")),
      ok ? null : note("bad", "Damaged or missing", el("ul", {}, r.bad.map(b => el("li", {class: "mono"}, `${b.item}: ${b.file} (${b.problem})`))),
        el("div", {class: "muted"}, "If it keeps happening, try another USB port or cable, or another drive.")),
      el("div", {class: "row actions"}, ok ? null : (move ? el("button", {class: "primary", onclick: () => pack(d)}, "Pack again") : null),
        el("button", {onclick: home}, "Home")));
  }, {cancelled: "Stopped. Nothing on the drive was changed."});
}

function licenseList() {
  const items = ["iLok License Manager (any iLok plugins)", "Waves Central → Licenses", "Native Access (Native Instruments)",
    "Arturia Software Center", "Plugin Alliance Installation Manager", "Any other maker with an activation limit"];
  const done = store.get("licenses", {});
  return el("ul", {class: "checks"}, items.map(t => el("li", {}, el("label", {},
    el("input", {type: "checkbox", checked: !!done[t], onchange: (e) => { done[t] = e.target.checked; store.set("licenses", done); }}), " ", t))));
}

/* ---------- rehearsal: a whole practice move on this computer (the menu's option 3) ---------- */
async function rehearseWhere() {
  const need = 2 * chosenBytes();
  const list = el("div", {class: "choices"}, el("p", {class: "muted"}, "Checking free space…"));
  const other = el("input", {type: "text", class: "mono", placeholder: "Or type a drive or folder, e.g. E:\\ or /Volumes/My Drive"});
  const otherMsg = el("div", {class: "muted small"});
  show(backTo(chooseItems), steps(REH_STEPS, 1), el("h1", {}, "Where to rehearse"),
    el("p", {class: "sub"}, `A rehearsal needs about ${size(need)} free: a bundle plus an unpacked copy. You can delete it in one click afterwards.`),
    list,
    el("h2", {}, "Somewhere else"),
    el("div", {class: "row"}, other, el("button", {onclick: async () => {
      otherMsg.textContent = "";
      try {
        const f = await api("/api/folder", {path: other.value});
        if (!f.exists) { otherMsg.textContent = "That folder doesn't exist."; return; }
        confirmRehearse({name: baseName(f.path), to: f.path, path: f.path, free: f.free}, need);
      } catch (e) { otherMsg.textContent = e.message; }
    }}, "Use this folder")), otherMsg);
  let r;
  try { r = await api("/api/rehearsal"); } catch (e) { list.replaceChildren(el("p", {class: "err"}, e.message)); return; }
  const short = r.free < need;
  list.replaceChildren(el("button", {class: "choice" + (short ? " dim" : ""),
      onclick: () => confirmRehearse({name: "your Music folder", to: null, path: r.default, free: r.free}, need)},
    el("div", {class: "choice-title"}, "In your Music folder"),
    el("div", {class: "muted"}, `${size(r.free)} free`),
    short ? el("div", {class: "err small"}, `Not enough room: needs ${size(need)}. Use a bigger drive, or untick big folders.`) : null,
    el("div", {class: "muted mono small"}, r.default)));
}

function confirmRehearse(w, need) {
  const short = w.free < need;
  show(backTo(rehearseWhere), steps(REH_STEPS, 1), el("h1", {}, "Ready to rehearse"),
    el("div", {class: "panel"},
      el("div", {}, "A practice move of ", el("b", {}, size(need / 2)), " into ", el("b", {}, w.name)),
      el("div", {class: "muted mono small"}, w.path),
      w.to ? el("div", {class: "muted small"}, "Freighter makes a folder called “Freighter Rehearsal” there.") : null,
      short ? el("div", {class: "err"}, `Not enough room: needs about ${size(need)}, and ${size(w.free)} is free.`) : null),
    el("h2", {}, "What happens"),
    el("ol", {},
      el("li", {}, el("b", {}, "Pack: "), "copies everything into a bundle, as if onto your drive."),
      el("li", {}, el("b", {}, "Unpack: "), "into a pretend new computer next to it. Your real folders count as gone, so only the copies can pass."),
      el("li", {}, el("b", {}, "Repair: "), "points the copied projects at the copied samples."),
      el("li", {}, el("b", {}, "Check: "), "reads every copied project and compares each sample with the original, byte for byte.")),
    el("p", {class: "muted"}, "Your real files are only read, never changed. It takes about as long as packing twice, and you can stop at any time."),
    el("div", {class: "row actions"}, el("button", {class: "primary", disabled: short, onclick: () => rehearse(w)}, "Rehearse")));
}

function rehearse(w) {
  runJob("rehearse", Object.assign({to: w.to, skip: [...move.skip], include: [...move.include]}, move.rangeParams || {}),
    [steps(REH_STEPS, 2), el("h1", {}, "Rehearsing"),
      el("p", {class: "sub"}, "Packing, unpacking into a pretend new computer, repairing, then checking every sample. Your real files are only read.")],
    rehearsed, {cancelled: "Stopped. Your real files weren't touched. You can delete the rehearsal's files from the home screen."});
}

function rehearsed(r, id) {
  const t = r.totals, checked = t.kept + t.lost;
  const list = (items, total) => el("details", {}, el("summary", {}, "Show them"),
    el("ul", {}, items.map(x => el("li", {class: "mono"}, x))),
    total > items.length ? el("div", {class: "muted small"}, `…and ${(total - items.length).toLocaleString()} more in the report.`) : null);
  const parts = [steps(REH_STEPS, 3),
    el("h1", {class: r.passed ? "okc" : "err"}, r.passed ? "Passed: every sample survived the move" : `Failed: ${plural(t.lost, "sample")} lost in the move`),
    el("div", {class: "panel"}, el("div", {class: "big"}, `${t.kept.toLocaleString()} of ${checked.toLocaleString()}`),
      el("div", {class: "muted"}, `samples that worked before the move still work after it, checked byte for byte, across ${plural(t.projects, "project")}.`))];
  if (t.lost) parts.push(note("bad", `${plural(t.lost, "sample")} lost`,
    el("div", {class: "muted"}, "Please don't do your real move yet. ",
      el("a", {href: "https://github.com/chasubowl-creator/freighter/issues/new", target: "_blank", rel: "noopener"}, "Report a problem"),
      " and attach the report, so it can be fixed first."), list(r.lost, t.lost)));
  if (t.already) parts.push(note("", `${plural(t.already, "sample")} ${t.already === 1 ? "was" : "were"} already missing before the move`,
    el("div", {class: "muted"}, "Not something the move did. Fix them on this computer first, for example by plugging in the drive they were on."),
    list(r.already, t.already)));
  if (t.unchecked) parts.push(note("", `${plural(t.unchecked, "file")} couldn't be checked automatically`,
    el("div", {class: "muted"}, r.old_live === t.unchecked
      ? "They're Ableton sets and racks saved by Live 10 or older, which store sample paths in a way Freighter can't read yet. Most are demo files that came with sample packs. Open your own ones after the real move to check them."
      : "For example Ableton projects saved by Live 10 or older. Open them after the real move to check them."),
    list(r.unchecked, t.unchecked)));
  parts.push(el("div", {class: "row"}, el("button", {onclick: () => api(`/api/jobs/${id}/open`, {file: "report"}).catch(() => {})}, "Open the full report"),
      el("span", {class: "muted mono small"}, r.report)),
    el("h2", {}, "Clean up"),
    el("p", {class: "muted"}, "The rehearsal's files are copies, so deleting them is safe."),
    el("div", {class: "row"}, el("button", {onclick: () => deleteRehearsal(r.folder)}, "Delete the rehearsal files"),
      el("span", {class: "muted mono small"}, r.folder)),
    el("div", {class: "row actions"},
      r.passed ? el("button", {class: "primary", onclick: () => { move.mode = "pack"; chooseItems(); }}, "Pack for real") : null,
      el("button", {onclick: home}, "Home")));
  show(...parts);
}

function deleteRehearsal(folder) {
  if (!confirm(`Delete the rehearsal files in ${folder}? They're copies: your real files aren't touched.`)) return;
  runJob("rehearse_cleanup", {folder}, [el("h1", {}, "Deleting the rehearsal files")], (r) =>
    show(backTo(home), el("h1", {}, "Rehearsal files deleted"),
      el("div", {class: "panel"}, el("div", {class: "big"}, size(r.freed)), el("div", {class: "muted"}, "freed. Your real files weren't touched.")),
      el("div", {class: "row actions"}, el("button", {onclick: home}, "Home"))),
    {noCancel: true});
}

/* ---------- new computer: 1 pick bundle, 2 unpack and repair, 3 results ---------- */
const NEW_STEPS = ["Pick the bundle", "Unpack and repair", "Done"];

async function pickBundle() {
  const list = el("div", {class: "choices"}, el("p", {class: "muted"}, "Looking for Freighter bundles on your drives…"));
  const other = el("input", {type: "text", class: "mono", placeholder: "Or type where the bundle is, e.g. E:\\ or /Volumes/My Drive"});
  const otherMsg = el("div", {class: "muted small"});
  show(backTo(home), steps(NEW_STEPS, 0), el("h1", {}, "Pick the bundle"),
    el("p", {class: "sub"}, "Plug in the drive you packed onto. Freighter looks for Freighter-… folders on it."),
    list,
    el("h2", {}, "Somewhere else"),
    el("div", {class: "row"}, other, el("button", {onclick: async () => {
      otherMsg.textContent = "";
      try {
        const f = await api("/api/folder", {path: other.value});
        if (!f.exists) { otherMsg.textContent = "That folder doesn't exist."; return; }
        if (!f.bundles.length) { otherMsg.textContent = "No Freighter bundle there."; return; }
        f.bundles.length === 1 ? confirmUnpack(f.bundles[0]) : list.replaceChildren(...f.bundles.map(bundleChoice));
      } catch (e) { otherMsg.textContent = e.message; }
    }}, "Look there")), otherMsg,
    el("div", {class: "row actions"}, el("button", {onclick: pickBundle}, "Look again")));
  let drives;
  try { drives = (await api("/api/drives")).drives; } catch (e) { list.replaceChildren(el("p", {class: "err"}, e.message)); return; }
  const bundles = drives.flatMap(d => d.bundles);
  list.replaceChildren(...(bundles.length ? bundles.map(bundleChoice) :
    [el("p", {class: "muted"}, "No bundle found yet. Plug in the drive and choose Look again, or type where it is below.")]));
}

function bundleChoice(b) {
  return el("button", {class: "choice", onclick: () => confirmUnpack(b)},
    el("div", {class: "choice-title"}, `From ${b.host}`),
    el("div", {class: "muted"}, `Packed ${when(b.created)} · ${plural(b.items, "folder")}` + (b.bytes ? ` · ${size(b.bytes)}` : "")),
    b.range ? el("div", {class: "muted small"}, `Projects saved ${rangeText(b.range)}`) : null,
    b.problems.length ? el("div", {class: "err small"}, "Not finished packing") : el("div", {class: "okc small"}, "Complete"),
    el("div", {class: "muted mono small"}, b.path));
}

function confirmUnpack(b) {
  let together = false, anyway = false;
  const where = el("div", {}, el("p", {class: "muted"}, "Working out where everything goes…"));
  const preview = async () => {
    try {
      const r = await api("/api/unpack-preview", {bundle: b.path, keep_together: together});
      where.replaceChildren(el("table", {}, el("thead", {}, el("tr", {}, el("th", {}, "What"), el("th", {class: "num"}, "Size"), el("th", {}, "Goes to"))),
        el("tbody", {}, r.plan.map(x => el("tr", {}, el("td", {}, x.label), el("td", {class: "num"}, size(x.bytes)),
          el("td", {class: "mono path"}, x.dest))))));
    } catch (e) { where.replaceChildren(el("p", {class: "err"}, e.message)); }
  };
  const go = el("button", {class: "primary", disabled: !!b.problems.length, onclick: () => unpack(b, together, anyway)}, "Unpack and repair");
  const parts = [backTo(pickBundle), steps(NEW_STEPS, 0), el("h1", {}, `Unpack from ${b.host}`),
    b.range ? el("p", {class: "sub"}, `The projects saved ${rangeText(b.range)}, with the kits they use.`) : null,
    el("div", {class: "muted mono small"}, b.path)];
  if (b.problems.length) parts.push(note("bad", "This bundle isn't finished",
    el("ul", {}, b.problems.map(p => el("li", {}, p))),
    el("div", {class: "muted"}, "Best: on the old computer, start packing onto this drive again; it picks up where it stopped."),
    el("label", {}, el("input", {type: "checkbox", onchange: (e) => { anyway = e.target.checked; go.disabled = !anyway; }}),
      " The old computer is gone: unpack what's there anyway")));
  if (b.same_os) parts.push(el("div", {class: "panel"},
    el("b", {}, "Where should the folders go?"),
    el("label", {class: "radio"}, el("input", {type: "radio", name: "where", checked: true, onchange: () => { together = false; preview(); }}),
      " Back where they were on the old computer ", el("span", {class: "muted"}, "(recommended: your DAW and plugins find everything where they expect it)")),
    el("label", {class: "radio"}, el("input", {type: "radio", name: "where", onchange: () => { together = true; preview(); }}),
      " All together in Music/Freighter")));
  else parts.push(el("p", {class: "muted"}, "This bundle comes from a different kind of computer, so your FL Studio and Ableton folders go where this computer expects them and everything else into Music/Freighter, then your projects are repaired to match."));
  parts.push(el("h2", {}, "Where everything will go"), where,
    el("p", {class: "muted"}, "Nothing already on this computer is overwritten: a different file with the same name is kept, and the moved one saved next to it. You can undo the whole unpack afterwards."),
    el("div", {class: "row actions"}, go));
  show(...parts);
  preview();
}

function unpack(b, together, anyway) {
  runJob("unpack", {bundle: b.path, keep_together: together, anyway},
    [steps(NEW_STEPS, 1), el("h1", {}, "Unpacking"), el("p", {class: "sub"}, "Copying every file into place, checking it against the original, then repairing your projects.")],
    unpacked, {cancelled: "Stopped. Start unpacking again and it skips what's already in place."});
}

function repairSummary(rep) {
  const parts = [];
  if (rep.fl_open) parts.push(note("bad", "FL Studio is open, so FL projects weren't changed",
    el("div", {class: "muted"}, "Close FL Studio, then repair again."),
    el("div", {class: "row"}, el("button", {onclick: repairAgain}, "Repair again"))));
  parts.push(el("div", {class: "panel"},
    el("div", {class: "big"}, plural(rep.repaired, "project")),
    el("div", {class: "muted"}, `repaired: ${plural(rep.samples_fixed, "sample")} now ` +
      (rep.samples_fixed === 1 ? "points to its moved copy. " : "point to their moved copies. ") +
      `${plural(rep.in_place, "sample")} ${rep.in_place === 1 ? "was" : "were"} already in place. Originals are kept in "Freighter backup" folders.`)));
  if (rep.missing_count) parts.push(note("bad", `${plural(rep.missing_count, "sample")} still missing`,
    el("div", {class: "muted"}, "Usually samples that were already missing on the old computer."),
    el("details", {}, el("summary", {}, "Show them"), el("ul", {}, rep.missing.map(m => el("li", {class: "mono"}, m))))));
  if (rep.old_format.length) parts.push(note("", `${plural(rep.old_format.length, "Ableton project")} from Live 10 or older`,
    el("div", {class: "muted"}, "Freighter can't rewrite these yet. Open each in Live, then File → Manage Files → Locate, and point it at the moved samples.")));
  if (rep.damaged.length) parts.push(note("", `${plural(rep.damaged.length, "project")} looked damaged and ${rep.damaged.length === 1 ? "was" : "were"} left unchanged`,
    el("div", {class: "muted"}, "Often an autosave caught mid-save."),
    el("ul", {}, rep.damaged.map(m => el("li", {class: "mono"}, m)))));
  return parts;
}

function unpacked(r) {
  const u = r.unpack || {};
  const rows = (u.entries || []).map(e => el("tr", {}, el("td", {}, e.label, el("div", {class: "path mono"}, "from " + e.old)),
    el("td", {class: "mono path"}, e.new)));
  const parts = [steps(NEW_STEPS, 2), el("h1", {}, (u.problems || []).length || (u.bad || []).length ? "Unpacked, with problems" : "Everything is in place"),
    u.range ? el("p", {class: "sub"}, `The projects saved ${rangeText(u.range)}, with the kits they use.`) : null];
  if ((u.problems || []).length) parts.push(note("bad", "The bundle wasn't complete", el("ul", {}, u.problems.map(p => el("li", {}, p)))));
  if ((u.bad || []).length) parts.push(note("bad", `${plural(u.bad.length, "file")} had a problem`,
    u.blocked_hint ? el("div", {}, u.blocked_hint) : null, el("ul", {}, u.bad.map(p => el("li", {class: "mono"}, p)))));
  parts.push(...repairSummary(r.repair));
  if ((u.fl_search || []).length) parts.push(el("h2", {}, "Add these in FL Studio"),
    el("p", {class: "muted"}, "FL keeps its settings per computer. In FL Studio, open Options → File settings and add each folder under Browser extra search folders:"),
    el("ul", {class: "plain"}, u.fl_search.map(p => el("li", {class: "row"}, el("span", {class: "mono"}, p), copyButton(p)))));
  if ((u.plugin_data || []).length) parts.push(el("h2", {}, "Your plugin presets"),
    el("p", {class: "muted"}, u.cross_os
      ? "Plugins look for presets in their own folders on this computer. Open each plugin's preset browser and import them from here, or copy them into its preset folder:"
      : "They went back where they were, so your plugins find them once they're installed:"),
    el("ul", {class: "plain"}, u.plugin_data.map(p => el("li", {class: "row"},
      el("span", {}, p.label.replace(/^Plugin presets and data: /, "")), el("span", {class: "mono small muted"}, p.new),
      u.cross_os ? copyButton(p.new) : null))));
  const moved = (u.libraries || []).filter(l => l.moved), notMoved = (u.libraries || []).filter(l => !l.moved);
  if (moved.length) parts.push(el("h2", {}, "Reconnect your sound libraries"),
    el("p", {class: "muted"}, "Each maker's app needs to be told where a library is now. Install the maker's app first, then:"),
    el("ul", {class: "plain"}, moved.map(l => el("li", {},
      el("b", {}, l.name), el("span", {class: "muted"}, ` · ${l.maker}`),
      el("div", {class: "row"}, el("span", {class: "mono small muted"}, l.new), copyButton(l.new)),
      el("ol", {class: "small"}, l.steps.map(st => el("li", {}, st)))))));
  if (notMoved.length) parts.push(el("h2", {}, "Sound libraries to install again"),
    el("p", {class: "muted"}, "These stayed on the old computer. Install them from each maker's app:"),
    el("ul", {class: "plain"}, notMoved.map(l => el("li", {}, el("b", {}, l.name), el("span", {class: "muted"}, ` · ${l.maker} · ${l.steps[0]}`)))));
  (u.notes || []).forEach(n => parts.push(note("", "From the old computer", el("div", {class: "muted"}, n))));
  parts.push(el("h2", {}, "Where everything went"),
    u.back_in_place ? el("p", {class: "muted"}, "Same kind of computer, so your folders went back to the same places as on the old one.") : null,
    el("table", {}, el("thead", {}, el("tr", {}, el("th", {}, "What"), el("th", {}, "Now here"))), el("tbody", {}, rows)),
    el("p", {class: "muted small"}, "The full list is in ", el("span", {class: "mono"}, u.report || "unpack-report.txt"), "."),
    el("h2", {}, "Next: plugins"),
    el("p", {class: "muted"}, "Plugins can't be copied; they're installed from each maker. See which ones your projects need, most useful first."),
    el("div", {class: "row actions"}, el("button", {class: "primary", onclick: readiness}, "Check my plugins"), el("button", {onclick: home}, "Home")),
    el("h2", {}, "Changed your mind?"),
    el("p", {class: "muted"}, "Undo removes everything this unpack created and puts repaired projects back as they were. Files you've changed since are kept."),
    el("div", {class: "row"}, el("button", {onclick: () => undoUnpack(u.source_host)}, "Undo this unpack")));
  show(...parts);
}

function undoUnpack(host) {
  if (!confirm(`Remove everything the unpack from ${host} created, and put repaired projects back as they were?`)) return;
  runJob("undo", {host}, [el("h1", {}, "Undoing the unpack")], (r) =>
    show(backTo(home), el("h1", {}, "Undone"),
      el("div", {class: "panel"}, el("div", {class: "big"}, plural(r.removed, "file")),
        el("div", {class: "muted"}, `removed, and ${plural(r.restored, "project")} put back as ${r.restored === 1 ? "it was" : "they were"}.`)),
      r.kept.length ? note("", `Kept ${plural(r.kept.length, "file")} that changed since the unpack`,
        el("div", {class: "muted"}, "They're your work now, so Freighter left them alone."),
        el("details", {}, el("summary", {}, "Show them"), el("ul", {}, r.kept.map(k => el("li", {class: "mono"}, k))))) : null,
      el("div", {class: "row actions"}, el("button", {onclick: home}, "Home"))),
    {noCancel: true});
}

function repairAgain() {
  runJob("repair", {}, [backTo(home), el("h1", {}, "Repairing projects")], (rep) =>
    show(backTo(home), el("h1", {}, "Projects repaired"), ...repairSummary(rep),
      el("div", {class: "row actions"}, el("button", {class: "primary", onclick: readiness}, "Check my plugins"))));
}

/* ---------- plugins ---------- */
function readiness() {
  runJob("readiness", {}, [backTo(home), el("h1", {}, "Your plugins"),
    el("p", {class: "sub"}, "Reading which plugins each project uses, then checking what's installed here. Plugins are never loaded or run.")],
    (r) => {
      const ticked = store.get("installed", {});
      const left = r.total - r.ready;
      const rows = r.missing.map(m => el("li", {class: "plugin"},
        el("label", {}, el("input", {type: "checkbox", checked: !!ticked[m.name],
          onchange: (e) => { ticked[m.name] = e.target.checked; store.set("installed", ticked); }}),
          " ", el("b", {}, m.name), m.vendor ? el("span", {class: "muted"}, " by " + m.vendor) : null),
        el("div", {class: "muted small"}, `Used in ${plural(m.projects.length, "project")}` +
          (m.opens ? ` · installing it opens ${plural(m.opens, "more project")}` : "")),
        m.link ? el("a", {href: m.link, target: "_blank", rel: "noopener noreferrer", class: "small"}, "Get it from " + (m.vendor || "the maker"))
          : el("a", {href: m.search, target: "_blank", rel: "noopener noreferrer", class: "small"}, "Search the web for it"),
        el("details", {}, el("summary", {class: "small"}, "Which projects"), el("ul", {class: "small"}, m.projects.map(p => el("li", {}, p))))));
      show(backTo(home), el("h1", {}, "Your plugins"),
        el("div", {class: "panel"}, el("div", {class: "big"}, `${r.ready} of ${r.total}`),
          el("div", {class: "muted"}, left ? `projects have every plugin they use. Install the ones below, most useful first.` :
            "projects have every plugin they use. Nothing to install.")),
        r.missing.length ? el("ul", {class: "plain"}, rows) : null,
        r.versions.length ? el("h2", {}, "DAW versions") : null,
        ...r.versions.map(v => el("p", {}, `${v.daw} ${v.version} or newer `, el("span", {class: "muted"}, `(the newest that saved your projects, e.g. ${v.example})`))),
        ...r.warnings.map(w => note("", "Watch out", el("div", {class: "muted"}, w))),
        r.on_old_computer ? el("h2", {}, "Before you wipe this computer") : null,
        r.on_old_computer ? licenseList() : null,
        el("div", {class: "row actions"}, el("button", {class: "primary", onclick: readiness}, "Check again"),
          el("button", {onclick: pluginReport}, "Full plugin report")));
    });
}

function pluginReport() {
  runJob("plugins", {}, [backTo(home), el("h1", {}, "Plugin report"), el("p", {class: "sub"}, "Reading plugin information files. Plugins are never loaded or run.")], (r, id) => {
    const makers = r.makers.map(([m, n]) => el("tr", {}, el("td", {}, m), el("td", {class: "num"}, n)));
    const flagged = r.flagged.map(f => el("tr", {}, el("td", {}, f.name, el("div", {class: "path"}, `${f.vendor} · ${f.format}`)),
      el("td", {}, f.flags.split(" | ").map(x => el("div", {}, x)))));
    show(backTo(home), el("h1", {}, "Plugin report"),
      el("div", {class: "panel row"},
        el("div", {style: "flex:1"}, el("div", {class: "big"}, r.plugins.toLocaleString()),
          el("div", {class: "muted"}, `${r.plugins === 1 ? "plugin" : "plugins"} from ${plural(r.maker_count, "maker")} (${plural(r.count, "file")} across formats)`)),
        el("button", {class: "primary", onclick: () => api(`/api/jobs/${id}/open`, {file: "report"})}, "Open the full report")),
      r.flagged.length ? note("bad", `${plural(r.flagged.length, "plugin")} ${r.flagged.length === 1 ? "needs" : "need"} a look`,
        el("div", {class: "muted"}, "Broken signatures, old 32-bit or Intel-only versions, or duplicates. Details are in the full report.")) :
        note("", "Nothing looks wrong."),
      r.flagged.length ? el("table", {}, el("thead", {}, el("tr", {}, el("th", {}, "Plugin"), el("th", {}, "What's wrong"))), el("tbody", {}, flagged)) : null,
      el("h2", {}, "By maker"),
      el("table", {}, el("thead", {}, el("tr", {}, el("th", {}, "Maker"), el("th", {class: "num"}, "Plugins"))), el("tbody", {}, makers)));
  });
}

/* ---------- crate ---------- */
const crateState = {folders: [], by: "genre"};

async function crateStart() {
  const list = el("ul", {class: "plain"});
  const suggest = el("div", {class: "row"});
  const input = el("input", {type: "text", class: "mono", placeholder: "Type a sample folder, e.g. D:\\Samples or /Users/you/Desktop/DrumKits"});
  const msg = el("div", {class: "muted small"});
  const go = el("button", {class: "primary", onclick: cratePreview}, "Preview");
  const redraw = () => {
    list.replaceChildren(...crateState.folders.map(f => el("li", {class: "row"}, el("span", {class: "mono", style: "flex:1"}, f),
      el("button", {class: "small", onclick: () => { crateState.folders = crateState.folders.filter(x => x !== f); redraw(); }}, "Remove"))));
    if (!crateState.folders.length) list.replaceChildren(el("li", {class: "muted"}, "No folders yet."));
    go.disabled = !crateState.folders.length;
  };
  const add = (f) => { if (f && !crateState.folders.includes(f)) crateState.folders.push(f); redraw(); };
  show(backTo(home), el("h1", {}, "Sort your samples"),
    el("p", {class: "sub"}, "Crate builds one tidy library out of your drum kits and sample packs, sorted by genre, pack and sound. It's made of links, so it takes no extra space, and your original folders stay exactly as they are."),
    el("h2", {}, "Folders to sort"), list, suggest,
    el("div", {class: "row"}, input, el("button", {onclick: async () => {
      msg.textContent = "";
      try { const f = await api("/api/folder", {path: input.value}); f.exists ? (add(f.path), input.value = "") : msg.textContent = "That folder doesn't exist."; }
      catch (e) { msg.textContent = e.message; }
    }}, "Add")), msg,
    el("h2", {}, "Sort by"),
    el("label", {class: "radio"}, el("input", {type: "radio", name: "by", checked: crateState.by === "genre", onchange: () => { crateState.by = "genre"; }}),
      " Genre, then pack, then sound ", el("span", {class: "muted"}, "(House/UNDRGRND Deep Grooves/Drums/Kicks)")),
    el("label", {class: "radio"}, el("input", {type: "radio", name: "by", checked: crateState.by === "type", onchange: () => { crateState.by = "type"; }}),
      " Sound only, every pack mixed ", el("span", {class: "muted"}, "(Drums/Kicks)")),
    el("div", {class: "row actions"}, go));
  redraw();
  try {
    const s = await api("/api/crate-suggestions");
    const fresh = s.folders.filter(f => !crateState.folders.includes(f));
    if (fresh.length) suggest.replaceChildren(el("span", {class: "muted small"}, "FL Studio's browser folders:"),
      ...fresh.map(f => el("button", {class: "small", onclick: (e) => { add(f); e.target.remove(); }}, "+ " + f.replace(/[\\/]+$/, "").split(/[\\/]/).pop())));
  } catch (e) {}
}

function cratePreview() {
  runJob("crate_preview", {folders: crateState.folders, by: crateState.by},
    [backTo(crateStart), el("h1", {}, "Sorting preview"), el("p", {class: "sub"}, "Reading every sound, finding duplicates and working out genres. Nothing is changed.")],
    (r, id) => show(backTo(crateStart), el("h1", {}, "Sorting preview"),
      el("div", {class: "panel row"},
        el("div", {style: "flex:1"}, el("div", {class: "big"}, r.unique.toLocaleString()),
          el("div", {class: "muted"}, `sounds to sort (${plural(r.duplicates, "duplicate")} skipped, ${r.guessed.toLocaleString()} sorted by how they sound)`)),
        el("button", {onclick: () => api(`/api/jobs/${id}/open`, {file: "report"})}, "Open the full preview")),
      r.library_exists ? note("", "You already have a Crate library", el("div", {class: "muted"}, "Building adds these sounds to it; nothing in it is removed.")) : null,
      el("h2", {}, "By genre"),
      el("table", {}, el("thead", {}, el("tr", {}, el("th", {}, "Genre"), el("th", {class: "num"}, "Packs"), el("th", {class: "num"}, "Sounds"))),
        el("tbody", {}, r.genres.map(([g, p, n]) => el("tr", {}, el("td", {}, g), el("td", {class: "num"}, p), el("td", {class: "num"}, n.toLocaleString()))))),
      el("p", {class: "muted"}, "Genres come from pack names; packs whose names don't say get a guess from their loops' tempo, marked in the full preview."),
      el("div", {class: "row actions"}, el("button", {class: "primary", onclick: crateBuild}, "Build the library")),
      el("p", {class: "muted small"}, "Goes to ", el("span", {class: "mono"}, r.library), ".")),
    {noCancel: true});
}

function crateBuild() {
  runJob("crate_build", {folders: crateState.folders, by: crateState.by},
    [el("h1", {}, "Building your library"), el("p", {class: "sub"}, "Making links to your sounds, sorted. Your originals aren't moved.")],
    (r, id) => show(backTo(home), el("h1", {}, "Your library is ready"),
      el("div", {class: "panel row"},
        el("div", {style: "flex:1"}, el("div", {class: "big"}, r.made.toLocaleString()),
          el("div", {class: "muted"}, `sounds added (${plural(r.skipped, "duplicate")} skipped)` +
            (r.copies ? `; ${plural(r.copies, "file")} had to be copied because ${r.copies === 1 ? "it's" : "they're"} on another drive` : "; no extra space used"))),
        el("button", {class: "primary", onclick: () => api(`/api/jobs/${id}/open`, {file: "library"})}, "Open the library")),
      el("p", {class: "muted mono small"}, r.library),
      el("p", {class: "muted"}, "Tip: add this folder to FL Studio's browser (Options → File settings → Browser extra search folders) or Ableton's Places."),
      el("h2", {}, "Changed your mind?"),
      el("p", {class: "muted"}, "Removing the library never touches your original sample folders."),
      el("div", {class: "row"}, el("button", {onclick: crateUndo}, "Remove the library"))),
    {noCancel: true});
}

function crateUndo() {
  if (!confirm("Remove the Crate library? Your original sample folders aren't touched.")) return;
  runJob("crate_undo", {}, [el("h1", {}, "Removing the library")], (r) =>
    show(backTo(home), el("h1", {}, "Library removed"), el("p", {class: "muted"}, `${plural(r.removed, "file")} removed. Your original folders are unchanged.`),
      el("div", {class: "row actions"}, el("button", {onclick: home}, "Home"))), {noCancel: true});
}

home();
</script>
</body></html>
"""
