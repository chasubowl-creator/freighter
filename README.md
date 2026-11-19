# _Freighter

Moves a music producer's whole studio to a new computer: projects, presets, and every sample the
projects use, wherever it lives. It then repairs projects so nothing opens silent. Plugins can't be
copied, so **_Manifest**, the free plugin scan, gives you a reinstall checklist grouped by maker.

FL Studio and Ableton Live. Windows and Mac, in any direction. Free, and everything runs locally.

## Try it

    python3 manifest.py              # _Manifest: plugin report (use "py" on Windows)
    python3 freighter.py pack --list    # what a move would take, and how big
    python3 freighter.py rehearse       # practise a full move on this computer, safely
    python3 crate.py preview ~/Desktop/DrumKits   # _Crate: see how your samples would be sorted

Testing on a Windows PC, step by step: [WINDOWS-TEST.md](WINDOWS-TEST.md). The full walkthrough for a real move is in [MOVING-FROM-WINDOWS.md](MOVING-FROM-WINDOWS.md).

What's left before release, in order: [ROADMAP.md](ROADMAP.md).

## Files

| Path | What it is |
|---|---|
| `manifest.py` | _Manifest: finds installed plugins, flags problems, compares two computers |
| `freighter.py` | Freighter: `scan`, `pack`, `unpack`, `fix-ableton`, `check-fl`, `rehearse` |
| `app.py` | Freighter in your web browser (`freighter app`, preview): a private local page for the move list and plugin scan |
| `crate.py` | _Crate: sorts drum kits and sample packs into one library by genre, pack and sound type (`preview`, `build`, `undo`). Never moves your originals |
| `tests/` | Automated tests: `python3 -m unittest discover -s tests -v` |
| `.github/workflows/tests.yml` | Runs the tests on Windows and macOS for every change |
| `site/` | Landing page: `landing.html` is the source, `python3 build.py` makes `deploy/` for Netlify |
| `TESTING.md` | How Freighter is tested |
| `freighter_version.py` | The version number all three tools share |
| `CHANGELOG.md` | What changed in each version |
| `ROADMAP.md` | What's left before release, in order |
| `LICENSE` | GPL-3.0 |

Needs only Python 3.8 or newer. No other packages.

## License

Freighter is free software under the [GNU General Public License v3.0](LICENSE). You can use, study, share and
change it. If you share a changed version, it has to stay open under the same license.

Copyright © 2026 chasubowl-creator.

## Contributing

Bug reports and ideas are very welcome: [open an issue](https://github.com/chasubowl-creator/freighter/issues/new).
Please attach the report files Freighter wrote (for example `rehearsal-report.txt` or `unpack-report.txt`) and say which
version you ran (`freighter --version`).

Code contributions (pull requests) aren't being accepted for now. This keeps the project's licensing simple while it
finds its feet.
