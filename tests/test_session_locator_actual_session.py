import json
import os
import time
from pathlib import Path

from pipeline.session_locator import locate


def make_session(root: Path, name: str, video: Path, roi, mtime: float) -> Path:
    session = root / name
    (session / "trajectories").mkdir(parents=True)
    (session / "session.json").write_text(json.dumps({"video_paths": [str(video)], "roi_list": roi}))
    (session / "trajectories" / "trajectories.npy").write_bytes(b"npy")
    os.utime(session / "session.json", (mtime, mtime))
    os.utime(session / "trajectories" / "trajectories.npy", (mtime, mtime))
    return session


def test_locates_idtracker_assigned_suffix_by_video_roi_and_start_marker(tmp_path):
    video = tmp_path / "Camera_ACT1.mp4"
    video.write_bytes(b"")
    roi_a5 = ['+ Polygon [[1.0, 2.0], [3.0, 4.0]]']
    roi_b5 = ['+ Polygon [[11.0, 12.0], [13.0, 14.0]]']
    toml = tmp_path / "Camera_ACT1_A5.toml"
    toml.write_text(f'video_paths = ["{video}"]\nroi_list = {roi_a5!r}\n')
    marker = tmp_path / "started.marker"
    marker.write_text("")
    started = time.time() - 10
    os.utime(marker, (started, started))

    make_session(tmp_path, "session_Camera_ACT1", video, roi_a5, started - 100)
    wanted = make_session(tmp_path, "session_Camera_ACT1_1", video, roi_a5, started + 2)
    make_session(tmp_path, "session_Camera_ACT1_2", video, roi_b5, started + 3)

    assert locate(toml, newer_than_file=marker) == wanted.resolve()
