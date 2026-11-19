#!/usr/bin/env python3
"""
_Crate - sorts drum kits and sample packs into one tidy library. Part of Freighter.

Your original files are never moved or renamed (your projects point at them). _Crate builds a separate
sorted library of hard links: on the same drive they take no extra space and stay in sync with the
originals. Across drives it copies instead. Everything it creates is logged, so "undo" removes exactly that.

  python3 crate.py preview ~/Desktop/DrumKits ~/Downloads     what would go where (nothing is changed)
  python3 crate.py build   ~/Desktop/DrumKits ~/Downloads     build the library in Music/Freighter Crate
        options: --to FOLDER (library location), --copy (real copies instead of links)
  python3 crate.py undo                                       remove what the last build created

The library is grouped by genre, then by pack, then by sound type:
  House/UNDRGRND Sounds - Organic House - Wav/Drums/Kicks/...    (--by type drops the genre and pack levels)
A pack's genre comes from its name (house, trap, UK garage...). Packs whose name doesn't say get a guess from
their loops' tempo, marked as a guess in the report; packs with neither go in "Other".

Sorting uses the file name first, then its folder names, then (for unlabelled WAVs) the sound itself:
how long it is and how noisy or boomy it is. Sound-based guesses are marked as guesses in the report.
Needs only Python 3.8+.
"""

import argparse
import datetime as dt
import hashlib
import html
import json
import os
import re
import shutil
import struct
import sys
import wave
import webbrowser
from pathlib import Path

AUDIO_EXTS = {".wav", ".wave", ".aif", ".aiff", ".flac", ".mp3", ".ogg", ".m4a"}
MIDI_EXTS = {".mid", ".midi"}
SKIP_DIRS = {"__macosx", ".trash", "$recycle.bin", "freighter crate", "freighter backup"}

# ------------------------------------------------------------------------------------------ categories
# Drum one-shots, most specific first. A tag in the file name beats one in a folder name.
ONE_SHOT_RULES = [
    ("Drums/808s",            {"808", "808s"}, set()),
    ("Drums/Hi-Hats/Open",    {"ohh", "ophat", "openhat", "openhh"}, {"open hat", "open hh", "open hihat", "open hi hat"}),
    ("Drums/Hi-Hats/Closed",  {"hat", "hats", "hh", "hihat", "hihats", "chh", "hhc"}, {"hi hat", "closed hat"}),
    ("Drums/Kicks",           {"kick", "kicks", "kik", "bd", "bassdrum", "kck"}, {"bass drum"}),
    ("Drums/Snares",          {"snare", "snares", "snr", "sn", "sd"}, set()),
    ("Drums/Claps",           {"clap", "claps", "clp"}, set()),
    ("Drums/Rims",            {"rim", "rims", "rimshot", "sidestick"}, set()),
    ("Drums/Toms",            {"tom", "toms"}, set()),
    ("Drums/Cymbals",         {"crash", "ride", "cymbal", "cymbals", "cym", "china", "splash"}, set()),
    ("Drums/Percussion",      {"perc", "percs", "prc", "percussion", "percussions", "shaker", "shakers", "tambourine", "tamb", "conga",
                               "congas", "bongo", "bongos", "bongodrm", "cowbell", "woodblock", "clave", "guiro", "snap", "snaps"},
                              set()),
    ("Vocals",                {"vox", "vocal", "vocals", "acapella", "acapellas", "acappella", "chant", "adlib", "adlibs", "tag", "tags"}, set()),
    ("FX",                    {"fx", "fxs", "sfx", "riser", "uplifter", "downlifter", "impact", "sweep", "whoosh", "noise",
                               "transition", "trans", "reverse", "foley"}, set()),
    ("Bass",                  {"bass", "basses", "sub", "reese", "synthbass"}, set()),
    ("Keys & Chords",         {"keys", "piano", "rhodes", "organ", "chord", "chords", "stab", "stabs"}, set()),
    ("Pads",                  {"pad", "pads", "atmos", "atmosphere", "ambient", "drone", "texture", "textures", "background"}, set()),
    ("Synths",                {"synth", "synths", "lead", "leads", "pluck", "plucks", "arp", "mallet", "mallets", "marimba",
                               "bell", "bells", "guitar", "strings", "brass", "flute", "melodic", "melody"}, set()),
]
# Loops get their own shelves.
LOOP_RULES = [
    ("Loops/Vocals", ONE_SHOT_RULES[10][1]),
    ("Loops/Drums",  {"drum", "drums", "top", "tops", "groove", "grooves", "beat", "beats", "break", "breaks",
                      "stripped", "kick", "hat", "hats", "hh", "perc", "percussion", "snare", "clap", "shaker", "crash",
                      "ride", "tom", "toms", "808"} | ONE_SHOT_RULES[1][1] | ONE_SHOT_RULES[2][1] | ONE_SHOT_RULES[3][1]
                     | ONE_SHOT_RULES[4][1] | ONE_SHOT_RULES[5][1] | ONE_SHOT_RULES[6][1] | ONE_SHOT_RULES[9][1]),
    ("Loops/Bass",   ONE_SHOT_RULES[12][1]),
    ("Loops/FX",     ONE_SHOT_RULES[11][1]),
    ("Loops/Melodic", ONE_SHOT_RULES[13][1] | ONE_SHOT_RULES[14][1] | ONE_SHOT_RULES[15][1]),
]
LOOP_WORDS = {"loop", "loops", "lp", "break", "breaks"}
ONE_SHOT_WORDS = {"oneshot", "oneshots", "shot", "shots", "hit", "hits"}
NOT_BPM = {808, 909, 707, 606, 303}


# a pack's genre, from its folder name. First match wins, so "tech house" is checked before "house".
GENRE_RULES = [
    ("Tech House",    r"tech ?house"),
    ("UK Garage",     r"uk ?garage|\bukg\b|\b2 ?step\b|\bgarage\b"),
    ("Jersey Club",   r"jersey|jerzy|\bclub\b"),
    ("Drum & Bass",   r"drum ?(and|&|n) ?bass|\bdnb\b|\bjungle\b"),
    ("Drill",         r"\bdrill\b"),
    ("Trap",          r"\btrap\b|\brage\b|\bopium\b|\bmetro+\b|\b808 ?mafia\b|\bplugg?\b"),
    ("Hip-Hop",       r"hip ?hop|boom ?bap|\brap\b"),
    ("Lo-Fi",         r"lo ?fi\b"),
    ("R&B",           r"\br ?(&|n|and) ?b\b|\brnb\b"),
    ("Afro",          r"\bafro|amapiano"),
    ("Techno",        r"\btechno\b"),
    ("Dubstep",       r"dubstep|\bdub\b|riddim"),
    ("House",         r"house"),
    ("Pop",           r"\bpop\b"),
]


def genre_of(pack, bpms=()):
    """(genre, how) for a pack: how is 'name', 'tempo' (a guess) or 'none'."""
    name = re.sub(r"[_\-.]+", " ", pack.lower())
    for genre, pattern in GENRE_RULES:
        if re.search(pattern, name):
            return genre, "name"
    bpms = sorted(b for b in bpms if b)
    if len(bpms) >= 10:
        mid = bpms[len(bpms) // 2]
        if 110 <= mid <= 128:
            return "House", "tempo"
        if 129 <= mid <= 140:
            return "Techno", "tempo"
        if mid >= 141 or mid <= 100:
            return "Trap", "tempo"
    return "Other", "none"


def tokens(text):
    """'US_DG_SynthBass_123_zed_E (HH)' -> ['us','dg','synth','bass','123','zed','e','hh']"""
    t = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    t = re.sub(r"([A-Za-z])(\d)", r"\1 \2", t)
    t = re.sub(r"(\d)([A-Za-z])", r"\1 \2", t)
    words = [w for w in re.split(r"[^a-z0-9#]+", t.lower()) if w]
    # glue tags that sample packs commonly write as one word, so "808s" and "hihat" still match
    return words + ["".join(words[i:i + 2]) for i in range(len(words) - 1)]


def find_bpm(stem):
    for m in re.finditer(r"(?<![\d.])(\d{2,3})(?:\s*bpm)?(?![\d.])", stem, re.I):
        n = int(m.group(1))
        if 60 <= n <= 200 and n not in NOT_BPM:
            return n
    return None


def find_key(stem):
    m = re.search(r"(?:^|[_\s\-\(\[])([A-G])([#b♯♭]?)\s*(m(?:in(?:or)?)?|maj(?:or)?)?(?=$|[_\s\-\)\].])", stem)
    if not m:
        return None
    note = m.group(1) + m.group(2).replace("♯", "#").replace("♭", "b")
    minor = (m.group(3) or "").lower().startswith("m") and not (m.group(3) or "").lower().startswith("maj")
    return note + ("m" if minor else "")


def phrase_hit(words, phrases):
    joined = " " + " ".join(words) + " "
    return any(" " + p + " " in joined for p in phrases)


def match_rules(words, rules):
    ws = set(words)
    for cat, tags, phrases in rules:
        if ws & tags or phrase_hit(words, phrases):
            return cat
    return None


def match_loop(words):
    ws = set(words)
    for cat, tags in LOOP_RULES:
        if ws & tags:
            return cat
    return None


# ------------------------------------------------------------------------------------------ listening
def wav_features(path, max_seconds=1.0):
    """(duration s, zero-crossing rate, rms) of a PCM WAV, or None if it can't be read here."""
    try:
        with wave.open(str(path), "rb") as w:
            ch, width, rate, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            dur = n / float(rate) if rate else 0
            frames = w.readframes(min(n, int(rate * max_seconds)))
    except (wave.Error, EOFError, OSError, struct.error):
        return None
    if not frames or width not in (1, 2, 3, 4):
        return (dur, None, None) if dur else None
    step = width * ch
    vals = []
    for i in range(0, len(frames) - step + 1, step * 2):   # every other frame, first channel
        b = frames[i:i + width]
        if width == 1:
            v = b[0] - 128
        elif width == 2:
            v = struct.unpack("<h", b)[0]
        elif width == 3:
            v = int.from_bytes(b, "little", signed=True)
        else:
            v = struct.unpack("<i", b)[0]
        vals.append(v)
    if len(vals) < 32:
        return (dur, None, None)
    peak = max(1, max(abs(v) for v in vals))
    gate = peak * 0.02                                       # ignore near-silence when counting crossings
    loud = [v for v in vals if abs(v) > gate]
    crossings = sum(1 for a, b in zip(loud, loud[1:]) if (a < 0) != (b < 0))
    zcr = crossings / max(1, len(loud)) * (rate / 2) / 22050   # normalise to 44.1 kHz, every-other-frame read
    rms = (sum(v * v for v in vals) / len(vals)) ** 0.5 / peak
    return (dur, zcr, rms)


def guess_from_sound(feat):
    """Best guess for an unlabelled one-shot from how it sounds. Returns (category, reason) or (None, None)."""
    if not feat:
        return None, None
    dur, zcr, _rms = feat
    if dur > 30:
        return "Unsorted/Long audio", f"{dur:.0f} s long, probably a song or stem"
    if zcr is None:
        return None, None
    if dur > 2.5:
        return None, None
    if zcr < 0.012 and dur > 0.6:
        return "Drums/808s", "low and long"
    if zcr < 0.03:
        return "Drums/Kicks", "low and short"
    if zcr > 0.28:
        return ("Drums/Hi-Hats/Closed" if dur < 0.35 else "Drums/Hi-Hats/Open"), "bright and noisy"
    if zcr > 0.08:
        return "Drums/Snares", "noisy mid-range"
    return None, None


# ------------------------------------------------------------------------------------------ classify
def classify(path, root):
    """(category, how, bpm, key). how: 'name', 'folder', 'sound' (a guess) or 'none'."""
    stem = path.stem
    ext = path.suffix.lower()
    rel_dirs = list(path.parent.relative_to(root).parts)[::-1][:4] if path.parent != root else []
    name_words = tokens(stem)
    folder_words = [tokens(d) for d in rel_dirs]
    if ext in MIDI_EXTS:
        return "MIDI", "name", find_bpm(stem), find_key(stem)
    bpm, key = find_bpm(stem), find_key(stem)
    all_words = set(name_words).union(*folder_words) if folder_words else set(name_words)
    # only the file's own folder can call it a loop (pack names like "Loop Cult" don't count);
    # "One Shots/Drum Hits" two levels up still marks one-shots
    own = set(folder_words[0]) if folder_words else set()
    near = set().union(*folder_words[:2]) if folder_words else set()
    said_loop = bool(set(name_words) & LOOP_WORDS)
    said_shot = bool(set(name_words) & ONE_SHOT_WORDS) or (bool(near & ONE_SHOT_WORDS) and not said_loop)
    is_loop = not said_shot and (said_loop or bool(bpm) or bool(own & LOOP_WORDS))
    if is_loop:
        for how, words in [("name", name_words)] + [("folder", fw) for fw in folder_words]:
            cat = match_loop(words)
            if cat:
                return cat, how, bpm, key
        return "Loops/Other", "name", bpm, key
    for how, words in [("name", name_words)] + [("folder", fw) for fw in folder_words]:
        cat = match_rules(words, ONE_SHOT_RULES)
        if cat:
            return cat, how, bpm, key
    if ext in (".wav", ".wave"):
        cat, reason = guess_from_sound(wav_features(path))
        if cat:
            return cat, "sound:" + reason, bpm, key
    return "Unsorted", "none", bpm, key


def find_files(folders):
    out = []
    for f in folders:
        root = Path(f).expanduser().resolve()
        if root.is_file():
            out.append((root, root.parent))
            continue
        if not root.is_dir():
            print(f"Not found, skipped: {f}")
            continue
        for cur, dirs, files in os.walk(root, onerror=lambda e: None):
            dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIRS and not d.startswith(".")]
            for fn in files:
                if fn.startswith("._"):
                    continue
                ext = os.path.splitext(fn)[1].lower()
                if ext in AUDIO_EXTS or ext in MIDI_EXTS:
                    out.append((Path(cur) / fn, root))
    return out


def file_hash(p):
    h = hashlib.sha1()
    with open(p, "rb") as f:
        while True:
            b = f.read(1 << 20)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def plan(folders):
    files = find_files(folders)
    print(f"Sorting {len(files)} files...", flush=True)
    # duplicates: same size first, then same contents
    by_size = {}
    for p, root in files:
        try:
            by_size.setdefault(p.stat().st_size, []).append((p, root))
        except OSError:
            pass
    dup_of = {}
    for size, group in by_size.items():
        if len(group) < 2:
            continue
        seen = {}
        for p, _ in group:
            try:
                h = file_hash(p)
            except OSError:
                continue
            if h in seen:
                dup_of[str(p)] = str(seen[h])
            else:
                seen[h] = p
    items = []
    for i, (p, root) in enumerate(files, 1):
        if i % 500 == 0:
            print(f"  {i}/{len(files)}", flush=True)
        cat, how, bpm, key = classify(p, root)
        pack = p.relative_to(root).parts[0] if len(p.relative_to(root).parts) > 1 else root.name
        items.append({"path": str(p), "name": p.name, "pack": pack, "category": cat, "how": how,
                      "bpm": bpm, "key": key, "duplicate_of": dup_of.get(str(p))})
    bpms = {}
    for it in items:
        bpms.setdefault(it["pack"], []).append(it["bpm"])
    genres = {pack: genre_of(pack, b) for pack, b in bpms.items()}
    for it in items:
        it["genre"], it["genre_how"] = genres[it["pack"]]
    return items


# ------------------------------------------------------------------------------------------ report
CSS = """
:root{--bg:#0f1114;--panel:#171a1f;--line:#272c33;--ink:#eef0f2;--muted:#9aa1ab;--blue:#3d8bff;--warn:#f0b44b}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:1000px;margin:0 auto;padding:32px 16px 64px}h1{margin:0 0 4px;font-size:28px}h2{font-size:19px;margin:32px 0 8px}
.sub{color:var(--muted);margin:0 0 20px}.u{color:var(--blue);font-family:ui-monospace,Menlo,monospace}
.bars{display:flex;flex-direction:column;gap:6px}.bar{display:grid;grid-template-columns:200px 1fr 60px;gap:10px;align-items:center}
.bar i{display:block;height:12px;border-radius:3px;background:var(--blue)}.bar span:last-child{text-align:right;color:var(--muted);font-variant-numeric:tabular-nums}
details{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:8px 12px;margin:6px 0}
summary{cursor:pointer;font-weight:600}ul{margin:8px 0 4px;padding-left:18px;color:var(--muted);font-size:13.5px}
.tag{font-size:12px;color:var(--warn)}.note{background:var(--panel);border-left:3px solid var(--blue);padding:10px 14px;border-radius:4px;margin:12px 0}
"""


def write_report(items, out_dir, title):
    out_dir.mkdir(parents=True, exist_ok=True)
    cats = {}
    for it in items:
        if not it["duplicate_of"]:
            cats.setdefault(it["category"], []).append(it)
    dups = [it for it in items if it["duplicate_of"]]
    guessed = [it for it in items if it["how"].startswith("sound:") and not it["duplicate_of"]]
    top = max((len(v) for v in cats.values()), default=1)
    h = [f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
         f"<title>{html.escape(title)}</title><style>{CSS}</style><main>"]
    h.append(f"<h1><span class=u>_</span>Crate</h1><p class=sub>{len(items)} files &middot; "
             f"{len(items) - len(dups)} unique &middot; {len(dups)} duplicates skipped &middot; "
             f"{len(guessed)} sorted by sound (guesses)</p>")
    packs = {}
    for it in items:
        if not it["duplicate_of"] and it.get("genre"):
            packs.setdefault(it["genre"], {}).setdefault(it["pack"], [0, it["genre_how"]])[0] += 1
    if packs:
        h.append("<h2>Packs by genre</h2>")
        for g in sorted(packs, key=lambda g: (g == "Other", g)):
            lis = "".join(f"<li>{html.escape(pk)} ({n})" + (" <span class=tag>genre guessed from tempo</span>" if how == "tempo" else "")
                          + "</li>" for pk, (n, how) in sorted(packs[g].items(), key=lambda kv: kv[0].lower()))
            h.append(f"<details open><summary>{html.escape(g)}</summary><ul>{lis}</ul></details>")
    h.append("<h2>Sound types</h2><div class=bars>")
    for cat in sorted(cats, key=lambda c: (c.startswith("Unsorted"), c)):
        n = len(cats[cat])
        h.append(f"<div class=bar><span>{html.escape(cat)}</span><i style='width:{max(2, n / top * 100):.1f}%'></i><span>{n}</span></div>")
    h.append("</div><h2>Look inside</h2>")
    for cat in sorted(cats, key=lambda c: (c.startswith("Unsorted"), c)):
        lis = "".join(
            f"<li>{html.escape(it['name'])}"
            + (f" <span class=tag>guessed: {html.escape(it['how'][6:])}</span>" if it["how"].startswith("sound:") else "")
            + (f" &middot; {it['bpm']} BPM" if it["bpm"] else "") + (f" &middot; {html.escape(it['key'])}" if it["key"] else "")
            + "</li>" for it in sorted(cats[cat], key=lambda x: x["name"].lower())[:400])
        more = f"<li>&hellip; and {len(cats[cat]) - 400} more</li>" if len(cats[cat]) > 400 else ""
        h.append(f"<details><summary>{html.escape(cat)} ({len(cats[cat])})</summary><ul>{lis}{more}</ul></details>")
    h.append("<p class=note>Nothing has been changed. Your originals stay where they are; building the library adds "
             "links (or copies) in a separate folder.</p></main>")
    page = out_dir / "crate-preview.html"
    page.write_text("".join(h), encoding="utf-8")
    (out_dir / "crate-plan.json").write_text(json.dumps(items, indent=1), encoding="utf-8")
    return page


# ------------------------------------------------------------------------------------------ build / undo
def default_library():
    return Path(os.environ.get("FREIGHTER_HOME") or Path.home()) / "Music" / "Freighter Crate"


def safe_part(s):
    s = re.sub(r'[<>:"|?*\x00-\x1f]', "_", s).strip(" .")
    return s or "_"


def place(src, dst, copy):
    """Link (or copy) src to dst. Returns 'link', 'copy' or 'same'."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        try:
            if os.path.samefile(src, dst):
                return "same"
        except OSError:
            pass
        raise FileExistsError(dst)
    if not copy:
        try:
            os.link(src, dst)
            return "link"
        except OSError:
            pass   # different drive, or a file system without hard links
    shutil.copy2(src, dst)
    return "copy"


def folder_for(it, library, by="genre"):
    parts = it["category"].split("/")
    if by == "genre":
        parts = [it.get("genre") or "Other", it["pack"]] + parts
    elif it["category"] == "MIDI":
        parts = parts + [it["pack"]]
    return library.joinpath(*[safe_part(p) for p in parts])


def build(items, library, copy, by="genre"):
    library.mkdir(parents=True, exist_ok=True)
    log_path = library / "crate-log.json"
    log = json.loads(log_path.read_text(encoding="utf-8")) if log_path.is_file() else {"created": []}
    made = links = copies = skipped = 0
    for it in items:
        if it["duplicate_of"]:
            skipped += 1
            continue
        src = Path(it["path"])
        folder = folder_for(it, library, by)
        name = safe_part(src.name)
        dst = folder / name
        for n in range(0, 50):
            try:
                how = place(src, dst, copy)
                break
            except FileExistsError:
                stem, ext = os.path.splitext(name)
                dst = folder / (f"{stem} ({safe_part(it['pack'])}){ext}" if n == 0 else f"{stem} ({safe_part(it['pack'])} {n + 1}){ext}")
        else:
            continue
        if how == "same":
            continue
        made += 1
        links += how == "link"
        copies += how == "copy"
        log["created"].append({"path": str(dst), "source": str(src), "how": how})
    log["last_build"] = dt.datetime.now().isoformat(timespec="seconds")
    log_path.write_text(json.dumps(log, indent=1), encoding="utf-8")
    return made, links, copies, skipped


def undo(library):
    log_path = library / "crate-log.json"
    if not log_path.is_file():
        sys.exit(f"Nothing to undo: no crate-log.json in {library}")
    log = json.loads(log_path.read_text(encoding="utf-8"))
    removed = kept = 0
    for e in log.get("created", []):
        p = Path(e["path"])
        if not p.exists():
            continue
        try:
            ok = e["how"] == "copy" or os.path.samefile(p, e["source"])
        except OSError:
            ok = False   # the original is gone: this link is now the only copy, so keep it
        if ok:
            p.unlink()
            removed += 1
        else:
            kept += 1
    for extra in ("crate-preview.html", "crate-plan.json"):   # the report the build wrote
        (library / extra).unlink(missing_ok=True)
    # tidy now-empty folders, deepest first
    for cur, dirs, files in sorted(os.walk(library), key=lambda t: -len(t[0])):
        if Path(cur) != library and not os.listdir(cur):
            os.rmdir(cur)
    log_path.unlink()
    if library.is_dir() and not os.listdir(library):
        library.rmdir()
    print(f"Removed {removed} files the build created."
          + (f" Kept {kept} whose original is gone (they're now the only copy)." if kept else ""))
    return removed


# ------------------------------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description="Sort drum kits and sample packs into a tidy library.",
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("preview", help="show what would go where (changes nothing)")
    s.add_argument("folders", nargs="+")
    s.add_argument("--out", metavar="FOLDER", help="where to write the preview (default: next to this program)")
    s.add_argument("--no-open", action="store_true")
    s = sub.add_parser("build", help="build the sorted library")
    s.add_argument("folders", nargs="+")
    s.add_argument("--to", metavar="FOLDER", help="library location (default: Music/Freighter Crate)")
    s.add_argument("--copy", action="store_true", help="make real copies instead of links")
    s.add_argument("--by", choices=("genre", "type"), default="genre",
                   help="genre: Genre/Pack/Kicks... (default); type: Kicks... with every pack mixed together")
    s = sub.add_parser("undo", help="remove what builds created")
    s.add_argument("--to", metavar="FOLDER", help="library location (default: Music/Freighter Crate)")
    args = ap.parse_args()

    if args.cmd == "undo":
        undo(Path(args.to).expanduser() if args.to else default_library())
        return 0
    items = plan(args.folders)
    if not items:
        print("No audio or MIDI files found.")
        return 0
    if args.cmd == "preview":
        here = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
        page = write_report(items, Path(args.out) if args.out else here / "crate-preview", "_Crate preview")
        counts = {}
        for it in items:
            if not it["duplicate_of"]:
                counts[it["category"]] = counts.get(it["category"], 0) + 1
        for cat in sorted(counts, key=lambda c: (c.startswith("Unsorted"), c)):
            print(f"  {counts[cat]:>6}  {cat}")
        print(f"\nPreview: {page}\nNothing was changed. Run 'build' to create the library.")
        if not args.no_open:
            webbrowser.open(page.as_uri())
        return 0
    library = Path(args.to).expanduser() if args.to else default_library()
    made, links, copies, skipped = build(items, library, args.copy, args.by)
    write_report(items, library, "_Crate library")
    print(f"\nLibrary: {library}\n{made} files added ({links} links, {copies} copies), {skipped} duplicates skipped."
          f"\nYour originals weren't touched. To remove the library: crate.py undo"
          + (f" --to \"{library}\"" if args.to else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
