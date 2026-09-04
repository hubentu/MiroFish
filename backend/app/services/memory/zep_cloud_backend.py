"""Zep Cloud knowledge-graph backend.

Wraps existing zep-cloud SDK helpers behind KnowledgeGraphBackend.
Cloud-only: get_zep_client rejects ZEP_API_URL.

Document ingest uses Zep Batch API (create/add/process) with reconciliation;
wait_for_batch remains available for progress polling and resume.
"""

from __future__ import annotations

import hashlib
import time
import warnings
from datetime import datetime
from typing import Any, Callable, Optional

from pydantic import Field
from zep_cloud import BatchAddItem, EntityEdgeSourceTarget, NotFoundError
from zep_cloud.external_clients.ontology import EdgeModel, EntityModel, EntityText

from ...utils.locale import t
from ...utils.ontology import (
    MAX_ONTOLOGY_TYPES,
    RESERVED_ONTOLOGY_ATTRIBUTE_NAMES,
    normalize_ontology_attributes,
    normalize_ontology_source_targets,
)
from ...utils.zep import (
    ZEP_INGESTION_WAIT_TIMEOUT_SECONDS,
    call_zep_read_with_retry,
    get_zep_client,
    is_retryable_zep_error,
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


def _episodes_from_zep(edge: Any) -> list[str]:
    raw = getattr(edge, "episodes", None) or getattr(edge, "episode_ids", None) or []
    return [str(item) for item in raw]


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
        created_at=_dt_str(getattr(node, "created_at", None)),
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
        episodes=_episodes_from_zep(edge),
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

    def hydrate_graph_snapshot(self, graph_id: str, snapshot: dict) -> None:
        raise NotImplementedError(
            "Report transfer hydrate requires MEMORY_BACKEND=graphiti"
        )

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

    @staticmethod
    def build_operation_id(graph_id: str, contents: list[str]) -> str:
        payload_hash = hashlib.sha256("\0".join(contents).encode("utf-8")).hexdigest()
        return hashlib.sha256(
            f"{graph_id}:{payload_hash}".encode("utf-8")
        ).hexdigest()

    @staticmethod
    def validate_batch_chunks(chunks: list[str], *, batch_size: int = 350) -> None:
        if not chunks:
            raise ValueError("At least one text chunk is required")
        if not 1 <= batch_size <= 350:
            raise ValueError("batch_size must be between 1 and 350")
        if len(chunks) > 50_000:
            raise ValueError("A Zep batch cannot contain more than 50,000 items")
        oversized = [index for index, chunk in enumerate(chunks) if len(chunk) > 10_000]
        if oversized:
            raise ValueError(
                f"Zep batch item exceeds 10,000 characters at chunk {oversized[0]}"
            )

    def _find_batch_by_operation_id(
        self,
        graph_id: str,
        operation_id: str,
        *,
        max_attempts: int = 3,
    ) -> Any | None:
        for attempt in range(1, max_attempts + 1):
            matches: list[Any] = []
            cursor: int | None = None
            seen_cursors: set[int] = set()
            while True:
                page = call_zep_read_with_retry(
                    lambda: self.client.batch.list(limit=100, cursor=cursor),
                    operation_name=f"reconcile batch create {operation_id}",
                )
                for batch in getattr(page, "batches", None) or []:
                    metadata = getattr(batch, "metadata", None) or {}
                    if (
                        metadata.get("mirofish_operation_id") == operation_id
                        and metadata.get("graph_id") == graph_id
                    ):
                        matches.append(batch)
                next_cursor = getattr(page, "next_cursor", None)
                if next_cursor is None:
                    break
                if next_cursor == cursor or next_cursor in seen_cursors:
                    raise RuntimeError("Zep batch list cursor did not advance")
                seen_cursors.add(next_cursor)
                cursor = next_cursor

            if len(matches) > 1:
                raise RuntimeError(
                    f"Multiple Zep batches match operation {operation_id}; refusing ambiguity"
                )
            if matches:
                return matches[0]
            if attempt < max_attempts:
                time.sleep(attempt)
        return None

    def _list_batch_items(self, batch_id: str) -> list[Any]:
        items: list[Any] = []
        cursor: int | None = None
        seen_cursors: set[int] = set()
        while True:
            page = call_zep_read_with_retry(
                lambda: self.client.batch.list_items(
                    batch_id=batch_id,
                    limit=100,
                    cursor=cursor,
                ),
                operation_name=f"list batch items {batch_id}",
            )
            items.extend(getattr(page, "items", None) or [])
            next_cursor = getattr(page, "next_cursor", None)
            if next_cursor is None:
                break
            if next_cursor == cursor or next_cursor in seen_cursors:
                raise RuntimeError(f"Zep batch {batch_id} item cursor did not advance")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        return items

    def _reconcile_batch_item_count(
        self,
        batch_id: str,
        expected_item_count: int,
        *,
        max_attempts: int = 3,
    ) -> list[Any]:
        items: list[Any] = []
        for attempt in range(1, max_attempts + 1):
            items = self._list_batch_items(batch_id)
            if len(items) >= expected_item_count:
                return items
            if attempt < max_attempts:
                time.sleep(attempt)
        return items

    def get_batch_summary(self, batch_id: str) -> Any:
        return call_zep_read_with_retry(
            lambda: self.client.batch.get(batch_id=batch_id),
            operation_name=f"get batch {batch_id}",
        )

    def add_episodes(
        self,
        graph_id: str,
        items: list[EpisodeItem],
        *,
        progress_callback: Callable[[int, int], None] | None = None,
        operation_id: str | None = None,
        batch_created_callback: Callable[[str | None, str], None] | None = None,
        batch_size: int = 350,
        message_progress_callback: Callable[[str, float], None] | None = None,
        use_batch: bool = False,
    ) -> IngestResult:
        """Ingest episodes via Batch API (builder) or sequential graph.add (sim)."""

        if not graph_id:
            raise ValueError("graph_id is required")

        # GraphBuilder passes Batch kwargs / use_batch=True; sim updater does not.
        use_batch_api = use_batch or (
            operation_id is not None
            or batch_created_callback is not None
            or message_progress_callback is not None
        )
        if not use_batch_api:
            return self._add_episodes_sequential(
                graph_id, items, progress_callback=progress_callback
            )

        contents = [item.content for item in items]
        self.validate_batch_chunks(contents, batch_size=batch_size)

        total_chunks = len(contents)
        operation_id = operation_id or self.build_operation_id(graph_id, contents)
        if batch_created_callback:
            batch_created_callback(None, operation_id)

        try:
            batch = self.client.batch.create(
                metadata={
                    "mirofish_operation_id": operation_id,
                    "graph_id": graph_id,
                    "chunk_count": total_chunks,
                }
            )
        except Exception as error:
            if not is_retryable_zep_error(error):
                raise
            batch = self._find_batch_by_operation_id(graph_id, operation_id)
            if batch is None:
                raise RuntimeError(
                    "Zep batch creation is unconfirmed and no matching operation was found"
                ) from error
        batch_id = getattr(batch, "batch_id", None)
        if not batch_id:
            raise RuntimeError("Zep Batch API returned no batch_id")
        if batch_created_callback:
            batch_created_callback(batch_id, operation_id)

        episode_uuids: list[str] = []
        for i in range(0, total_chunks, batch_size):
            batch_chunks = contents[i : i + batch_size]
            batch_num = i // batch_size + 1
            total_batches = (total_chunks + batch_size - 1) // batch_size
            done = i + len(batch_chunks)

            if message_progress_callback:
                message_progress_callback(
                    t(
                        "progress.sendingBatch",
                        current=batch_num,
                        total=total_batches,
                        chunks=len(batch_chunks),
                    ),
                    done / total_chunks,
                )
            if progress_callback is not None:
                progress_callback(done, total_chunks)

            batch_items = [
                BatchAddItem(
                    type="graph_episode",
                    graph_id=graph_id,
                    data=chunk,
                    data_type="text",
                    source_description=(
                        items[i + offset].source_description
                        or "MiroFish source document chunk"
                    ),
                    metadata={
                        "mirofish_operation_id": operation_id,
                        "chunk_index": i + offset,
                        "chunk_sha256": hashlib.sha256(
                            chunk.encode("utf-8")
                        ).hexdigest(),
                    },
                )
                for offset, chunk in enumerate(batch_chunks)
            ]

            expected_item_count = i + len(batch_items)
            try:
                item_details = self.client.batch.add(
                    batch_id=batch_id,
                    items=batch_items,
                )
            except Exception as e:
                if message_progress_callback:
                    message_progress_callback(
                        t("progress.batchFailed", batch=batch_num, error=str(e)),
                        0,
                    )
                if is_retryable_zep_error(e):
                    recovered_items = self._reconcile_batch_item_count(
                        batch_id,
                        expected_item_count,
                    )
                    recovered_indexes = {
                        getattr(item, "sequence_index", None)
                        for item in recovered_items
                    }
                    if (
                        len(recovered_items) == expected_item_count
                        and recovered_indexes == set(range(expected_item_count))
                    ):
                        item_details = recovered_items[i:expected_item_count]
                    else:
                        raise RuntimeError(
                            f"Zep batch {batch_id} item submission is unconfirmed; "
                            "the draft was not processed or replayed"
                        ) from e
                else:
                    raise RuntimeError(
                        f"Zep batch {batch_id} item submission failed"
                    ) from e

            if len(item_details or []) != len(batch_items):
                recovered_items = self._reconcile_batch_item_count(
                    batch_id,
                    expected_item_count,
                )
                recovered_indexes = {
                    getattr(item, "sequence_index", None)
                    for item in recovered_items
                }
                if (
                    len(recovered_items) == expected_item_count
                    and recovered_indexes == set(range(expected_item_count))
                ):
                    item_details = recovered_items[i:expected_item_count]
                else:
                    raise RuntimeError(
                        f"Zep batch {batch_id} acknowledged {len(item_details or [])} "
                        f"of {len(batch_items)} items"
                    )
            for item in item_details:
                episode_uuid = getattr(item, "episode_uuid", None)
                if episode_uuid:
                    episode_uuids.append(episode_uuid)

        try:
            self.client.batch.process(batch_id=batch_id)
        except Exception as error:
            summary = call_zep_read_with_retry(
                lambda: self.client.batch.get(batch_id=batch_id),
                operation_name=f"reconcile batch {batch_id}",
            )
            if getattr(summary, "status", None) in {None, "draft"}:
                raise RuntimeError(
                    f"Zep batch {batch_id} processing is unconfirmed"
                ) from error

        return IngestResult(
            episode_uuids=episode_uuids,
            item_count=total_chunks,
            batch_id=batch_id,
            operation_id=operation_id,
        )

    def _add_episodes_sequential(
        self,
        graph_id: str,
        items: list[EpisodeItem],
        *,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> IngestResult:
        """Add small/sim episodes via client.graph.add (created_at + metadata)."""
        episode_uuids: list[str] = []
        total = len(items)
        for index, item in enumerate(items, start=1):
            created_at = item.created_at
            if created_at is None and item.reference_time is not None:
                created_at = item.reference_time.isoformat()
            kwargs: dict[str, Any] = {
                "graph_id": graph_id,
                "type": "text",
                "data": item.content,
                "source_description": item.source_description or "mirofish",
            }
            if created_at:
                kwargs["created_at"] = created_at
            if item.metadata:
                kwargs["metadata"] = item.metadata
            episode = self.client.graph.add(**kwargs)
            episode_uuid = (
                getattr(episode, "uuid_", None) or getattr(episode, "uuid", None)
            )
            if not episode_uuid:
                raise RuntimeError("Zep graph.add returned no episode UUID")
            episode_uuids.append(str(episode_uuid))
            if progress_callback is not None:
                progress_callback(index, total)
        return IngestResult(episode_uuids=episode_uuids, item_count=total)

    def wait_for_batch(
        self,
        batch_id: str,
        item_count: int,
        *,
        progress_callback: Callable[[str, float], None] | None = None,
        timeout: int | None = None,
    ) -> list[str]:
        timeout = timeout or ZEP_INGESTION_WAIT_TIMEOUT_SECONDS
        start_time = time.time()
        terminal_states = {"succeeded", "partial", "failed", "invalid", "canceled"}
        status = None

        while True:
            if time.time() - start_time > timeout:
                raise TimeoutError(
                    f"Zep batch {batch_id} did not finish within {timeout}s"
                )

            summary = call_zep_read_with_retry(
                lambda: self.client.batch.get(batch_id=batch_id),
                operation_name=f"poll batch {batch_id}",
            )
            status = getattr(summary, "status", None)
            progress = getattr(summary, "progress", None)
            percent = float(getattr(progress, "percent_complete", 0) or 0) / 100
            if progress_callback:
                completed = int(getattr(progress, "succeeded_items", 0) or 0)
                progress_callback(
                    t(
                        "progress.zepProcessing",
                        completed=completed,
                        total=item_count,
                        pending=max(item_count - completed, 0),
                        elapsed=int(time.time() - start_time),
                    ),
                    min(max(percent, 0.0), 1.0),
                )

            if status in terminal_states:
                break
            time.sleep(3)

        items = self._list_batch_items(batch_id)
        if status != "succeeded":
            failed_items = [
                item
                for item in items
                if getattr(item, "status", None) not in {"succeeded", "skipped"}
            ]
            first_error = (
                getattr(failed_items[0], "error", None) if failed_items else None
            )
            raise RuntimeError(
                f"Zep batch {batch_id} ended as {status}; "
                f"failed_items={len(failed_items)}; first_error={first_error}"
            )
        if len(items) != item_count:
            raise RuntimeError(
                f"Zep batch {batch_id} contains {len(items)} items, "
                f"expected {item_count}"
            )

        ordered_items = sorted(
            items,
            key=lambda item: getattr(item, "sequence_index", 0) or 0,
        )
        episode_uuids: list[str] = []
        for item in ordered_items:
            item_status = getattr(item, "status", None)
            episode_uuid = getattr(item, "episode_uuid", None)
            source_uuid = getattr(item, "source_uuid", None)
            if item_status != "succeeded" or not episode_uuid:
                raise RuntimeError(
                    f"Zep batch {batch_id} returned an incomplete item"
                )
            if source_uuid and source_uuid != episode_uuid:
                raise RuntimeError(
                    f"Zep batch {batch_id} returned mismatched episode UUIDs"
                )
            episode_uuids.append(episode_uuid)

        if progress_callback:
            progress_callback(
                t(
                    "progress.processingComplete",
                    completed=len(episode_uuids),
                    total=item_count,
                ),
                1.0,
            )
        return episode_uuids

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
