# v0.9.5 — Fight sides and cleaner assay outputs

Changes relative to the confirmed v0.9.4 baseline:

- Adds `horizontal_side_mean_x` and `mean_x_position_px` to each fight individual-summary row.
- Adds explicit left/right animal index and role columns to fight pair summaries.
- Defines left/right from mean valid X position across the analyzed window.
- Burns the run name directly beneath the track legend on every PNG and every PDF page.
- Prevents fight-only runs from creating BA summary files.
- Prevents BA-only runs from creating combat summary files.
