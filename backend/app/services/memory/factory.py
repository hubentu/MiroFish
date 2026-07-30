from functools import lru_cache
from typing import Optional

from ...config import Config
from .protocol import KnowledgeGraphBackend


@lru_cache(maxsize=1)
def _cached_backend(backend_name: str) -> KnowledgeGraphBackend:
    if backend_name == "zep":
        from .zep_cloud_backend import ZepCloudBackend
        return ZepCloudBackend()
    from .graphiti_backend import GraphitiBackend
    return GraphitiBackend()


def get_memory_backend(*, override: Optional[KnowledgeGraphBackend] = None) -> KnowledgeGraphBackend:
    if override is not None:
        return override
    name = (Config.MEMORY_BACKEND or "graphiti").strip().lower()
    return _cached_backend(name)


def clear_memory_backend_cache() -> None:
    _cached_backend.cache_clear()
