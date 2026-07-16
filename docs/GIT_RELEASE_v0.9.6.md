# v0.9.6 — Fight starting-side correction

Fight beetles are labeled left or right according to their starting location. By default, the code takes the median of each beetle's valid X coordinates in the first 30 analyzed frames. Lower X is `left`; higher X is `right`.

Individual fight summary columns:
- `starting_side`
- `starting_x_position_px`
- `starting_position_window_frames`

Pair summary columns:
- `animal0_starting_x_position_px`
- `animal1_starting_x_position_px`
- `left_starting_animal_index_0_based`
- `right_starting_animal_index_0_based`
- `left_starting_analysis_role`
- `right_starting_analysis_role`
- `starting_position_window_frames`
