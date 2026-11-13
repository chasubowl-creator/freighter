# Testing Freighter on your Windows PC (about 30 minutes)

Nothing here changes your music files. Freighter only **reads** them, and writes its copies into its
own folder, which you delete at the end.

---

## Step 1: Get the code (2 min)

1. On the PC, open **https://github.com/chasubowl-creator/freighter** and sign in to GitHub.
2. Click the green **`<> Code`** button, then **Download ZIP**.
3. In your Downloads folder, right-click **freighter-main.zip** and choose **Extract All… → Extract**.
4. Move the extracted **freighter-main** folder somewhere easy, like your **Desktop**.

## Step 2: Install Python (5 min)

1. Go to **https://www.python.org/downloads/** and click the big yellow **Download Python** button.
2. Run the installer. **On the first screen, tick "Add python.exe to PATH"** at the bottom.
   This is the step people miss.
3. Click **Install Now**, then **Close** when it's done.

## Step 3: Open a command window in the folder (1 min)

1. Open the **freighter-main** folder in File Explorer.
2. Click the **address bar** at the top, where the folder path is shown, type `cmd`, and press **Enter**.
3. A black window opens, already in the right folder. Check that Python works:

       py --version

   You should see `Python 3.x.x`. If you see "not recognized", re-run the Python installer and tick
   "Add python.exe to PATH".

All the commands below are typed into this black window. Press **Enter** after each one.

## Step 4: Scan your plugins (2 min)

    py rig_scanner.py

Your plugin report opens in the browser. Check it looks right: are your main plugins listed?
**Do you keep VST plugins in a custom folder** (for example `D:\VST`)? If so, run it again with that folder:

    py rig_scanner.py --extra-dir "D:\VST"

## Step 5: See what a move would take (1 min)

    py rig_mover.py pack --list

This lists everything Freighter would move, with sizes and a **total** at the bottom. Check:
- Are your FL Studio projects and sample folders there?
- Is anything missing, like samples on another drive? Note it down: that's useful feedback.

## Step 6: Rehearse the move (5–20 min)

The rehearsal needs **free space of about twice the total** from Step 5. To check free space, open
File Explorer → **This PC** and look at the C: drive.

**If there's room:**

    py rig_mover.py rehearse

**If C: is too small,** plug in an external drive (say it's `E:`) and use it:

    py rig_mover.py rehearse --to E:\freighter-rehearsal

**Or leave the biggest item out** (use an ID from the Step 5 list):

    py rig_mover.py rehearse --skip used2

At the end it prints either:
- **PASS:** every sample your projects use survived the move, or
- **FAIL:** lists the samples it lost. That's a Freighter bug, and exactly what we're looking for.

## Step 7: Send the results back

Send these to the Mac, whichever way is easiest: Google Drive, email to yourself, or a USB stick.
- `rehearsal\rehearsal-report.txt` (or `E:\freighter-rehearsal\rehearsal-report.txt`)
- the `plugins.csv` file inside the new `rig-report-...` folder
- a photo or copy of anything in the black window that looks like an error

Then tell Claude where they are. Google Drive works well, because Claude can read it directly.

## Step 8: Clean up

Delete the **rehearsal** folder (or `E:\freighter-rehearsal`). Your real files were never changed.

---

**Stuck?** Take a photo of the black window and send it. Error messages are the most useful thing.
