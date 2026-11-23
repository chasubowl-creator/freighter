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

- **Check the drive** (issue #77): after packing, read every file back and compare it with the original, so a
  bad drive or cable is caught while the old computer still has your files. Damaged copies are removed and packing
  again copies just those. In the app, the menu and `freighter verify`.
- **Check my projects** (issue #78): every project on the new computer and whether it's ready (every sample
  found, every plugin installed), problems first. In the app, menu option 7 and `freighter health`.
- **Plugins by app** (issue #79): the plugins to install are grouped by the app each maker uses (Native Access,
  Waves Central, Arturia Software Center, Plugin Alliance…), most-used first.
- **Plugins that are only files come along** (issue #80): on a Windows-to-Windows or Mac-to-Mac move, plugins from
  makers without an installer app (most free and indie plugins) are packed and go back in the same plugin folder,
  so the new computer needs no download for them. Plugins that install through an app, use iLok, or look cracked
  stay on the install list. Program Files needs Windows' permission once; a plugin already installed is never
  replaced, and undo removes them.
- **Copied plugins are checked for malware** (issue #81), in four checkpoints:
  1. Installers and programs found in sample folders stay behind (as before). That's how most malware in
     cracked software travels.
  2. On the old computer, a plugin whose files changed after its maker signed them isn't copied, and Microsoft
     Defender (or ClamAV, if installed) scans the rest before packing. Originals are never touched.
  3. On the new computer, copied plugins wait in a holding folder until the virus scanner there has checked
     them. Anything it flags never reaches a plugin folder. On a Mac, plugins Apple hasn't checked are marked
     like a download, so macOS scans each one the first time a DAW loads it (Open Anyway once).
  4. Calm wording: "not copied, for safety", with the reason and "install a fresh copy from its maker". The
     plugin report says "Changed after signing" instead of "Signature broken".
- **Plugins to install** (issue #82): the app's plugins page is now one clean checklist of the plugins Freighter
  couldn't bring, by brand. Each brand opens to show how its plugins install (Native Access, Waves Central,
  Arturia Software Center and 20 more, checked links), its plugins with a type and how many projects use them, and
  a link to each one on its maker's site. Tick plugins off, take ones you don't need off the list, or press Check
  again to tick off whatever is now installed. Ticks are kept in Music/Freighter. No pictures and nothing loaded
  from the internet. Slate Digital's installer is now called Complete Access Hub.
- **A new look, matching the website with an early-internet feel**: the app is a "_freighter.exe" window over a
  photo of green hills, in the website's fonts (Sora, Inter, JetBrains Mono), with a status bar, a blinking cursor,
  pixel icons, and a segmented progress bar. The website's pixel courier waits by the new computer and carries
  crates across the hills while Freighter works. The photo and fonts are bundled (credits in assets/CREDITS.md),
  so the app still never loads anything from the internet.
- **`freighter selftest`** (issue #83): checks a copy of Freighter works as built (its photo and fonts, the app
  page, and a small move with a plugin, then undo). The build runs it on Windows and Mac; on Windows it also
  puts a plugin into Program Files and back out, and times a Microsoft Defender scan.
- **First-run steps** (issue #39): every download has a `START HERE.txt` (unzip, get past "Windows protected your
  PC" or macOS's first-run block, open the menu or the app), and the README has the same steps plus what Freighter
  touches and what it never does.
- **Intel Macs** (issue #37): releases now have `freighter-mac-intel.zip` next to the Apple Silicon download. Both
  Mac zips are made the way Finder makes them, so the programs keep their permission to run; the build checks
  each one is for the right chip and signed (ad hoc: notarizing needs an Apple developer account).

### Downloads
- Each version tag publishes a GitHub Release with the Windows and Mac downloads, which need no GitHub account
  (issue #36). The Mac programs now keep their "executable" permission, so they start after unzipping.

### Known problems
The fixes planned before the public beta are listed in the
[M1 milestone](https://github.com/chasubowl-creator/freighter/milestone/2).
