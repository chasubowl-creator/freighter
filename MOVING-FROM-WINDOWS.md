# Moving your studio with Freighter

**Plugins move as a list** (you reinstall them from each maker).
**Your own files move as files**: projects, presets, recordings, and every sample your projects use.
You need an external drive with enough free space (step 2 shows how much).

## 1. Get Freighter on both computers

From **https://github.com/chasubowl-creator/freighter/actions**, open the newest run with a green check and
download **freighter-windows** (or **freighter-mac** for a Mac). Unzip it. You get `freighter` and `manifest`.
No other installs needed. Windows may say "Windows protected your PC": click **More info → Run anyway**.

## 2. On the old computer

Double-click **freighter**. In the menu:

- **2: See what a move would take.** It finds your FL Studio and Ableton folders, plus every folder your
  projects pull samples from, such as a drum kit folder on the Desktop. Note the total size.
- **4: Pack for the move onto a drive.** Plug in the drive, then drag it into the window when asked.
  If it gets interrupted, choose 4 again. It picks up where it stopped.

Installers and programs found in sample folders are left behind on purpose, and listed in `left-behind.txt`.

## 3. Before you wipe or sell the old computer

- [ ] **Deactivate plugin licenses** in iLok License Manager, Waves Central, Native Access, Arturia
      Software Center and any others, so they free up for the new computer.
- [ ] Samples on another drive that step 2 didn't show? Run `freighter pack --to E:\ --include "D:\Samples"`.

## 4. On the new computer

Double-click **freighter**:

- **5: Unpack a bundle from the drive.** Drag the `Freighter-...` folder from the drive into the window.
  FL Studio and Ableton folders go where the new computer expects them, and sample folders go to
  `Music/Freighter/<old computer's name>/`. Every file is checked against the original as it's copied.
- **6: Repair projects after unpacking.**
  - **Ableton Live 11 and 12:** sample paths are rewritten to the new locations. The original of each
    changed project is kept in a `Freighter backup` folder next to it.
  - **Ableton Live 10 or older:** open the project, then File > Manage Files > Locate.
  - **FL Studio:** Freighter lists the folders to add in FL under Options > File settings > Browser extra
    search folders. FL then finds the moved samples. Save each project afterwards.

## 5. Reinstall plugins

Run `manifest --compare "Music/Freighter/<old computer's name>/plugins-<old computer's name>.csv"`.
The report lists every plugin from the old computer that isn't installed yet, grouped by maker.
Run it again after installing to watch the list shrink.

## 6. Check before you're done

- [ ] Open 2–3 important projects and play them through.
- [ ] Only then wipe the old computer.
