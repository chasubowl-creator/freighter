# Testing Freighter

Three layers, from fastest to most realistic. None of them change your real files.

## 1. Automated tests (seconds, run after every change)

    python3 -m unittest discover -s tests -v

They cover:
- plugin naming and matching
- chip detection (Intel / Apple Silicon / 32-bit)
- reading FL Studio and Ableton projects
- Windows ↔ Mac path mapping
- full pack → unpack → repair moves between fake computers
- resuming after an interruption
- damaged-file detection
- not overwriting files that already exist
- the rehearsal itself

Each test works in its own temporary folder.

## 2. Rehearsal on your own computer (minutes)

    python3 freighter.py rehearse

This does a full move on one machine. It packs your real setup, unpacks it into a stand-in "new computer"
folder, and repairs the projects. Then it checks **every sample in every project**. While it checks, it
pretends the old computer's files are gone, so a pass means the moved copies are what's working.

- **PASS:** every sample that worked before the move still works after it.
- **FAIL:** lists each lost sample. That's a Freighter bug: send the `rehearsal-report.txt`.

It needs about twice the size of what gets packed (`freighter.py pack --list` shows the total).
Leave big items out with `--skip ID`, or put it on another drive with `--to /Volumes/DRIVE`.
Delete the `Rehearsal` folder in `Music/Freighter` afterwards.

**On a Windows PC:** same command with `py` instead of `python3`.

## 3. Real Windows and Mac machines (automatic, once on GitHub)

`.github/workflows/tests.yml` runs the automated tests, the scanner and `pack --list` on Windows and
macOS, with old (3.8) and new (3.12) Python, every time code changes. This catches Windows-only problems
that can't show up on a Mac. Results are on the repository's **Actions** tab
(https://github.com/chasubowl-creator/freighter/actions), which also holds the built programs.

## What still needs a person

- Opening moved projects in FL Studio and Ableton and listening to them.
- A real move between two different computers. Your current PC → new PC is the first one.
