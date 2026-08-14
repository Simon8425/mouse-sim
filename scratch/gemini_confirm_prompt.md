# Gemini Confirmation Prompt — Re-verify the Drop Animation (after fix + server restart)

You are a physics/animation debugger. I will send you a NEW screen recording of
the gaming-mouse drop-test web app. The backend was RESTARTED with a fixed
engine, and the debug overlay was fixed to stop reporting fake motion during
inter-drop gaps. **I need you to confirm the fix works** by reading the exact
on-screen PHYSICS DEBUG overlay numbers — and to catch any REAL remaining bug.

IMPORTANT CONTEXT (what changed):
- The previous recording you analyzed was made against a STALE server that was
  still running the OLD physics engine (it had been started before the fix and
  never restarted). The new recording is against the current code.
- The overlay previously showed fake "velocity bursts" and "rot-rate spikes"
  during the 0.5 s pauses between drops (it interpolated across the teleport
  gap). That display bug is fixed: during a gap the overlay now shows
  v=0, rot rate=0, and the held rest pose.
- The expected current behavior for the DEFAULT test (flat drop, 0.75 m,
  concrete, 3 drops): every drop settles flat on the skates within ~1.5 s,
  2 impacts each, ~0.5 s pause between drops, total playback ~4.3 s,
  NO DROP_SIM_DID_NOT_SETTLE warnings.

## What to verify (read the overlay numbers)

1. **DROP 1**: impact time/speed, then the settle phase (~0.5 s after impact):
   does the model settle FLAT (up on the skates, not tilted)? What are
   rot rate and Δq/frame at rest? What is the drop table's settled flag and
   settled_s?
2. **DROP 2 and DROP 3**: same — settle flat, 2 impacts each, settled=YES?
3. **Pacing**: between drops, is the pause ~0.5 s (motion stop → next drop
   start)? Does the model teleport to the top and FALL for every drop? Does
   the overlay show v=0 / rot rate=0 during the pause (the fixed gap hold)?
4. **Warnings**: any DROP_SIM_DID_NOT_SETTLE or other CHECKS in the overlay or
   right rail?
5. **Total playback**: roughly 4-5 s, ending with the model frozen flat?

## Report format

```
DROP 1: settled=YES/NO, settled_s=..., impacts=..., rest orientation=flat/tilted/...
DROP 2: ...
DROP 3: ...
PACING: drop1->drop2 gap=...s, drop2->drop3 gap=...s, all drops fell? yes/no
CHECKS: none / <list>
TOTAL: last t=...s
VERDICT: FIX CONFIRMED (all drops settle flat, clean pacing, no warnings)
         or <specific remaining issue with numbers>
```

Be quantitative. If the model does NOT settle flat, or a drop does NOT fall,
capture the exact t, pos z, rot rate, Δq, and the drop table numbers at that
moment. Do not report the old "24 s / 28 impacts / 5.2 m/s" numbers unless you
actually see them in THIS recording — that was the stale server.
