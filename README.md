# _Freighter

Moves a music producer's whole studio to a new computer: projects, presets, and every sample the
projects use, wherever it lives. It then repairs projects so nothing opens silent. Plugins can't be
copied, so **_Manifest**, the free plugin scan, gives you a reinstall checklist grouped by maker.

FL Studio and Ableton Live. Windows and Mac, in any direction. Free, and everything runs locally.

## Try it

    python3 manifest.py              # _Manifest: plugin report (use "py" on Windows)
    python3 freighter.py pack --list    # what a move would take, and how big
    python3 freighter.py rehearse       # practise a full move on this computer, safely

Testing on a Windows PC, step by step: [WINDOWS-TEST.md](WINDOWS-TEST.md). The full walkthrough for a real move is in [MOVING-FROM-WINDOWS.md](MOVING-FROM-WINDOWS.md).

## Files

| Path | What it is |
|---|---|
| `manifest.py` | _Manifest: finds installed plugins, flags problems, compares two computers |
| `freighter.py` | Freighter: `scan`, `pack`, `unpack`, `fix-ableton`, `check-fl`, `rehearse` |
| `tests/` | Automated tests: `python3 -m unittest discover -s tests -v` |
| `.github/workflows/tests.yml` | Runs the tests on Windows and macOS for every change |
| `site/` | Landing page: `landing.html` is the source, `python3 build.py` makes `deploy/` for Netlify |
| `TESTING.md` | How Freighter is tested |

Needs only Python 3.8 or newer. No other packages.
