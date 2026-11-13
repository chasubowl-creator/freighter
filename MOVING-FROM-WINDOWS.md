# Moving your music setup from the Windows PC to the Mac

**Plugins move as a list** (you reinstall the Mac version from each maker).
**Your own files move as files**: projects, presets, recordings, and the samples your projects use.
You'll need an external drive with enough free space (the `pack --list` step tells you how much).

## 1. On the Windows PC

1. Copy `rig_scanner.py` and `rig_mover.py` to the PC, into the same folder.
2. Install Python from https://www.python.org/downloads/ . On the first installer screen,
   tick **"Add python.exe to PATH"**.
3. Open the folder with the two files. Click the address bar, type `cmd`, press Enter.
4. See what will be moved and how big it is:

       py rig_mover.py pack --list

   It finds your FL Studio and Ableton folders, **plus every folder your projects pull samples
   from** (for example a drum kit folder on the Desktop). Add other folders with
   `--include "D:\Samples"`. Leave one out with `--skip used2` (use the ID from the list).
5. Plug in the drive (say it's `E:`) and pack:

       py rig_mover.py pack --to E:\

   If it gets interrupted, run the same command again. It picks up where it stopped.

## 2. Before you wipe or sell the PC

- [ ] **Deactivate licenses** in iLok License Manager, Waves Central, Native Access, Arturia
      Software Center and any others, so they free up for the Mac.
- [ ] Anything on other drives that `pack --list` didn't show? Add it with `--include` and pack again.

## 3. On the Mac

Plug in the drive. In Terminal, in the folder with the two files:

    python3 rig_mover.py unpack "/Volumes/DRIVE/RigMover-<PC name>-<date>"

FL Studio and Ableton folders go to their Mac locations. Sample folders go to
`~/Music/Rig Mover/<PC name>/`. Every file is checked against the original as it's copied.

Then repair your projects:

    python3 rig_mover.py fix-ableton
    python3 rig_mover.py check-fl

- **Ableton (Live 11 and 12):** sample paths are rewritten to the new locations. The original of
  each changed project is kept in a `Rig Mover backup` folder next to it.
- **Ableton Live 10 or older:** open the project, then File > Manage Files > Locate.
- **FL Studio:** `check-fl` lists the folders to add in FL under Options > File settings >
  Browser extra search folders. FL then finds the moved samples. Save each project afterwards.

## 4. Reinstall plugins

    python3 rig_scanner.py --compare "~/Music/Rig Mover/<PC name>/plugins-<PC name>.csv"

The report lists every PC plugin that isn't on the Mac yet, grouped by maker, with tick boxes.
Rerun it after installing to watch the list shrink.

## 5. Check before you're done

- [ ] Open 2–3 important projects on the Mac and play them through.
- [ ] Only then wipe the PC.
