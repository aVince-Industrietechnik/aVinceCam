"""Data model shared by the core and the JSON interface."""

from dataclasses import asdict, dataclass, field
from typing import List, Optional


@dataclass
class Format:
    pixel_format: str
    width: int
    height: int
    # Frame rates as reported by the source; empty if unknown.
    fps: List[float] = field(default_factory=list)


@dataclass
class Camera:
    id: str
    label: str
    model: Optional[str] = None
    kind: str = "unknown"            # integrated | external | unknown
    api: str = "unknown"             # libcamera | v4l2
    pipeline: Optional[str] = None   # e.g. ipu3, uvcvideo
    location: Optional[str] = None   # front | back | external | None
    nodes: List[str] = field(default_factory=list)
    formats: Optional[List[Format]] = None
    formats_source: str = "not probed"
    # {"min": fps, "max": fps, "source": "..."} or None if unknown.
    frame_rate_limits: Optional[dict] = None
    # Identifier used by the backend (e.g. the libcamera camera id).
    backend_id: Optional[str] = None
    # A focus motor (lens/VCM) belongs to this camera. libcamera drives it
    # automatically while streaming, so the preview is locked by default.
    has_lens: bool = False
    sensor_size: Optional[List[int]] = None
    status: str = "unknown"          # free | busy | own-preview | unknown
    status_certainty: str = "unknown"
    warnings: List[str] = field(default_factory=list)


@dataclass
class CoreError:
    code: str
    message: str
    detail: Optional[str] = None


def to_dict(obj):
    return asdict(obj)
