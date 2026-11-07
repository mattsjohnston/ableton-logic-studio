# Spike 6: setlist structure and performance (desk research, 2026-11-07, mostly UNVERIFIED)

Sources were thin. Most claims below are practitioner lore or inference and need testing on our rig.

**Verified (docs/forums):** Live opens one Set at a time, and there is no gapless switching between Sets. Each scene can carry its own tempo and time signature, applied when the scene launches. Scene follow actions exist. Clips can send Bank/Program Change when launched. The chain selector gates note-ons only (CC/PC still reach every chain). AbleSet (a third-party setlist app) runs as a companion to Live, which breaks our "show runs without custom code" rule unless you decide it's acceptable.

**The crux is the plugin count.** 25 songs × ~6 Analog Lab instances is ~150 instances in one big Set. Each instance holds RAM even when idle. Options:
- **A. One Set per song:** simplest to generate, and each song is isolated. The cost is a load gap between songs (likely 5–30 s, not measured).
- **B. One Set for the whole show with a shared pool of ~4–6 Analog Lab tracks:** presets switch per song via Program Change clips or stored chains. Gapless, but it depends on Analog Lab responding to Program Change (unverified) or on rack chains (RAM again).
- **C. Hybrid:** one Set per show block (e.g. 3 Sets of 8–10 songs), with the gaps placed where the band talks anyway.

**Measurement spike (needs a Live-saved Analog Lab block):** generate test Sets with 6/30/90 Analog Lab instances and measure load time, RAM and idle CPU. Also test Program Change preset switching and held notes at a chain switch. That decides between A, B and C on evidence.

Working defaults (same in every option): printed click + spoken cues on the cue output (3-4), scene tempo per section, 1-bar launch quantize, per song one vamp/loop scene + one jump-to-outro/ending scene + a STOP scene, APC mini/Launchpad + foot switch for next scene / stop all, and a stereo phone fallback (stems minus live parts, with click on one side) for the whole song.
