from src.models import RemoteVideo, RemoteToml
from src.matching import match_files


def test_matching_deduplicates_exact_pair():
    video = RemoteVideo(
        path="/data/video/Camera_1_ACT1.mp4",
        filename="Camera_1_ACT1.mp4",
        stem="Camera_1_ACT1",
        size_bytes=10,
        modified_epoch=1.0,
    )
    toml = RemoteToml(
        path="/data/toml/Camera_1_ACT1_A1.toml",
        filename="Camera_1_ACT1_A1.toml",
        stem="Camera_1_ACT1_A1",
        size_bytes=10,
        modified_epoch=1.0,
        embedded_video_path="/data/video/Camera_1_ACT1.mp4",
        embedded_video_filename="Camera_1_ACT1.mp4",
        cell_label="A1",
        number_of_animals=1,
        roi_count=1,
        area_min=100,
        area_max=5000,
        background_difference_threshold=30,
    )
    units, unmatched = match_files([video], [toml, toml])
    assert len(units) == 1
    assert not unmatched
