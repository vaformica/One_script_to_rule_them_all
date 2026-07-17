from pipeline.run_metadata import RunMetadata


def test_columns():
    m = RunMetadata(
        run_index=1,
        run_timestamp="2026-07-15 12:00:00",
        analysis_type="ba",
        video_path="/x/v.mp4",
        video_filename="v.mp4",
        toml_source_path="/x/a.toml",
        toml_run_copy_path="/r/a.toml",
        cell_label="A1",
        remote_run_dir="/r",
    )
    assert m.csv_columns()["pipeline_run_index"] == 1
    assert "Attempt 00001" in m.png_label()
