from dataclasses import dataclass
from typing import Optional


@dataclass
class VideoRecord:
    path: str
    filename: str
    stem: str


@dataclass
class TomlRecord:
    path: str
    filename: str
    stem: str
    embedded_video_filename: Optional[str] = None
    cell_label: Optional[str] = None
    number_of_animals: Optional[int] = None
    roi_count: Optional[int] = None
    area_min: Optional[float] = None
    area_max: Optional[float] = None
    background_difference: Optional[float] = None
    parse_error: Optional[str] = None


@dataclass
class SessionRecord:
    path: str
    folder_name: str


@dataclass
class MatchRecord:
    selected: bool
    status: str
    reason: str
    video_path: Optional[str]
    video_filename: Optional[str]
    toml_path: str
    toml_filename: str
    cell_label: Optional[str]
    session_path: Optional[str]
    number_of_animals: Optional[int]
    roi_count: Optional[int]
    area_min: Optional[float]
    area_max: Optional[float]
    background_difference: Optional[float]
    assay_type: str
