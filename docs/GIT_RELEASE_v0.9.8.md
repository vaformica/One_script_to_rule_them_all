# v0.9.8 — idTracker-style 3-D fight tracks

This release refines the fight-only 3-D QC page to resemble the classic idTracker
space-time trajectory display.

## Changes

- Retains animal identity colors for the full 3-D trajectories.
- Uses an oblique corner view with a visibly vertical time axis.
- Draws the primary and secondary ROI outlines on the z=0 floor.
- Uses a taller 3-D plotting box so temporal structure is easier to see.
- Preserves start and end symbols in each animal's color.
- Preserves red interaction stars at their x-y position and time.
- Breaks trajectories across missing positions and implausibly large jumps.
- Keeps the 2-D interaction hotspot page as the primary fight QC view.

The 3-D page is fight-only. BA output remains unchanged.
