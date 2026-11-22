# Testing Freighter on your Windows PC (about 20 minutes)

Nothing here changes your music files. Freighter only **reads** them, and writes its reports and copies
into `Music\Freighter` in your user folder, which you can delete at the end. No Python or other installs needed.

---

## Step 1: Download the program (3 min)

1. On the PC, open **https://github.com/chasubowl-creator/freighter/actions** (no account needed)
2. Click the newest run with a **green check**.
3. Scroll to **Artifacts** at the bottom and click **freighter-windows**. A zip downloads.
4. In Downloads, right-click **freighter-windows.zip** → **Extract All… → Extract**.
5. Move the extracted folder to your **Desktop**. Inside are `freighter.exe` (the mover), `manifest.exe`
   (the plugin list), `crate.exe` (the sample sorter) and this guide. Always run them from the extracted
   folder, not from inside the zip.

## Step 2: Open it (1 min)

Double-click **freighter.exe**.

Windows will probably show **"Windows protected your PC"**. That's because Freighter isn't paid-signed
yet, not because anything is wrong. Click **More info → Run anyway**. Rarely, antivirus wrongly flags
programs packaged this way. If that happens, take a photo of the warning and send it rather than
forcing it.

A window opens with a numbered menu.

## Step 3: Scan your plugins (2 min)

Type **1** and press Enter. Your plugin report opens in the browser. Check:
- Are your main plugins listed?
- Look at the **Safety check** section. **Signature broken** means a plugin's files were changed after
  its maker signed them. Note any of those.

Press Enter to go back to the menu.

## Step 4: See what a move would take (1 min)

Type **2** and press Enter. It lists everything Freighter would move, with a **total** at the bottom.
- Are your FL Studio projects and sample folders there?
- Anything missing, like samples on another drive? Note it down. That's useful feedback.
- Read any notes under the list:
  - **"already missing"**: samples your projects use that aren't on the PC any more (maybe an unplugged drive).
  - **OneDrive**: files that only live in the cloud and would download while packing.
  - **"come with installed programs"**: samples from FL Studio's or Ableton's own sounds. These are fine;
    they come back when you install the program on the new computer.
  Take a photo of these notes.

## Step 5: Rehearse the move (5–20 min)

The rehearsal needs **free space of about twice the total** from Step 4. To check, open File Explorer →
**This PC** and look at the C: drive.

Type **3** and press Enter. At the end it prints either:
- **PASS:** every sample your projects use survived the move, or
- **FAIL:** lists the samples it lost. That's a Freighter bug, and exactly what we're looking for.

**If C: is too small,** use the command version instead, pointing at an external drive (see "Command
version" below): `freighter.exe rehearse --to E:\`

## Optional: try the new app window (5 min)

Type **9** and press Enter. Freighter opens in your web browser. Choose **Start packing** to see the same list as
Step 4 with tick boxes, or **Check my plugins**. You don't need to pack anything; close the tab and the window when
you're done. Screenshots of anything confusing are very welcome.

## Step 6: Send the results back

Send these back (Google Drive or email both work). The window shows where each one was saved:
- `rehearsal-report.txt`, in `C:\Users\<you>\Music\Freighter\Rehearsal` (or in `E:\Freighter Rehearsal` if you
  rehearsed onto another drive)
- `plugins.csv`, in the `manifest-...` folder inside `C:\Users\<you>\Music\Freighter\Reports`
- `freighter-crash.txt`, if Freighter saved one in `Music\Freighter`
- a photo of anything in the window that looks like an error

## Step 7: Clean up

Delete the **Rehearsal** folder in `Music\Freighter` (or `Freighter Rehearsal` on the other drive). Your real
files were never changed.

---

## Command version (for more options)

In the folder with freighter.exe, click the address bar, type `cmd`, press Enter, then for example:

    freighter.exe pack --list
    freighter.exe rehearse --to E:\
    freighter.exe rehearse --skip used-3fa2b1c9      (use an ID from the pack --list output)
    manifest.exe --extra-dir "D:\VST"
    manifest.exe --virus-scan

`--virus-scan` also checks every plugin with Microsoft Defender (slower).

**Stuck?** Take a photo of the window and send it. Error messages are the most useful thing.
