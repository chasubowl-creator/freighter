# Changelog

Changes to Freighter: _Freighter (the mover), _Manifest (the plugin scan) and _Crate (the sample sorter).
All three share one version number, set in `freighter_version.py`. Check yours with `freighter --version`.

## 0.9.0 (beta, not yet released)

The first version with a number. Everything that exists so far:

### _Freighter
- Moves FL Studio and Ableton Live setups between Windows and Mac, in any direction, using a bundle on an external drive.
- Finds projects, the FL Studio and Ableton user folders, and every sample folder your projects use.
- Copies can resume after an interruption, and every file is checked with a checksum.
- Leaves programs and installers out of the move (listed in `left-behind.txt`).
- Repairs Ableton Live 11 and 12 projects so they point at the moved samples, keeping a backup of each original.
- Checks FL Studio projects for samples that can't be found after the move.
- `rehearse` practises the whole move on one computer without changing anything.
- A numbered menu when you double-click it.

### _Manifest
- Lists installed plugins on Mac and Windows, flags 32-bit, Intel-only, duplicate and badly signed plugins, and can run a virus scan.
- Compares two computers and gives a reinstall checklist grouped by maker.

### _Crate
- Sorts drum kits and sample packs into one library by genre, pack and sound type, skipping duplicates.
- Uses links, so the library takes no extra space; `undo` removes it without touching your originals.

### Known problems
The fixes planned before the public beta are listed in the
[M1 milestone](https://github.com/chasubowl-creator/freighter/milestone/2).
