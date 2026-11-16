# Setting up Ableton Logic Studio on your Mac

This turns the band's Logic songs into Ableton Live sets you can rehearse and play. Takes about 10 minutes, once.

## You need
- **macOS** with **Logic Pro**, **Ableton Live 12 Suite** (the normal release is fine) and **Arturia Analog Lab V** installed.
- A **GitHub** account with access to this repo, plus a way to download it. Easiest: ask Claude Code *"clone mattsjohnston/ableton-logic-studio into ~/Dev and set it up"*. It will follow `CLAUDE.md`.

## 1. Get the code
```bash
cd ~/Dev && gh repo clone mattsjohnston/ableton-logic-studio && cd ableton-logic-studio
```
(No `gh`? `git clone https://github.com/mattsjohnston/ableton-logic-studio.git`. If macOS asks to install "command line developer tools", say yes.)

## 2. Run the installer
Double-click **`Setup.command`** in Finder, or run `./Setup.command`. It:
- finds Python 3.11+, or installs it with *uv* if you don't have one,
- installs the project's packages into a private `.venv` folder (nothing system-wide),
- offers to install **AbletonOSC**, which lets the web page play sections in Live. Say yes,
- runs a check (`doctor`) and lists anything missing.

If macOS says the file "can't be opened", right-click it → **Open** → **Open**.

## 3. One-time settings in Live
1. **Restart Live**, then **Live → Settings → Tempo & MIDI**:
   - **Control Surface 1 → AbletonOSC** (leave Input/Output as *None*). The status bar says "AbletonOSC: Listening…".
   - Under **Input Ports**, turn on **Track** for each keyboard and the e-kit you play live.
2. **Live → Settings → Plug-Ins**: turn on **Use Audio Units v2** (Analog Lab is loaded as AU) and click **Rescan**.
3. **Live → Settings → Audio**: pick your interface. Outputs **1/2** go to the PA, **3/4** to the in-ears (click and spoken cues).

## 4. Use it
Double-click **`LLS.command`**. The app opens at http://127.0.0.1:8765. The **?** button explains the workflow:
1. **Export from Logic**, working on a *copy* of the song: *File → Export → All Tracks as Audio Files* (WAVE 24-bit, Normalize off, Bypass Effect Plug-ins off, Include Volume/Pan Automation on, *Extend File Length to Project End*), plus *File → Bounce → Project* for the full mix. For reverb/delay returns: Mixer → select the aux strips → *Create Tracks for Selected Channel Strips*, then export those tracks too (name them without "/").
2. **Import** (song menu → **+ New song**): pick the `.logicx` and the export folder.
3. **Arrange / Lineup**: name sections, choose who plays what (drag parts onto performers), and edit `band.json` in the **Band** tab.
4. **Build → Open in Live** to rehearse. **Setlists → Build show** for the gig (one Live set per song plus phone fallback mixes).
5. **Send a song to the other Matt**: song menu → **Export song package…** gives you a `.llsong` file. He uses **Import song package…**.

## If something's wrong
- Run `./.venv/bin/python -m lls doctor`, or open Claude Code in this folder and say *"run the doctor and fix what it finds"*.
- Live won't open a built set → update Live to the latest 12.x.
- Analog Lab parts are silent → check step 3.2 (AU on, rescan).
- The page says "Live not connected" → step 3.1 (AbletonOSC as Control Surface) and Live must be running.
