"""Release notes for a version tag: how to download and open Freighter, then that version's CHANGELOG section.
Run by the release job (issue #36). Usage: python3 .github/release_notes.py v0.9.0-test.1"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from freighter_version import __version__  # noqa: E402


def notes(tag):
    version = tag[1:] if tag.startswith("v") else tag
    base = version.split("-")[0]
    if base != __version__:
        sys.exit(f"Tag {tag} doesn't match freighter_version.py ({__version__}). Fix one of them first.")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    m = re.search(rf"^## {re.escape(base)}\b[^\n]*\n(.*?)(?=^## |\Z)", changelog, re.S | re.M)
    section = m.group(1).strip() if m else "See CHANGELOG.md."
    parts = []
    if "-" in version:
        parts += ["**Test build.** For trying Freighter on your own computer before a real move: the steps are in "
                  "`WINDOWS-TEST.md` inside the download. Please don't rely on it for your real move yet.", ""]
    parts += [
        "## Download",
        "",
        "- **Windows:** download **freighter-windows.zip**, right-click it, choose **Extract All**, then double-click "
        "**freighter.exe** in the extracted folder. If Windows says \"Windows protected your PC\", click "
        "**More info → Run anyway** (Freighter isn't code-signed yet).",
        "- **Mac:** download **freighter-mac.zip**, double-click it to unzip, then double-click **freighter**. The "
        "first time, macOS blocks it: open **System Settings → Privacy & Security**, scroll down and click "
        "**Open Anyway**.",
        "",
        "If your browser warns that the file isn't commonly downloaded, choose **Keep**. Nothing to install and no "
        "Python needed. Each download holds `freighter` (the mover), `manifest` (the plugin list), `crate` (the "
        "sample sorter), **START HERE.txt** with these steps, the test guide, the license and the changelog.",
        "",
        f"## What's in {base}",
        "",
        section,
    ]
    return "\n".join(parts) + "\n"


if __name__ == "__main__":
    sys.stdout.write(notes(sys.argv[1]))
