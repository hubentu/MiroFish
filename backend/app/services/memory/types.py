from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class GraphNode:
    uuid: str
    name: str
    labels: list[str]
    summary: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)
    group_id: str = ""
    created_at: str | None = None


@dataclass
class GraphEdge:
    uuid: str
    name: str
    fact: str
    source_node_uuid: str
    target_node_uuid: str
    created_at: str | None = None
    valid_at: str | None = None
    invalid_at: str | None = None
    expired_at: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    group_id: str = ""
    episodes: list[str] = field(default_factory=list)


@dataclass
class SearchHits:
    facts: list[str]
    edges: list[dict[str, Any]]
    nodes: list[dict[str, Any]]
    query: str
    total_count: int


@dataclass
class EpisodeItem:
    content: str
    name: str | None = None
    reference_time: datetime | None = None
    source_description: str = "mirofish"


@dataclass
class IngestResult:
    episode_uuids: list[str]
    item_count: int
    batch_id: str | None = None
    operation_id: str | None = None
