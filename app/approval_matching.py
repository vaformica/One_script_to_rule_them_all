from pathlib import Path


QC_STATUS_ALIASES = {
    'DONE': 'APPROVED',
    'RERUN': 'NEEDS RERUN',
    'FIXED': 'SUPERSEDED',
}


def approval_key(video, cell, analysis):
    return (
        Path(str(video or '')).name.lower(),
        str(cell or '').strip().upper(),
        str(analysis or '').strip().lower(),
    )


def normalize_qc_status(value):
    status = str(value or 'UNSCORED').strip().upper()
    status = QC_STATUS_ALIASES.get(status, status)
    return status or 'UNSCORED'


def apply_qc_statuses(rows, qc_records):
    """Attach the latest QC decision for each video/cell/analysis subject."""
    for row in rows:
        record = qc_records.get(
            approval_key(row.get('video'), row.get('cell'), row.get('analysis'))
        )
        status = normalize_qc_status(record.get('qc_decision')) if record else 'UNSCORED'
        row['qc_status'] = status
        row['qc_record_id'] = record.get('record_id', '') if record else ''
        row['qc_date'] = record.get('date_run', '') if record else ''
        row['qc_notes'] = record.get('notes', '') if record else ''
        # Retain the old fields for compatibility with code and saved indices.
        row['approved'] = status == 'APPROVED'
        row['approved_record_id'] = row['qc_record_id'] if row['approved'] else ''
        row['approved_date'] = row['qc_date'] if row['approved'] else ''
    return rows


def apply_approvals(rows, approved):
    """Backward-compatible helper for maps that contain approved records only."""
    normalized = {}
    for key, record in approved.items():
        item = dict(record)
        item.setdefault('qc_decision', 'APPROVED')
        normalized[key] = item
    return apply_qc_statuses(rows, normalized)
