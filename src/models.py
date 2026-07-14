from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class RemoteVideo:
    path: str
    filename: str
    stem: str
    size_bytes: int
    modified_epoch: float


@dataclass(frozen=True)
class RemoteToml:
    path: str
    filename: str
    stem: str
    size_bytes: int
    modified_epoch: float
    embedded_video_path: Optional[str]
    embedded_video_filename: Optional[str]
    cell_label: Optional[str]
    number_of_animals: Optional[int]
    roi_count: Optional[int]
    area_min: Optional[float]
    area_max: Optional[float]
    background_difference_threshold: Optional[float]


@dataclass(frozen=True)
class AnalysisUnit:
    analysis_unit_id: str
    video_path: str
    toml_path: str
    video_filename: str
    toml_filename: str
    cell_label: str
    assay_type: str
    animal_count: Optional[int]
    roi_count: Optional[int]
    match_method: str
    match_score: int
