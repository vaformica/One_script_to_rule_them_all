from pathlib import Path


def approval_key(video, cell, analysis):
    return (
        Path(str(video or '')).name.lower(),
        str(cell or '').strip().upper(),
        str(analysis or '').strip().lower(),
    )


def apply_approvals(rows, approved):
    for row in rows:
        record = approved.get(
            approval_key(row.get('video'), row.get('cell'), row.get('analysis'))
        )
        row['approved'] = bool(record)
        row['approved_record_id'] = record.get('record_id', '') if record else ''
        row['approved_date'] = record.get('date_run', '') if record else ''
    return rows
