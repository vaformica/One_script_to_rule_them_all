from app.failure_matching import apply_failure_counts, failure_key


def test_failure_key_normalizes_subject():
    assert failure_key('/x/Video.MP4', ' a5 ', 'BA') == ('video.mp4', 'A5', 'ba')


def test_apply_failure_counts_attaches_history():
    rows=[{'video':'/x/video.mp4','cell':'A5','analysis':'ba'}]
    records={('video.mp4','A5','ba'):{'failed_count':2,'latest_failed_run_index':3,'latest_failure_reason':'missing bbox'}}
    apply_failure_counts(rows, records)
    assert rows[0]['failed_count']==2
    assert rows[0]['has_failed_run'] is True
    assert rows[0]['latest_failed_run_index']==3


def test_apply_failure_counts_defaults_to_zero():
    rows=[{'video':'/x/video.mp4','cell':'A5','analysis':'ba'}]
    apply_failure_counts(rows,{})
    assert rows[0]['failed_count']==0
    assert rows[0]['has_failed_run'] is False
