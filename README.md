# _Freighter

Moves a music producer's whole studio to a new computer: projects, presets, and every sample the
projects use, wherever it lives. It then repairs projects so nothing opens silent. Plugins that are only
files (most free and indie ones) come along too, back in their plugin folders, after your virus scanner
checks them on both computers. For the rest, which install
through their maker's app, **_Manifest**, the free plugin scan, gives you a reinstall checklist grouped by maker.

FL Studio and Ableton Live. Windows and Mac, in any direction. Free, and everything runs locally.

## Install

1. Download the newest zip from **[Releases](https://github.com/chasubowl-creator/freighter/releases)**: `freighter-windows.zip`,
   or `freighter-mac.zip` (Apple Silicon) / `freighter-mac-intel.zip` (Intel Mac).
2. Unzip it. Windows: right-click → **Extract All**. Mac: double-click.
3. Open it.
   - **Windows:** double-click `freighter.exe`. If "Windows protected your PC" appears, click **More info → Run anyway**
     (Freighter isn't code-signed yet).
   - **Mac:** double-click `freighter`. The first time, macOS blocks it: **System Settings → Privacy & Security**,
     scroll down, **Open Anyway**, then double-click it again.
4. A numbered menu opens; **9** opens the app in your browser. The same steps are in `START HERE.txt` inside the zip.

**What Freighter touches:** it only reads your music folders and projects on the old computer, and writes only to the
drive you pick. On the new computer it puts your files back where they were (or in `Music/Freighter`), repairs
projects after keeping a backup of each, and puts copied plugins in their plugin folders (asking Windows first for
Program Files). Everything it creates can be undone. Nothing is uploaded: it only goes online when you click
"Check for updates" or a link.

## Try it from source

    python3 manifest.py              # _Manifest: plugin report (use "py" on Windows)
    python3 freighter.py pack --list    # what a move would take, and how big
    python3 freighter.py pack --list --since 2024-03   # only projects saved since March 2024, with their kits
    python3 freighter.py rehearse       # practise a full move on this computer, safely
    python3 freighter.py check-update   # is a newer Freighter out? (asks GitHub only when you run it)
    python3 freighter.py verify E:\     # after packing: read the drive back and compare every file
    python3 freighter.py health         # on the new computer: is every project ready?
    python3 crate.py preview ~/Desktop/DrumKits   # _Crate: see how your samples would be sorted

Testing on a Windows PC, step by step: [WINDOWS-TEST.md](WINDOWS-TEST.md). The full walkthrough for a real move is in [MOVING-FROM-WINDOWS.md](MOVING-FROM-WINDOWS.md).

What's left before release, in order: [ROADMAP.md](ROADMAP.md).

## Files

| Path | What it is |
|---|---|
| `manifest.py` | _Manifest: finds installed plugins, flags problems, compares two computers |
| `freighter.py` | Freighter: `scan`, `pack`, `unpack`, `fix-ableton`, `fix-fl` (`check-fl` reports only), `plugins`, `undo-unpack`, `rehearse` |
| `assets/` | The app's photo and fonts, bundled so nothing loads online (credits in `assets/CREDITS.md`) |
| `app.py`, `app_page.py` | Freighter in your web browser (`freighter app`, or menu option 9): pack, unpack and repair, and the plugin checklist, on a private local page |
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
