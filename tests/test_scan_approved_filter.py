from app.approval_matching import approval_key, apply_approvals


def test_apply_approvals_matches_exact_video_cell_and_analysis():
    approved = {
        approval_key('Camera_1_ACT1.mp4', 'A5', 'ba'): {
            'record_id': 'Camera_1_ACT1_A5_R00001',
            'date_run': '2026-07-16 16:01:53',
        }
    }
    rows = [
        {'video': '/videos/Camera_1_ACT1.mp4', 'cell': 'a5', 'analysis': 'ba'},
        {'video': '/videos/Camera_1_ACT1.mp4', 'cell': 'A5', 'analysis': 'fight'},
    ]
    apply_approvals(rows, approved)
    assert rows[0]['approved'] is True
    assert rows[0]['approved_record_id'] == 'Camera_1_ACT1_A5_R00001'
    assert rows[1]['approved'] is False
