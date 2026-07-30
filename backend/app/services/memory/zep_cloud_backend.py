"""Zep Cloud knowledge-graph backend.

Wraps existing zep-cloud SDK helpers behind KnowledgeGraphBackend.
Cloud-only: get_zep_client rejects ZEP_API_URL.
"""

from __future__ import annotations

import warnings
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from pydantic import Field
from zep_cloud import EntityEdgeSourceTarget, NotFoundError
from zep_cloud.external_clients.ontology import EdgeModel, EntityModel, EntityText

from ...utils.ontology import (
    MAX_ONTOLOGY_TYPES,
    RESERVED_ONTOLOGY_ATTRIBUTE_NAMES,
    normalize_ontology_attributes,
    normalize_ontology_source_targets,
)
from ...utils.zep import (
    call_zep_read_with_retry,
    get_zep_client,
    normalize_zep_search_limit,
    normalize_zep_search_query,
)
from ...utils.zep_paging import fetch_all_edges, fetch_all_nodes
from .types import EpisodeItem, GraphEdge, GraphNode, IngestResult, SearchHits


def _zep_uuid(obj: Any) -> str:
    value = getattr(obj, "uuid_", None) or getattr(obj, "uuid", None) or ""
    return str(value) if value else ""


def _dt_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _to_rfc3339(value: datetime | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _node_from_zep(node: Any, *, group_id: str = "") -> GraphNode:
    labels = list(getattr(node, "labels", None) or [])
    attrs = getattr(node, "attributes", None) or {}
    if not isinstance(attrs, dict):
        attrs = {}
    return GraphNode(
        uuid=_zep_uuid(node),
        name=str(getattr(node, "name", "") or ""),
        labels=labels,
        summary=str(getattr(node, "summary", "") or ""),
        attributes=dict(attrs),
        group_id=group_id or str(getattr(node, "graph_id", "") or ""),
    )


def _edge_from_zep(edge: Any, *, group_id: str = "") -> GraphEdge:
    attrs = getattr(edge, "attributes", None) or {}
    if not isinstance(attrs, dict):
        attrs = {}
    return GraphEdge(
        uuid=_zep_uuid(edge),
        name=str(getattr(edge, "name", "") or ""),
        fact=str(getattr(edge, "fact", "") or ""),
        source_node_uuid=str(getattr(edge, "source_node_uuid", "") or ""),
        target_node_uuid=str(getattr(edge, "target_node_uuid", "") or ""),
        created_at=_dt_str(getattr(edge, "created_at", None)),
        valid_at=_dt_str(getattr(edge, "valid_at", None)),
        invalid_at=_dt_str(getattr(edge, "invalid_at", None)),
        expired_at=_dt_str(getattr(edge, "expired_at", None)),
        attributes=dict(attrs),
        group_id=group_id,
    )


def _safe_attr_name(attr_name: str) -> str:
    if attr_name.lower() in RESERVED_ONTOLOGY_ATTRIBUTE_NAMES:
        return f"entity_{attr_name}"
    return attr_name


class ZepCloudBackend:
    def __init__(self, api_key: str | None = None, client: Any | None = None) -> None:
        if client is not None:
            self.client = client
        else:
            self.client = get_zep_client(api_key)

    def create_graph(self, graph_id: str, name: str) -> str:
        self.client.graph.create(
            graph_id=graph_id,
            name=name,
            description="MiroFish Social Simulation Graph",
        )
        return graph_id

    def delete_graph(self, graph_id: str) -> None:
        self.client.graph.delete(graph_id=graph_id)

    def graph_exists(self, graph_id: str) -> bool:
        try:
            call_zep_read_with_retry(
                lambda: self.client.graph.get(graph_id),
                operation_name=f"graph exists {graph_id}",
            )
            return True
        except NotFoundError:
            return False

    def set_ontology(self, graph_id: str, ontology: dict[str, Any]) -> None:
        # Zep SDK requires Field(default=None); suppress the pydantic warning.
        warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")

        entity_types: dict[str, type] = {}
        for entity_def in ontology.get("entity_types", [])[:MAX_ONTOLOGY_TYPES]:
            name = entity_def["name"]
            description = entity_def.get("description", f"A {name} entity.")
            attrs: dict[str, Any] = {"__doc__": description}
            annotations: dict[str, Any] = {}
            for normalized in normalize_ontology_attributes(
                entity_def.get("attributes", [])
            ):
                attr_name = _safe_attr_name(normalized["name"])
                attrs[attr_name] = Field(
                    description=normalized["description"], default=None
                )
                annotations[attr_name] = Optional[EntityText]
            attrs["__annotations__"] = annotations
            entity_class = type(name, (EntityModel,), attrs)
            entity_class.__doc__ = description
            entity_types[name] = entity_class

        edge_definitions: dict[str, Any] = {}
        for edge_def in ontology.get("edge_types", [])[:MAX_ONTOLOGY_TYPES]:
            name = edge_def["name"]
            description = edge_def.get("description", f"A {name} relationship.")
            attrs = {"__doc__": description}
            annotations = {}
            for normalized in normalize_ontology_attributes(
                edge_def.get("attributes", [])
            ):
                attr_name = _safe_attr_name(normalized["name"])
                attrs[attr_name] = Field(
                    description=normalized["description"], default=None
                )
                annotations[attr_name] = Optional[str]
            attrs["__annotations__"] = annotations
            class_name = "".join(word.capitalize() for word in name.split("_"))
            edge_class = type(class_name, (EdgeModel,), attrs)
            edge_class.__doc__ = description

            source_targets = [
                EntityEdgeSourceTarget(
                    source=st.get("source", "Entity"),
                    target=st.get("target", "Entity"),
                )
                for st in normalize_ontology_source_targets(
                    edge_def.get("source_targets", [])
                )
            ]
            if source_targets:
                edge_definitions[name] = (edge_class, source_targets)

        if entity_types or edge_definitions:
            self.client.graph.set_ontology(
                graph_ids=[graph_id],
                entities=entity_types,
                edges=edge_definitions if edge_definitions else None,
            )

    def add_episodes(
        self,
        graph_id: str,
        items: list[EpisodeItem],
        *,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> IngestResult:
        # ponytail: sequential graph.add for protocol completeness; Batch API
        # reconciliation stays in GraphBuilderService until Task 6 extraction.
        episode_uuids: list[str] = []
        total = len(items)
        for index, item in enumerate(items, start=1):
            episode = self.client.graph.add(
                graph_id=graph_id,
                type="text",
                data=item.content,
                created_at=_to_rfc3339(item.reference_time),
                source_description=item.source_description or "mirofish",
                metadata={
                    "source": "mirofish",
                    "episode_name": item.name or f"episode_{index}",
                },
            )
            episode_uuids.append(_zep_uuid(episode))
            if progress_callback is not None:
                progress_callback(index, total)
        return IngestResult(episode_uuids=episode_uuids, item_count=len(items))

    def list_nodes(self, graph_id: str) -> list[GraphNode]:
        nodes = fetch_all_nodes(self.client, graph_id)
        return [_node_from_zep(n, group_id=graph_id) for n in nodes]

    def list_edges(self, graph_id: str) -> list[GraphEdge]:
        edges = fetch_all_edges(self.client, graph_id)
        return [_edge_from_zep(e, group_id=graph_id) for e in edges]

    def get_node(self, node_uuid: str) -> GraphNode | None:
        try:
            node = call_zep_read_with_retry(
                lambda: self.client.graph.node.get(uuid_=node_uuid),
                operation_name=f"get node {node_uuid[:8]}",
            )
        except NotFoundError:
            return None
        if not node:
            return None
        return _node_from_zep(node)

    def get_node_edges(self, graph_id: str, node_uuid: str) -> list[GraphEdge]:
        return [
            edge
            for edge in self.list_edges(graph_id)
            if edge.source_node_uuid == node_uuid or edge.target_node_uuid == node_uuid
        ]

    def search(
        self,
        graph_id: str,
        query: str,
        *,
        limit: int = 10,
        scope: str = "edges",
    ) -> SearchHits:
        zep_query = normalize_zep_search_query(query)
        zep_limit = normalize_zep_search_limit(limit)
        search_results = call_zep_read_with_retry(
            lambda: self.client.graph.search(
                graph_id=graph_id,
                query=zep_query,
                limit=zep_limit,
                scope=scope,
                reranker="cross_encoder",
            ),
            operation_name=f"graph search {graph_id}",
        )

        facts: list[str] = []
        edges: list[dict[str, Any]] = []
        nodes: list[dict[str, Any]] = []

        for edge in getattr(search_results, "edges", None) or []:
            fact = getattr(edge, "fact", None)
            if fact:
                facts.append(fact)
            edges.append(
                {
                    "uuid": _zep_uuid(edge),
                    "name": getattr(edge, "name", "") or "",
                    "fact": fact or "",
                    "source_node_uuid": getattr(edge, "source_node_uuid", "") or "",
                    "target_node_uuid": getattr(edge, "target_node_uuid", "") or "",
                }
            )

        for node in getattr(search_results, "nodes", None) or []:
            summary = getattr(node, "summary", None) or ""
            name = getattr(node, "name", "") or ""
            nodes.append(
                {
                    "uuid": _zep_uuid(node),
                    "name": name,
                    "labels": list(getattr(node, "labels", None) or []),
                    "summary": summary,
                }
            )
            if summary:
                facts.append(f"[{name}]: {summary}")

        return SearchHits(
            facts=facts,
            edges=edges,
            nodes=nodes,
            query=query,
            total_count=len(facts),
        )
