"""Graphiti + Neo4j knowledge-graph backend.

MiroFish ``graph_id`` == Graphiti ``group_id`` namespace within ONE shared Neo4j
database (do not create a Neo4j database per graph).
"""

from __future__ import annotations

import inspect
from datetime import datetime, timezone
from typing import Any, Callable

from pydantic import BaseModel

from ...config import Config
from .async_bridge import run_sync
from .ontology_mapper import map_entity_types
from .types import EpisodeItem, GraphEdge, GraphNode, IngestResult, SearchHits


def _dt_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _edge_to_dict(edge: Any) -> dict[str, Any]:
    return {
        "uuid": getattr(edge, "uuid", None),
        "name": getattr(edge, "name", None),
        "fact": getattr(edge, "fact", None),
        "source_node_uuid": getattr(edge, "source_node_uuid", None),
        "target_node_uuid": getattr(edge, "target_node_uuid", None),
        "group_id": getattr(edge, "group_id", "") or "",
        "created_at": _dt_str(getattr(edge, "created_at", None)),
        "valid_at": _dt_str(getattr(edge, "valid_at", None)),
        "invalid_at": _dt_str(getattr(edge, "invalid_at", None)),
        "expired_at": _dt_str(getattr(edge, "expired_at", None)),
    }


def _node_from_entity(node: Any) -> GraphNode:
    labels = list(getattr(node, "labels", None) or [])
    attrs = getattr(node, "attributes", None) or {}
    if not isinstance(attrs, dict):
        attrs = {}
    return GraphNode(
        uuid=str(getattr(node, "uuid", "")),
        name=str(getattr(node, "name", "") or ""),
        labels=labels,
        summary=str(getattr(node, "summary", "") or ""),
        attributes=dict(attrs),
        group_id=str(getattr(node, "group_id", "") or ""),
    )


def _edge_from_entity(edge: Any) -> GraphEdge:
    return GraphEdge(
        uuid=str(getattr(edge, "uuid", "")),
        name=str(getattr(edge, "name", "") or ""),
        fact=str(getattr(edge, "fact", "") or ""),
        source_node_uuid=str(getattr(edge, "source_node_uuid", "")),
        target_node_uuid=str(getattr(edge, "target_node_uuid", "")),
        created_at=_dt_str(getattr(edge, "created_at", None)),
        valid_at=_dt_str(getattr(edge, "valid_at", None)),
        invalid_at=_dt_str(getattr(edge, "invalid_at", None)),
        expired_at=_dt_str(getattr(edge, "expired_at", None)),
        attributes={},
        group_id=str(getattr(edge, "group_id", "") or ""),
    )


def _normalize_search_result(result: Any) -> tuple[list[Any], list[Any]]:
    """Accept list[EntityEdge] or SearchResults-like objects with .edges/.nodes."""
    if result is None:
        return [], []
    if isinstance(result, list):
        return result, []
    edges = getattr(result, "edges", None)
    nodes = getattr(result, "nodes", None)
    return list(edges or []), list(nodes or [])


class GraphitiBackend:
    def __init__(self, client: Any | None = None) -> None:
        self._graphs: dict[str, dict[str, Any]] = {}
        self._entity_types: dict[str, dict[str, type[BaseModel]]] = {}
        self._indices_ready = False
        if client is not None:
            self.client = client
        else:
            self.client = self._build_client()

    def _build_client(self) -> Any:
        from graphiti_core import Graphiti
        from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
        from graphiti_core.llm_client.config import LLMConfig
        from graphiti_core.llm_client.openai_client import OpenAIClient

        if not Config.NEO4J_PASSWORD:
            raise ValueError("NEO4J_PASSWORD is required for GraphitiBackend")

        llm_config = LLMConfig(
            api_key=Config.LLM_API_KEY,
            model=Config.LLM_MODEL_NAME,
            base_url=Config.LLM_BASE_URL,
        )
        llm_client = OpenAIClient(config=llm_config)

        embedder_kwargs: dict[str, Any] = {
            "api_key": Config.GRAPHITI_EMBEDDING_API_KEY or Config.LLM_API_KEY,
            "base_url": Config.GRAPHITI_EMBEDDING_BASE_URL or Config.LLM_BASE_URL,
        }
        if Config.GRAPHITI_EMBEDDING_MODEL:
            embedder_kwargs["embedding_model"] = Config.GRAPHITI_EMBEDDING_MODEL
        if Config.GRAPHITI_EMBEDDING_DIM:
            embedder_kwargs["embedding_dim"] = int(Config.GRAPHITI_EMBEDDING_DIM)
        embedder = OpenAIEmbedder(config=OpenAIEmbedderConfig(**embedder_kwargs))

        return Graphiti(
            Config.NEO4J_URI,
            Config.NEO4J_USER,
            Config.NEO4J_PASSWORD,
            llm_client=llm_client,
            embedder=embedder,
        )

    def _ensure_indices(self) -> None:
        if self._indices_ready:
            return
        # ponytail: MagicMock clients return non-awaitables; skip run_sync then
        result = self.client.build_indices_and_constraints()
        if inspect.isawaitable(result):
            run_sync(result)  # type: ignore[arg-type]
        self._indices_ready = True

    def create_graph(self, graph_id: str, name: str) -> str:
        self._graphs[graph_id] = {"name": name}
        self._ensure_indices()
        return graph_id

    def delete_graph(self, graph_id: str) -> None:
        self._graphs.pop(graph_id, None)
        self._entity_types.pop(graph_id, None)
        driver = getattr(self.client, "driver", None)
        if driver is None:
            return
        from graphiti_core.nodes import EntityNode

        run_sync(EntityNode.delete_by_group_id(driver, graph_id))

    def graph_exists(self, graph_id: str) -> bool:
        if graph_id in self._graphs:
            return True
        driver = getattr(self.client, "driver", None)
        if driver is None:
            return False
        from graphiti_core.nodes import EntityNode

        nodes = run_sync(EntityNode.get_by_group_ids(driver, [graph_id], limit=1))
        return bool(nodes)

    def set_ontology(self, graph_id: str, ontology: dict[str, Any]) -> None:
        self._entity_types[graph_id] = map_entity_types(ontology)
        if graph_id in self._graphs:
            self._graphs[graph_id]["ontology"] = ontology

    def add_episodes(
        self,
        graph_id: str,
        items: list[EpisodeItem],
        *,
        progress_callback: Callable[[int, int], None] | None = None,
        **_kwargs: Any,
    ) -> IngestResult:
        from graphiti_core.nodes import EpisodeType

        entity_types = self._entity_types.get(graph_id)
        episode_uuids: list[str] = []
        total = len(items)

        for index, item in enumerate(items, start=1):
            reference_time = item.reference_time or datetime.now(timezone.utc)
            result = run_sync(
                self.client.add_episode(
                    name=item.name or f"episode_{index}",
                    episode_body=item.content,
                    source_description=item.source_description or "mirofish",
                    reference_time=reference_time,
                    source=EpisodeType.text,
                    group_id=graph_id,
                    entity_types=entity_types,
                )
            )
            episode = getattr(result, "episode", None)
            episode_uuids.append(str(getattr(episode, "uuid", "")) if episode else "")
            if progress_callback is not None:
                progress_callback(index, total)

        return IngestResult(episode_uuids=episode_uuids, item_count=len(items))

    def list_nodes(self, graph_id: str) -> list[GraphNode]:
        driver = getattr(self.client, "driver", None)
        if driver is None:
            return []
        from graphiti_core.nodes import EntityNode

        nodes = run_sync(EntityNode.get_by_group_ids(driver, [graph_id]))
        return [_node_from_entity(n) for n in nodes]

    def list_edges(self, graph_id: str) -> list[GraphEdge]:
        driver = getattr(self.client, "driver", None)
        if driver is None:
            return []
        from graphiti_core.edges import EntityEdge
        from graphiti_core.errors import GroupsEdgesNotFoundError

        try:
            edges = run_sync(EntityEdge.get_by_group_ids(driver, [graph_id]))
        except GroupsEdgesNotFoundError:
            return []
        return [_edge_from_entity(e) for e in edges]

    def get_node(self, node_uuid: str) -> GraphNode | None:
        driver = getattr(self.client, "driver", None)
        if driver is None:
            return None
        from graphiti_core.nodes import EntityNode

        try:
            node = run_sync(EntityNode.get_by_uuid(driver, node_uuid))
        except Exception:
            return None
        if node is None:
            return None
        return _node_from_entity(node)

    def get_node_edges(self, graph_id: str, node_uuid: str) -> list[GraphEdge]:
        driver = getattr(self.client, "driver", None)
        if driver is None:
            return []
        from graphiti_core.edges import EntityEdge

        edges = run_sync(EntityEdge.get_by_node_uuid(driver, node_uuid))
        return [
            _edge_from_entity(e)
            for e in edges
            if str(getattr(e, "group_id", "") or "") == graph_id
        ]

    def search(
        self,
        graph_id: str,
        query: str,
        *,
        limit: int = 10,
        scope: str = "edges",
    ) -> SearchHits:
        del scope  # ponytail: Graphiti search() returns edges only; nodes via list later
        result = run_sync(
            self.client.search(
                query=query,
                group_ids=[graph_id],
                num_results=limit,
            )
        )
        edges_raw, nodes_raw = _normalize_search_result(result)
        edge_dicts = [_edge_to_dict(e) for e in edges_raw]
        facts = [d["fact"] for d in edge_dicts if d.get("fact")]
        node_dicts = [
            {
                "uuid": getattr(n, "uuid", None),
                "name": getattr(n, "name", None),
                "labels": list(getattr(n, "labels", None) or []),
                "summary": getattr(n, "summary", "") or "",
            }
            for n in nodes_raw
        ]
        return SearchHits(
            facts=facts,
            edges=edge_dicts,
            nodes=node_dicts,
            query=query,
            total_count=len(facts) + len(node_dicts),
        )
