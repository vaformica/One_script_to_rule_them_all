from __future__ import annotations

import json
import os
from pathlib import Path
import getpass

import pandas as pd
import streamlit as st
from PIL import Image

from src.config import load_config, ensure_local_directories
from src.database import Database
from src.ssh import SSHClient
from src.remote_scan import scan_remote
from src.matching import match_files
from src.run_manager import create_run
from src.slurm import submit_stage
from src.results import retrieve_quick_results
from src.exports import export_all


st.set_page_config(
    page_title="IDtracker Pipeline Controller",
    page_icon="🪲",
    layout="wide",
)

st.title("IDtracker Pipeline Controller")
st.caption("Mac controller for Firebird, SLURM, run history, and track-based QC")

try:
    config = load_config()
except Exception as exc:
    st.error(str(exc))
    st.stop()

ensure_local_directories(config)
db = Database(config.database_path)
ssh = SSHClient(config.ssh_host)

if "selected_analysis_units" not in st.session_state:
    st.session_state.selected_analysis_units = []
if "last_scan_unmatched" not in st.session_state:
    st.session_state.last_scan_unmatched = []
if "current_run_id" not in st.session_state:
    st.session_state.current_run_id = None

tab_files, tab_settings, tab_status = st.tabs(
    ["1. Files & Runs", "2. Settings", "3. Status & QC"]
)

with tab_files:
    st.header("Remote files and analysis units")

    with st.expander("Connection and scan folders", expanded=True):
        c1, c2 = st.columns(2)
        c1.code(config.ssh_host, language=None)
        c2.code(config.remote_project_root, language=None)

        st.write("Video search roots")
        st.code("\n".join(config.remote_video_roots), language=None)
        st.write("TOML search roots")
        st.code("\n".join(config.remote_toml_roots), language=None)

        cc1, cc2 = st.columns(2)
        if cc1.button("Test Firebird connection", use_container_width=True):
            try:
                result = ssh.run("hostname && whoami").check()
                st.success(result.stdout.strip())
            except Exception as exc:
                st.error(f"Connection failed: {exc}")

        if cc2.button("Scan Firebird and update catalog", type="primary", use_container_width=True):
            scan_id = db.begin_scan()
            with st.spinner("Scanning configured Firebird folders..."):
                try:
                    videos, tomls = scan_remote(
                        ssh,
                        config.remote_video_roots,
                        config.remote_toml_roots,
                    )
                    units, unmatched = match_files(videos, tomls)
                    db.upsert_analysis_units(units)
                    db.finish_scan(
                        scan_id,
                        video_count=len(videos),
                        toml_count=len(tomls),
                        matched_count=len(units),
                        notes=f"Unmatched or ambiguous TOMLs: {len(unmatched)}",
                    )
                    st.session_state.last_scan_unmatched = unmatched
                    st.success(
                        f"Found {len(videos)} videos, {len(tomls)} TOMLs, "
                        f"and {len(units)} unique matched analysis units."
                    )
                except Exception as exc:
                    db.finish_scan(scan_id, 0, 0, 0, notes=str(exc))
                    st.error(f"Scan failed: {exc}")

    units = db.list_analysis_units()
    if units:
        frame = pd.DataFrame(units)
        display_cols = [
            "analysis_unit_id", "video_filename", "toml_filename", "cell_label",
            "assay_type", "animal_count", "roi_count", "match_method",
            "prior_run_count", "latest_run_at", "accepted_at",
        ]
        available = [c for c in display_cols if c in frame.columns]
        st.subheader(f"Catalog: {len(frame)} unique analysis units")
        st.caption(
            "Rows are deduplicated by a stable analysis-unit ID. "
            "Repeated scans update the existing record."
        )
        event = st.dataframe(
            frame[available],
            use_container_width=True,
            hide_index=True,
            on_select="rerun",
            selection_mode="multi-row",
            key="analysis_units_table",
        )
        selected_rows = event.selection.rows if event else []
        st.session_state.selected_analysis_units = [
            units[i] for i in selected_rows
        ]
        st.info(f"{len(st.session_state.selected_analysis_units)} analysis units selected.")
    else:
        st.warning("No analysis units are in the catalog yet.")

    if st.session_state.last_scan_unmatched:
        with st.expander(
            f"Unmatched or ambiguous TOMLs ({len(st.session_state.last_scan_unmatched)})"
        ):
            st.dataframe(
                pd.DataFrame(st.session_state.last_scan_unmatched),
                use_container_width=True,
                hide_index=True,
            )

    st.subheader("Prior runs")
    runs = db.list_runs()
    if runs:
        st.dataframe(
            pd.DataFrame(runs),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption("No runs have been created yet.")

with tab_settings:
    st.header("Create a new immutable run")

    selected = st.session_state.selected_analysis_units
    st.write(f"Selected analysis units: **{len(selected)}**")

    run_label = st.text_input(
        "Run label",
        value="threshold_test",
        help="A short identifier describing the purpose of this run.",
    )
    notes = st.text_area(
        "Run notes",
        help="Record why the run is being performed and what changed.",
    )

    mode = st.radio(
        "Execution mode",
        options=["idtracker_then_postprocess", "idtracker_only", "postprocess_only"],
        format_func=lambda x: {
            "idtracker_then_postprocess": "Run IDtracker, then post-process",
            "idtracker_only": "Run IDtracker only",
            "postprocess_only": "Run post-processing only",
        }[x],
        help=(
            "IDtracker and post-processing are tracked as separate stages, "
            "even when they are run sequentially."
        ),
    )

    assay_profile = st.selectbox(
        "Analysis profile",
        options=["auto", "behavioral_assay", "fight"],
        help=(
            "Behavioral assays normally contain one beetle. "
            "Fight assays normally contain two beetles and may include a secondary ROI."
        ),
    )

    st.subheader("Run-specific TOML thresholds")
    st.caption(
        "These values are applied only to TOML copies stored inside the new run. "
        "Master TOMLs on Firebird are never modified."
    )
    t1, t2, t3 = st.columns(3)
    area_min = t1.number_input(
        "Minimum blob area",
        min_value=0.0,
        value=100.0,
        step=10.0,
        help=(
            "Smallest segmented blob allowed. Increase it to remove small noise blobs; "
            "decrease it if real beetles are being excluded."
        ),
    )
    area_max = t2.number_input(
        "Maximum blob area",
        min_value=0.0,
        value=5000.0,
        step=100.0,
        help=(
            "Largest segmented blob allowed. Decrease it to reject merged or oversized blobs; "
            "increase it if legitimate beetles are being excluded."
        ),
    )
    background_difference_threshold = t3.number_input(
        "Background-difference threshold",
        min_value=0.0,
        value=30.0,
        step=1.0,
        help=(
            "Minimum intensity difference from the estimated background. "
            "Higher values are more conservative; lower values detect subtler differences "
            "but may increase noise."
        ),
    )

    settings = {
        "run_label": run_label,
        "notes": notes,
        "mode": mode,
        "assay_profile": assay_profile,
        "area_min": area_min,
        "area_max": area_max,
        "background_difference_threshold": background_difference_threshold,
    }

    st.json(settings)

    if st.button("Create run snapshot", type="primary", disabled=not bool(selected)):
        try:
            run_id = create_run(config, db, ssh, selected, settings)
            st.session_state.current_run_id = run_id
            st.success(f"Created {run_id}")
            st.info(
                "The run now has copied TOMLs, a manifest, settings snapshot, "
                "local history, and a remote run directory."
            )
        except Exception as exc:
            st.error(f"Could not create run: {exc}")

with tab_status:
    st.header("Run status, results, and formal QC")

    runs = db.list_runs()
    if not runs:
        st.warning("Create a run first.")
    else:
        run_ids = [r["run_id"] for r in runs]
        default_index = 0
        if st.session_state.current_run_id in run_ids:
            default_index = run_ids.index(st.session_state.current_run_id)

        run_id = st.selectbox("Run", run_ids, index=default_index)
        st.session_state.current_run_id = run_id
        run = next(r for r in runs if r["run_id"] == run_id)

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Units", run["unit_count"])
        c2.metric("IDtracker complete", run["idtracker_completed"] or 0)
        c3.metric("Post-process complete", run["postprocess_completed"] or 0)
        c4.metric("Accepted", run["accepted_count"] or 0)

        st.code(run["remote_run_dir"], language=None)

        a1, a2, a3 = st.columns(3)
        if a1.button("Submit IDtracker", use_container_width=True):
            try:
                job_id = submit_stage(config, db, ssh, run_id, "idtracker")
                st.success(f"Submitted IDtracker job {job_id}")
            except Exception as exc:
                st.error(f"Submission failed: {exc}")

        if a2.button("Submit post-processing", use_container_width=True):
            try:
                job_id = submit_stage(config, db, ssh, run_id, "postprocess")
                st.success(f"Submitted post-processing job {job_id}")
            except Exception as exc:
                st.error(f"Submission failed: {exc}")

        if a3.button("Retrieve quick results", use_container_width=True):
            try:
                with st.spinner("Retrieving PNG and CSV results..."):
                    counts = retrieve_quick_results(config, db, ssh, run_id)
                st.success(
                    f"Retrieved {counts['tracks']} tracks and "
                    f"{counts['summaries']} summary files."
                )
            except Exception as exc:
                st.error(f"Retrieval failed: {exc}")

        st.subheader("Jobs")
        jobs = [j for j in db.list_jobs() if j["run_id"] == run_id]
        if jobs:
            for job in jobs:
                cols = st.columns([2, 2, 2, 1])
                cols[0].write(f"**{job['stage']}**")
                cols[1].code(job["job_id"], language=None)
                cols[2].write(job["state"])

                if cols[3].button(
                    "Refresh",
                    key=f"refresh_{job['job_id']}",
                ):
                    try:
                        status = ssh.job_status(job["job_id"])
                        db.update_job(
                            job["job_id"],
                            status["state"],
                            status["raw"],
                        )
                        st.rerun()
                    except Exception as exc:
                        st.error(str(exc))

                if st.button(
                    f"Cancel job {job['job_id']}",
                    key=f"cancel_{job['job_id']}",
                ):
                    try:
                        ssh.cancel_job(job["job_id"])
                        db.update_job(job["job_id"], "cancelled", "Cancelled from app")
                        st.success(f"Cancelled {job['job_id']}")
                        st.rerun()
                    except Exception as exc:
                        st.error(str(exc))
        else:
            st.caption("No jobs recorded for this run.")

        st.subheader("Run units")
        run_units = db.get_run_units(run_id)
        st.dataframe(
            pd.DataFrame(run_units),
            use_container_width=True,
            hide_index=True,
        )

        st.subheader("Track review")
        track_root = config.local_project_root / "retrieved_results" / run_id / "tracks"
        track_files = sorted(track_root.glob("*.png")) if track_root.exists() else []

        if not track_files:
            st.caption("No retrieved track PNGs found for this run.")
        else:
            reviewer = st.text_input("Reviewer", value=getpass.getuser())
            for idx, track in enumerate(track_files):
                with st.container(border=True):
                    st.write(f"**{track.name}**")
                    try:
                        image = Image.open(track)
                        st.image(image, use_container_width=True)
                    except Exception as exc:
                        st.error(f"Could not display image: {exc}")

                    possible_units = run_units
                    unit_labels = {
                        u["analysis_unit_id"]:
                            f"{u['video_filename']} | {u['cell_label']} | {u['analysis_unit_id']}"
                        for u in possible_units
                    }
                    unit_id = st.selectbox(
                        "Analysis unit",
                        options=list(unit_labels),
                        format_func=lambda x: unit_labels[x],
                        key=f"unit_{idx}",
                    )
                    decision = st.radio(
                        "QC decision",
                        options=["review_later", "accepted", "rejected", "rerun_needed"],
                        horizontal=True,
                        key=f"decision_{idx}",
                    )
                    qc_notes = st.text_area(
                        "QC notes",
                        key=f"notes_{idx}",
                        help=(
                            "Record visible tracking failures, identity swaps, missing trajectories, "
                            "ROI problems, or why the run was accepted."
                        ),
                    )
                    if st.button("Save QC decision", key=f"save_qc_{idx}"):
                        db.save_qc(
                            run_id=run_id,
                            analysis_unit_id=unit_id,
                            decision=decision,
                            reviewer=reviewer,
                            notes=qc_notes,
                            track_local_path=str(track),
                        )
                        st.success("QC decision saved.")
                        st.rerun()

        st.subheader("Exports")
        if st.button("Export catalog and accepted-result tables"):
            outputs = export_all(
                db,
                config.local_project_root / "exports",
            )
            st.success(
                "Exported:\n" + "\n".join(str(p) for p in outputs.values())
            )

        accepted = db.accepted_results()
        if accepted:
            st.write("Accepted results")
            st.dataframe(
                pd.DataFrame(accepted),
                use_container_width=True,
                hide_index=True,
            )
