from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class ImageInfo:
    path: Path
    size_bytes: int
    width: int
    height: int
    format: str
    exact_hash: str | None = None
    perceptual_hash: str | None = None


@dataclass(slots=True)
class SimilarGroup:
    images: list[ImageInfo]
    similarity: float


@dataclass(slots=True)
class ScanResult:
    root: Path
    discovered: int
    images: list[ImageInfo]
    exact_groups: list[list[ImageInfo]]
    similar_groups: list[SimilarGroup]
    skipped: list[tuple[Path, str]]
    duration_seconds: float
