from app.qc_rerun_matching import build_rerun_comparisons


def test_flagged_record_is_grouped_with_later_attempts():
    rows = [
        {"record_id":"vid_C1_A00001","video":"vid.mp4","cell":"C1","analysis":"BA","attempt_index":"1","qc_decision":"NEEDS RERUN","pipeline_status":"COMPLETED"},
        {"record_id":"vid_C1_A00002","video":"vid.mp4","cell":"C1","analysis":"BA","attempt_index":"2","qc_decision":"PENDING","pipeline_status":"COMPLETED"},
        {"record_id":"vid_C2_A00001","video":"vid.mp4","cell":"C2","analysis":"BA","attempt_index":"1","qc_decision":"APPROVED","pipeline_status":"COMPLETED"},
    ]
    result = build_rerun_comparisons(rows)
    assert [r["record_id"] for r in result] == ["vid_C1_A00001", "vid_C1_A00002"]
    assert result[0]["_comparison_role"] == "original"
    assert result[1]["_comparison_role"] == "later"


def test_flagged_without_replacement_is_retained():
    rows = [{"record_id":"x","video":"v.avi","cell":"A1","analysis":"FIGHT","attempt_index":"3","qc_decision":"RERUNNING"}]
    result = build_rerun_comparisons(rows)
    assert len(result) == 1
    assert "no later attempt" in result[0]["_comparison_label"]


def test_approved_later_attempt_is_marked_ready():
    rows = [
        {"record_id":"old","video":"v.mp4","cell":"A1","analysis":"BA","attempt_index":"1","qc_decision":"NEEDS RERUN"},
        {"record_id":"new","video":"v.mp4","cell":"A1","analysis":"BA","attempt_index":"2","qc_decision":"APPROVED"},
    ]
    result = build_rerun_comparisons(rows)
    assert "may be superseded" in result[1]["_comparison_label"]
