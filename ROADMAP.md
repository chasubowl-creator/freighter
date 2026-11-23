# Freighter: the path to release

Every task links to its GitHub issue, where the details live (what, why, where in the code, and when it's done).
Tick the box here when the issue closes.

**Size:** S = under a day · M = a few days · L = a week or more. **Who:** Claude (code and docs) or You (decisions, testing on your machines, accounts).

## Decisions so far

- **Free.** No paid tier or subscription.
- **Open source under GPL-3.0.** The repo goes public once the history is clean.
- **1.0 is a browser-based app.** Double-click Freighter and it opens in your web browser. It runs only on your computer.
- **1.0 moves through an external drive.** Computer-to-computer over the network comes in 1.1.
- **FL Studio and Ableton Live,** on Windows and Mac, in any direction.
- **Company name: Samplistics.** The tools are _Freighter, _Manifest and _Crate. The trademark and domain checks are in M5.

## M0 · Ready to go public ([issues](https://github.com/chasubowl-creator/freighter/milestone/1))

*about 1 day.* Clean the history, license it, give it a version, and open the code.

- [x] Remove personal details from the git history (#1) · S · Claude + you
- [x] Make the repository public (#2) · S · You (Claude can flip it)
- [x] Add the GPL-3.0 license and a contributions note (#3) · S · Claude
- [x] Version number, --version and a changelog (#4) · S · Claude
- [x] Move the project into a permanent folder on the Mac (#5) · S · You choose, Claude moves

**Done when:** The repository is public under GPL-3.0, with no personal details in its history, and has a version number and changelog.

## M1 · Never lose or skip a file ([issues](https://github.com/chasubowl-creator/freighter/milestone/2))

*about 1 week.* Fix every way the mover can silently skip, lose or misplace a producer's files.

- [x] Find the real Desktop, Documents and Music folders on Windows (OneDrive) (#6) · M · Claude
- [x] Samples sitting at the top of a drive are skipped (#7) · S · Claude
- [x] Projects that use no outside samples aren't packed (#8) · M · Claude
- [x] The Ableton User Library can be packed twice (#9) · S · Claude
- [x] Long Windows paths (over 260 characters) and network drives (#10) · M · Claude
- [x] External drive problems: FAT32 4 GB limit, name clashes, ._ files, read-only drives (#11) · M · Claude
- [x] Refuse to unpack, or build a Crate library, without enough free space (#12) · S · Claude
- [x] Match accented file names between Mac and Windows (#13) · S · Claude
- [x] Check the bundle version on unpack (#14) · S · Claude
- [x] Report samples that were already missing on the old computer (#15) · S · Claude
- [x] Rehearse leaves the menu pointed at the rehearsal folder (#16) · S · Claude
- [x] Crashes close the window before anyone can read them (#17) · S · Claude
- [x] Write reports and rehearsals to a sensible, writable place (#18) · S · Claude
- [x] Make Ableton repairs safe if the computer stops mid-write (#19) · S · Claude
- [x] Tests for cross-OS moves, and run scan in CI (#20) · M · Claude

**Done when:** Every fix has a test, CI is green on Windows and Mac, and nothing is ever skipped without being reported.

## M1b · Never a wrong result ([issues](https://github.com/chasubowl-creator/freighter/milestone/8))

*about 4–5 days.* Found in the major-improvements review: ways a move could still silently mix up, miss or wrongly add files. Done before anyone relies on a real move.

- [x] Resuming a pack can put one folder's files into another (#57) · M · Claude
- [x] Moved folders with the same name land in the same place (#58) · S · Claude
- [x] Check bundles as a whole: completeness, reports, and resuming the next day (#59) · M · Claude
- [x] Never pack programs or app folders as "samples" (#60) · M · Claude
- [x] Demo projects inside sample packs pull whole libraries into the move (#61) · M · Claude
- [x] Close the remaining ways folders are missed or packed by mistake (#62) · M · Claude
- [x] Repair can pick the wrong file after a name clash on Windows (#63) · S · Claude
- [x] Safety net: every sample a project uses must be in the bundle (#64) · M · Claude
- [x] Make the rehearsal honest (#65) · M · Claude
- [x] App: one job at a time for real, and sturdier requests (#66) · S · Claude

**Done when:** Every item has a test that reproduced it, CI is green on Windows and Mac, and the real rehearsal still passes.

## M2 · Proven on real computers ([issues](https://github.com/chasubowl-creator/freighter/milestone/3))

*about 1 week, mostly you.* Run Freighter on real Windows and Mac setups, including a full move, and fix what turns up.

- [x] Tidy the tester docs (#21) · S · Claude
- [ ] Run the Windows test on your current PC (#22) · M · You
- [ ] Fix whatever the Windows test turns up (#23) · M · Claude
- [ ] Do one real move: Windows PC → drive → Mac (#24) · M · You
- [ ] Ask 2–3 producer friends to run a rehearsal (#25) · S · You

**Done when:** Zero lost samples in every rehearsal and in one real Windows → Mac move.

## M2b · Projects open by themselves ([issues](https://github.com/chasubowl-creator/freighter/milestone/9))

*about 2–3 weeks.* The product promise: projects open on the new computer without hunting for samples or plugins.

- [x] Same-places restore for same-OS moves (#67) · L · Claude
- [x] Repair FL Studio projects: rewrite sample paths in the .flp (#68) · L · Claude
- [x] Read what FL Studio and Ableton already know about your folders (#69) · M · Claude
- [x] Plugin readiness for each project (#70) · L · Claude
- [x] Sample libraries and plugin content (Kontakt and friends) (#71) · L · Claude
- [ ] Ableton: real Live 11/12 sets, Live 10 sets and racks (#72) · M · Claude

**Done when:** A same-OS move needs no repair, FL projects are repaired like Ableton's, and each project shows which plugins it still needs.

## M3 · The 1.0 app ([issues](https://github.com/chasubowl-creator/freighter/milestone/4))

*about 2–3 weeks.* Double-click Freighter and a clean app opens in the browser: old computer, new computer, plugins, Crate.

- [ ] Browser app: local server, security and shutdown (#26) · L · Claude
- [x] App: old-computer screens (scan, choose, pack) (#27) · L · Claude
- [x] App: new-computer screens (unpack, repair, results) (#28) · L · Claude
- [x] App: plugins screen with the reinstall checklist (#29) · M · Claude
- [x] Plugin maker download links and a license-deactivation checklist (#30) · M · Claude
- [x] Move third-party plugin presets (#31) · M · Claude
- [x] Repair Ableton device presets and clips (.adg, .alc) (#32) · S · Claude
- [x] FL Studio: point it at the moved sample folders (#33: guided step; projects repaired by #68) · M · Claude
- [x] App: Crate screen (#34) · S · Claude
- [x] Unpack journal, preview and undo (#73) · M · Claude
- [x] Speed for 500 GB moves (#74) · M · Claude
- [x] Move only the projects from a time range, with the kits they use (#76) · M · Claude
- [x] Check the drive before leaving the old computer (#77) · S · Claude
- [x] Project health on the new computer (#78) · M · Claude
- [x] Plugins grouped by the app that installs them (#79) · S · Claude
- [x] Copy plugins that are only files, so the new computer needs no download (#80) · M · Claude
- [x] Check copied plugins for malware on both computers (#81) · M · Claude
- [x] Plugins to install: a checklist by brand, with where to get each one (#82) · M · Claude
- [ ] Usability test with a non-technical producer (#35) · S · You

**Done when:** A non-technical producer completes a move without help.

## M4 · Ship it ([issues](https://github.com/chasubowl-creator/freighter/milestone/5))

*about 1 week.* Real downloads: tagged GitHub Releases, Mac builds that open, free Windows signing, update check, feedback.

- [x] Release builds on version tags (GitHub Releases) (#36) · M · Claude
- [x] Self-check of the built program, run by CI on Windows and Mac (#83) · S · Claude
- [ ] A Mac build that opens on every Mac (#37) · M · Claude
- [ ] Free Windows code signing (SignPath Foundation) (#38) · M · You apply, Claude wires CI
- [ ] Download and first-run instructions (#39) · S · Claude
- [x] Check for updates button (#40) · S · Claude
- [x] Feedback: issue templates and a "Report a problem" link (#41) · S · Claude

**Done when:** Pushing a version tag produces downloadable Windows and Mac builds that open with documented steps.

## M5 · Site and launch ([issues](https://github.com/chasubowl-creator/freighter/milestone/6))

*about 3–5 days.* Honest, private, live site with a download button, then the public beta and 1.0.

- [ ] Privacy note and self-hosted fonts (#42) · S · Claude
- [ ] Back up or remove the "Unsubscribe anytime" promise (#43) · S · Claude
- [ ] Download button, GitHub and contact links (#44) · S · Claude
- [ ] Make the page's claims match what the app does (#45) · S · Claude
- [ ] Small site copy fixes (#46) · S · Claude
- [ ] Lock in the Samplistics name and domain (#47) · S · You (Claude updates the site and app)
- [ ] Put the site live on Netlify (#48) · S · You
- [ ] Beta launch: email the waitlist and post to producer communities (#49) · M · You (Claude drafts)
- [ ] Release 1.0 (#50) · S · Claude + you

**Done when:** 10–20 beta testers finish moves with zero data loss, every bug is closed, and v1.0.0 is tagged.

## After 1.0 ([issues](https://github.com/chasubowl-creator/freighter/milestone/7))

*later.* What comes once 1.0 is out, starting with network transfer in 1.1.

- [ ] Network transfer: move computer to computer without a drive (1.1) (#51) · L · Claude
- [ ] Samples loaded inside FL plugins (FPC, Slicex, DirectWave) (#52) · L · Claude
- [ ] Ableton Live 10 and older projects (#53) · M · Claude
- [ ] More DAWs, picked by the waitlist (#54) · L · Claude
- [ ] Desktop app (Tauri) (#55) · L · Claude
- [ ] Decide on optional paid extras (#56) · S · You

**Done when:** Picked and ordered using beta feedback and the waitlist's DAW answers.

## How the work proceeds

1. Milestones go in order: M0, M1, M1b, then M2 (your real-computer tests) alongside M2b, then M3–M5. Your Windows test can start any time, since it only reads and rehearses; a real move should wait for M1b and the first M2b items.
1. Within a milestone, Claude tasks can run while You tasks wait on you.
2. Each fix lands as its own commit with a test, and the commit message says `Fixes #N` so the issue closes itself.
3. New problems found in testing become new issues in the milestone where they block.
