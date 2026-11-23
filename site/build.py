#!/usr/bin/env python3
"""Build the hostable page: wraps landing.html (the Claude preview source) into a full
index.html, with sign-ups switched to Netlify Forms. Run after editing landing.html:

    python3 build.py
"""
import re
from pathlib import Path

here = Path(__file__).resolve().parent
src = (here / "landing.html").read_text(encoding="utf-8")

# The page loads nothing from other sites (issue #42): fonts and images live in assets/. Links may point anywhere.
OUTSIDE = r"""((?:https?:)?//[^"')\s>]+)"""
loads = (re.findall(r"""\bsrc\s*=\s*["']?\s*""" + OUTSIDE, src, re.I)
         + re.findall(r"""<link\b[^>]*\bhref\s*=\s*["']?\s*""" + OUTSIDE, src, re.I)
         + re.findall(r"""(?:url\(|@import)\s*["']?\s*""" + OUTSIDE, src, re.I))
if loads:
    raise SystemExit("landing.html loads from other sites (put the file in assets/ instead):\n  " + "\n  ".join(loads))

src = src.replace('<meta name="claude:viewport-fit-sides" content="cover">\n', "")
mode_line = 'const FORM_MODE = "preview";'
if mode_line not in src:
    raise SystemExit("Couldn't find the FORM_MODE line in landing.html")
src = src.replace(mode_line, 'const FORM_MODE = "netlify";')

split = src.index("</style>") + len("</style>")
doc = ("<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
       "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1,viewport-fit=cover\">\n"
       + src[:split] + "\n</head>\n<body>\n" + src[split:] + "\n</body>\n</html>\n")

out = here / "deploy"
out.mkdir(exist_ok=True)
(out / "index.html").write_text(doc, encoding="utf-8")
import shutil
if (here / "assets").is_dir():
    shutil.copytree(here / "assets", out / "assets", dirs_exist_ok=True)  # images the page uses
old = here / "index.html"
if old.exists():
    old.unlink()  # the hostable copy now lives in deploy/
print(f"Built {out / 'index.html'}  ->  drag the 'deploy' folder onto Netlify")
