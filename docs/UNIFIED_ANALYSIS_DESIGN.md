# Unified BA and Fight Post-processing Design

## Why the newer fight analyzer is the shared base

The uploaded fight analyzer already contains the newer shared behavior-analysis
features used for both animals:

- trajectory discovery and shape normalization
- JSON and TOML metadata discovery
- ROI parsing
- artifact-jump interpolation
- sustained movement onset
- ROI and wall-buffer metrics
- turtling detection
- per-frame kinematics
- BA-style ROI-local track maps
- start, end, sustained-onset, interpolated, and turtling markers

Those shared operations now run once in
`analysis/analyze_idtracker_unified.py`.

## Automatic mode selection

- one animal: behavioral assay
- two or more animals: fight

Mode can also be forced with:

```bash
--analysis-type ba
--analysis-type fight
```

## Shared track-map stream

Both assays now call the same `plot_tracks()` implementation.

BA produces:

- one combined/individual track map
- one PDF
- individual movement/ROI/turtling summary
- per-frame kinematics
- turtling events

Fights produce the same individual products plus:

- combined two-animal map
- one map per animal
- pair summary
- pairwise per-frame data
- contact events
- possible-fight events
- InqScribe file

## Uploaded originals

Exact copies of the supplied scripts are preserved in:

```text
analysis/legacy_uploaded_20260715/
```

They are retained for scientific comparison and regression testing, but the
pipeline does not execute them.

## Validation requirement

The unified script compiles, but scientific equivalence must be tested against
known BA and fight sessions before large-scale use. Compare:

- movement and ROI summaries
- turtling metrics
- fight pair/contact metrics
- frame windows
- map geometry and markers
- merged batch row counts
