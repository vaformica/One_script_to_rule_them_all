from pathlib import Path


def failure_key(video, cell, analysis):
    return (
        Path(str(video or '')).name.lower(),
        str(cell or '').strip().upper(),
        str(analysis or '').strip().lower(),
    )


def apply_failure_counts(rows, failure_records):
    """Attach per-subject failed-attempt counts to Search & Match rows."""
    for row in rows:
        rec = failure_records.get(
            failure_key(row.get('video'), row.get('cell'), row.get('analysis')),
            {},
        )
        count = int(rec.get('failed_count') or 0)
        row['failed_count'] = count
        row['latest_failed_run_index'] = rec.get('latest_failed_run_index', '')
        row['latest_failed_timestamp'] = rec.get('latest_failed_timestamp', '')
        row['latest_failed_run_dir'] = rec.get('latest_failed_run_dir', '')
        row['latest_failure_reason'] = rec.get('latest_failure_reason', '')
        row['has_failed_run'] = count > 0
    return rows
