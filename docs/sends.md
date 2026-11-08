# Aux returns and sends (lls/sends.py)

Logic stems omit the shared aux sends (Bus 1 Big Plate 4.4 s, Bus 2 Vocal Plate 1.1 s, Bus 3 Hall 3.9 s, Bus 4 Room 0.4 s). `song.json` option `"returns": true` (default) builds one Live Reverb return per aux (cloned from the template Live-saved Reverb, DecayTime/PreDelay/RoomSize/ShelfHiGain set) plus the template "B Delay", gives every track one TrackSendHolder per return, and sets sends by role.

- **Not decoded from Logic.** The AuCO strip record has only 0/1 flag words after fader/pan/IO; the Smart Controls bplists mentioning "isSendMuteMapping" are not send levels. Levels are heuristic: vocal -12 dB to Vocal Plate; pad/lead/keys/fx -15 dB to Hall; drums -18 dB to Room; bass none; live vocal input -12 dB to Vocal Plate.
- Send Manual is linear gain, 0.000316 (-70 dB, off) to 1. TrackSendHolder and SendsPre ids are list-local 0..n-1; their Automation/Modulation targets are renumbered globally.
- Unverified in a running Live: that the returns load and sound right.
