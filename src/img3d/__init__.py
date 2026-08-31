"""Image-to-3D: provider ABC + HTTP client for the local neural service."""

from .client import RemoteImg3DProvider, get_img3d_provider, load_hardware_config
from .provider import ImageTo3DProvider, ImageTo3DResult

__all__ = [
    "ImageTo3DProvider",
    "ImageTo3DResult",
    "RemoteImg3DProvider",
    "get_img3d_provider",
    "load_hardware_config",
]
