# v0.9.7 — Robust starting sides and fight interaction maps

- Starting left/right assignment now uses the first 30 **valid tracked positions** after analysis begins. Leading IDtracker NaNs no longer force `unknown` when a visible start position exists.
- Adds starting-position QC fields recording the first valid local frame and number of valid positions used.
- Fight PDFs add an interaction-location page. Small red stars mark the midpoint between beetles on frames meeting the contact threshold.
- Fight PDFs also add an experimental 3D page with X and Y in space and elapsed analysis time on the Z axis.
- The collector preserves the analysis-generated multipage fight PDF rather than rebuilding a reduced PDF from PNG files.
- BA outputs are unchanged.
