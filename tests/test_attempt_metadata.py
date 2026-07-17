import json
from pipeline.run_metadata import RunMetadata

def test_attempt_index_is_used_in_new_identifier(tmp_path):
    m = RunMetadata(run_index=3, attempt_index=3, run_timestamp='2026-07-16 12:00:00', analysis_type='ba', video_path='/x/v.mp4', video_filename='v.mp4', toml_source_path='/x/a.toml', toml_run_copy_path='/r/a.toml', cell_label='C5', remote_run_dir='/r')
    assert '_A00003_' in m.identifier()
    assert m.csv_columns()['pipeline_attempt_index'] == 3

def test_legacy_json_gets_attempt_index(tmp_path):
    data = dict(run_index=2, run_timestamp='2026-07-16 12:00:00', analysis_type='fight', video_path='/x/v.mp4', video_filename='v.mp4', toml_source_path='/x/a.toml', toml_run_copy_path='/r/a.toml', cell_label='A1', remote_run_dir='/r')
    p=tmp_path/'run_metadata.json'; p.write_text(json.dumps(data))
    m=RunMetadata.from_json(p)
    assert m.attempt_index == 2
