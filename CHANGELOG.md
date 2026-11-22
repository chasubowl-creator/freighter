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

### Fixed before the beta (milestone M1)
- Finds Desktop, Documents and Music where Windows really keeps them, including OneDrive, and warns about cloud-only files.
- Packs samples that sit at the top of a drive, projects that use no outside samples, and never packs the User Library twice.
- Handles paths past Windows' 260-character limit and network drives.
- FAT32's 4 GB limit, clashing file names, Mac `._` files and read-only drives are handled up front.
- Checks free space before unpacking or building a copied Crate library.
- Matches accented file names between Mac and Windows.
- Lists samples that were already missing before the move.
- Rehearse no longer leaves the menu pointed at the rehearsal folder; errors keep the window open and save a crash report.
- Reports and rehearsals go to Music/Freighter; leftover rehearsals are never packed.
- Ableton repairs are written safely; bundles from a newer Freighter are refused with a clear message.

### Never a wrong result (milestone M1b)
- Resuming a pack can no longer mix up folders; same-named folders land apart; bundles are checked as a whole.
- Programs and app folders are never packed as samples; demo projects in sample packs don't pull in whole libraries.
- A safety net checks that every sample a project uses is in the bundle; the rehearsal is stricter and compares contents.

### Projects open by themselves (milestone M2b, in progress)
- `fix-fl` repairs FL Studio projects by rewriting their sample paths (backups kept, nothing changed while FL is open).
- Windows → Windows and Mac → Mac moves put folders back in the same places.
- FL Studio's browser folders, user-data folder and recent projects are read and moved on purpose.
- `freighter plugins` (menu option 7) shows which plugins your projects need and which to install first.

### Freighter in your web browser (milestone M3, in progress)
- `freighter app` (or menu option 9) opens a private local page that runs a whole move:
  - **Old computer:** see what would move and untick what you don't want, pick a drive (with warnings for FAT32,
    read-only drives and not enough room), pack with progress and cancel, then a license-deactivation checklist.
  - **New computer:** pick a bundle found on your drives, choose "back where they were" on a same-OS move, unpack and
    repair in one go, then see where everything went and which FL browser folders to add back.
  - **Plugins:** which plugins your projects need, most useful first, with each maker's website; tick them off and
    check again.
  - **Crate:** pick sample folders (FL's browser folders are suggested), preview the sort by genre, build the library
    of links, open it, or remove it.
- Unpacking can be previewed (`unpack --preview`, and on the app's confirm screen) and undone (`undo-unpack`, and a
  button on the app's results page): undo removes exactly what the unpack created and puts repaired projects back,
  keeping anything changed since. Unpacking a newer bundle replaces files an earlier unpack put there, unless you
  changed them.
- The app can **rehearse** too: pick what moves and where, then see PASS or FAIL with every lost, already-missing
  and unchecked sample, and delete the rehearsal's files in one click (`rehearse --clean` on the command line).
- The home screen lists what's already on this computer: earlier unpacks (repair again, undo) and leftover
  rehearsals (delete), even after Freighter was closed.
- Copies show files done and a time-left guess; long jobs show which step they're on.
- The plugin checklist links to about 75 makers' sites, and offers a web search for any other maker.
- Faster (issue #74): on a 30,000-sample test studio, planning takes half the time, packing about 40% less, and
  unpacking again no longer re-reads files it already copied. Results are byte-for-byte the same.
- **Plugin presets move too** (issue #31): folders in Documents and Music (and the Mac's Audio Presets folders or
  Windows' Public Documents) named after an installed plugin's maker, or holding preset files, such as Serum's
  `Xfer` folder. Same-OS moves put them back in place; across Windows and Mac the results say where they are.
  Folders over 10 GB (probably sound libraries) are offered, not packed.
- **Ableton racks and clips** (.adg, .alc) are read when packing, repaired after unpacking and checked by the
  rehearsal (issue #32). Their originals are kept where Live's browser won't list them. Live's installed Packs
  never move: they reinstall from Ableton.
- On Windows, USB drives that call themselves "fixed" are no longer called the computer's own disk; a typed `E:`
  means the top of E:, and Explorer's "Copy as path" quotes are fine.
- The computer stays awake while packing and unpacking. A rehearsal always tests the FL repair, even with FL open.
- When Windows or macOS refuses a write, the results say what to allow.

- **Bring only some projects** (issue #76): choose a time range (in the app, the menu, or `--since/--until`)
  and only the projects last saved in it move. Each sample they use brings its whole kit, never the folder of
  other kits above it; sample libraries are offered with their size instead of packed whole. Projects left
  behind are listed in the bundle.

- **Sound libraries** (issue #71): Kontakt / Native Instruments, Omnisphere's STEAM folder, Spitfire, UVI,
  EastWest and Toontrack libraries are found on any drive, listed with their size, and taken only when ticked
  (`--include` on the command line). After unpacking, each one you took comes with its maker's steps to reconnect
  it, and the rest are listed to install again from the maker's app.
- **Report a problem** (issue #41): in the app's header, the menu (`r`) and on any error. It opens a short GitHub
  form with only the version and kind of computer filled in.
- **Check for updates** (issue #40): on the app's home screen, the menu (`u`) or `freighter check-update`. It asks
  GitHub only when clicked.

### Downloads
- Each version tag publishes a GitHub Release with the Windows and Mac downloads, which need no GitHub account
  (issue #36). The Mac programs now keep their "executable" permission, so they start after unzipping.

### Known problems
The fixes planned before the public beta are listed in the
[M1 milestone](https://github.com/chasubowl-creator/freighter/milestone/2).
