from app.approval_matching import approval_key, apply_qc_statuses, normalize_qc_status


def test_apply_qc_statuses_attaches_needs_rerun():
    records = {
        approval_key('Camera_1_ACT1.mp4', 'A5', 'ba'): {
            'record_id': 'Camera_1_ACT1_A5_R00001',
            'date_run': '2026-07-16 16:01:53',
            'qc_decision': 'RERUN',
            'notes': 'Track crossed wall',
        }
    }
    rows = [
        {'video': '/videos/Camera_1_ACT1.mp4', 'cell': 'a5', 'analysis': 'ba'},
        {'video': '/videos/Camera_1_ACT1.mp4', 'cell': 'A5', 'analysis': 'fight'},
    ]
    apply_qc_statuses(rows, records)
    assert rows[0]['qc_status'] == 'NEEDS RERUN'
    assert rows[0]['qc_record_id'] == 'Camera_1_ACT1_A5_R00001'
    assert rows[0]['qc_notes'] == 'Track crossed wall'
    assert rows[0]['approved'] is False
    assert rows[1]['qc_status'] == 'UNSCORED'


def test_qc_aliases_are_normalized():
    assert normalize_qc_status('done') == 'APPROVED'
    assert normalize_qc_status('rerun') == 'NEEDS RERUN'
    assert normalize_qc_status('fixed') == 'SUPERSEDED'
