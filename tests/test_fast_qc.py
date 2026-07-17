import csv
from pathlib import Path
from collector import qc_manager


def test_fast_status_updates_index_without_building_masters(tmp_path, monkeypatch):
    root = tmp_path
    qc = root / "QC"
    qc.mkdir()
    run_dir = root / "runs" / "r1"
    run_dir.mkdir(parents=True)
    with (qc / "run_status.csv").open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=qc_manager.QC_FIELDS)
        writer.writeheader()
        writer.writerow({
            "record_id": "rec1", "run_index": "1", "video": "video.mp4",
            "cell": "A1", "analysis": "ba", "pipeline_status": "COMPLETED",
            "qc_decision": "PENDING", "run_dir": str(run_dir),
        })

    called = {"rebuild": 0, "annotate": 0}
    monkeypatch.setattr(qc_manager, "rebuild", lambda root: called.__setitem__("rebuild", called["rebuild"] + 1))
    monkeypatch.setattr(qc_manager, "annotate_run_summaries", lambda *args: called.__setitem__("annotate", called["annotate"] + 1))

    result = qc_manager.set_status(root, "rec1", "APPROVED", fast=True)
    rows = qc_manager.read(qc / "run_status.csv")
    assert rows[0]["qc_decision"] == "APPROVED"
    assert result["fast"] is True
    assert called == {"rebuild": 0, "annotate": 0}
