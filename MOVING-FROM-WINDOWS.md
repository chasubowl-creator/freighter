# Moving your studio with Freighter

**Plugins move as a list** (you reinstall them from each maker).
**Your own files move as files**: projects, presets, recordings, and every sample your projects use.
You need an external drive with enough free space (step 2 shows how much).
Format it as **exFAT**, which both Windows and Mac can read and write. NTFS drives are read-only on a
Mac, and FAT32 can't hold files over 4 GB. To format: Windows, right-click the drive → Format → exFAT;
Mac, Disk Utility → Erase → ExFAT. Formatting erases the drive.

## 1. Get Freighter on both computers

From **https://github.com/chasubowl-creator/freighter/releases**, download **freighter-windows.zip** (or
**freighter-mac.zip** for a Mac) from the newest release. No sign-in needed. Unzip it (on
Windows: right-click → Extract All; don't run it from inside the zip). You get `freighter` (the mover),
`manifest` (the plugin list) and `crate` (the sample sorter). No other installs needed. Windows may say "Windows protected your PC": click **More info → Run anyway**. A Mac blocks it the first
time: open **System Settings → Privacy & Security**, scroll down and click **Open Anyway**.

## 2. On the old computer

Double-click **freighter**. In the menu:

- **2: See what a move would take.** It finds your FL Studio and Ableton folders, every folder your
  projects pull samples from (such as a drum kit folder on the Desktop), and your plugin presets (such as
  `Documents\Xfer` for Serum). Very big plugin folders, usually sound libraries, are listed but left out
  unless you add them. Note the total size.
- **4: Pack for the move onto a drive.** Plug in the drive, then drag it into the window when asked.
  If it gets interrupted, choose 4 again. It picks up where it stopped. Freighter keeps the computer awake
  while it copies. When it's done, **eject the drive** before unplugging it.

Installers and programs found in sample folders are left behind on purpose, and listed in `left-behind.txt`.

### Bring only some projects (optional)

If moving everything is too much data, bring only the projects you last saved in a time range, for example
March 2024 to March 2026. In the app, choose **Only projects last saved from … to …** on the "What a move would
take" screen; in the menu, type the range when it asks **Which projects?** (for example `2024-03 to 2026-03`);
on the command line, add `--since 2024-03 --until 2026-03`.

- Projects saved outside the range stay on the old computer. The bundle lists them in `projects-left-out.txt`.
- For every sample those projects use, the **whole kit** it belongs to comes, but never the folder of other
  kits above it. For example, the "Tech House Kit" in `House`, but not the rest of `House`.
- Your sample libraries (FL's browser folders) aren't moved whole; each is listed with its size, so you can
  tick one in the app (or add it with `--include "D:\Samples"`) to take all of it.
- Your FL Studio and Ableton folders, presets and plugin presets still come, minus the old project files.

## 3. Before you wipe or sell the old computer

Keep it as it is until your projects open and play on the new one.

- [ ] **Deactivate plugin licenses**, so they free up for the new computer:
  - [ ] iLok License Manager (PACE/iLok plugins)
  - [ ] Waves Central → Licenses
  - [ ] Native Access (Native Instruments)
  - [ ] Arturia Software Center
  - [ ] Plugin Alliance Installation Manager
  - [ ] Any other maker with a license app or activation limit
- [ ] Samples on another drive that step 2 didn't show? Run `freighter pack --to E:\ --include "D:\Samples"`.

## 4. On the new computer

Double-click **freighter**:

- **5: Unpack a bundle from the drive.** Drag the `Freighter-...` folder from the drive into the window.
  FL Studio and Ableton folders go where the new computer expects them. Every file is checked against the
  original as it's copied. Sample folders:
  - **Same kind of computer** (Windows → Windows, Mac → Mac): back to the same places as before, e.g. your
    Desktop kits onto the new Desktop and `D:\Samples` onto D:, if the new computer has that drive. Projects then
    find everything without any repair. (`--keep-together` puts them all in `Music/Freighter` instead.)
  - **Windows ↔ Mac**, or a drive the new computer doesn't have: `Music/Freighter/<old computer's name>/`, and
    step 6 repairs the projects.
- **6: Repair projects after unpacking.**
  - **Ableton Live 11 and 12:** sample paths are rewritten to the new locations. The original of each
    changed project is kept in a `Freighter backup` folder next to it.
  - **Ableton Live 10 or older:** open the project, then File > Manage Files > Locate.
  - **FL Studio:** sample paths in each project are rewritten to the new locations, the same way, with the
    original kept in a `Freighter backup` folder. Close FL Studio first; Freighter won't change projects while
    it's open.

Unpacked into the wrong place, or changed your mind? `freighter undo-unpack` removes everything the unpack
created and puts repaired projects back. Files you've changed since are kept.

## 5. Reinstall plugins

Unpack prints the exact command at the end. In a command window opened in Freighter's folder it looks like:

    manifest.exe --compare "C:\Users\<you>\Music\Freighter\<old computer>\plugins-<old computer>.csv"

(on a Mac: `./manifest --compare "/Users/<you>/Music/Freighter/<old computer>/plugins-<old computer>.csv"`).
The report lists every plugin from the old computer that isn't installed yet, grouped by maker.
Run it again after installing to watch the list shrink.

## 6. Check before you're done

- [ ] Open 2–3 important projects and play them through.
- [ ] Only then wipe the old computer.
