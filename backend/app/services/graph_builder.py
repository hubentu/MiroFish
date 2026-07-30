"""
图谱构建服务
接口2：通过 KnowledgeGraphBackend 构建 Standalone Graph
"""

import hashlib
import uuid
import time
import threading
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass

from ..models.task import TaskManager, TaskStatus
from ..utils.zep import (
    ZEP_INGESTION_WAIT_TIMEOUT_SECONDS,
    is_retryable_zep_error,
)
from .text_processor import TextProcessor
from ..utils.locale import t, get_locale, set_locale
from .memory.factory import get_memory_backend
from .memory.protocol import KnowledgeGraphBackend
from .memory.types import EpisodeItem
from .memory.zep_cloud_backend import ZepCloudBackend


@dataclass
class GraphInfo:
    """图谱信息"""
    graph_id: str
    node_count: int
    edge_count: int
    entity_types: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "graph_id": self.graph_id,
            "node_count": self.node_count,
            "edge_count": self.edge_count,
            "entity_types": self.entity_types,
        }


@dataclass(frozen=True)
class BatchSubmission:
    """Durable identity for one ingestion operation (Zep Batch or local UUID)."""

    batch_id: str
    operation_id: str
    episode_uuids: List[str]
    item_count: int


class GraphBuilderService:
    """
    图谱构建服务
    负责通过 memory backend 构建知识图谱
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        backend: KnowledgeGraphBackend | None = None,
    ):
        # keep api_key only for legacy callers; do not require ZEP key when graphiti
        self.api_key = api_key
        self.backend = backend or get_memory_backend()
        self.task_manager = TaskManager()

    @property
    def client(self):
        """Legacy accessor for scripts/tests that poke the Zep SDK client."""
        return getattr(self.backend, "client", None)

    @client.setter
    def client(self, value) -> None:
        # ponytail: object.__new__ tests assign .client; wrap as ZepCloudBackend
        self.backend = ZepCloudBackend(client=value)

    def build_graph_async(
        self,
        text: str,
        ontology: Dict[str, Any],
        graph_name: str = "MiroFish Graph",
        chunk_size: int = 500,
        chunk_overlap: int = 50,
        batch_size: int = 350
    ) -> str:
        task_id = self.task_manager.create_task(
            task_type="graph_build",
            metadata={
                "graph_name": graph_name,
                "chunk_size": chunk_size,
                "text_length": len(text),
            }
        )

        current_locale = get_locale()

        thread = threading.Thread(
            target=self._build_graph_worker,
            args=(task_id, text, ontology, graph_name, chunk_size, chunk_overlap, batch_size, current_locale)
        )
        thread.daemon = True
        thread.start()

        return task_id

    def _build_graph_worker(
        self,
        task_id: str,
        text: str,
        ontology: Dict[str, Any],
        graph_name: str,
        chunk_size: int,
        chunk_overlap: int,
        batch_size: int,
        locale: str = 'zh'
    ):
        set_locale(locale)
        try:
            self.task_manager.update_task(
                task_id,
                status=TaskStatus.PROCESSING,
                progress=5,
                message=t('progress.startBuildingGraph')
            )

            chunks = TextProcessor.split_text(text, chunk_size, chunk_overlap)
            self.validate_batch_chunks(chunks, batch_size=batch_size)
            total_chunks = len(chunks)

            graph_id = self.create_graph(graph_name)
            self.task_manager.update_task(
                task_id,
                progress=10,
                message=t('progress.graphCreated', graphId=graph_id)
            )

            self.set_ontology(graph_id, ontology)
            self.task_manager.update_task(
                task_id,
                progress=15,
                message=t('progress.ontologySet')
            )

            self.task_manager.update_task(
                task_id,
                progress=20,
                message=t('progress.textSplit', count=total_chunks)
            )

            submission = self.add_text_batches(
                graph_id, chunks, batch_size,
                lambda msg, prog: self.task_manager.update_task(
                    task_id,
                    progress=20 + int(prog * 0.4),
                    message=msg
                )
            )

            self.task_manager.update_task(
                task_id,
                progress=60,
                message=t('progress.waitingZepProcess')
            )

            self._wait_for_batch(
                submission,
                lambda msg, prog: self.task_manager.update_task(
                    task_id,
                    progress=60 + int(prog * 0.3),
                    message=msg
                )
            )

            self.task_manager.update_task(
                task_id,
                progress=90,
                message=t('progress.fetchingGraphInfo')
            )

            graph_info = self._get_graph_info(graph_id)

            self.task_manager.complete_task(task_id, {
                "graph_id": graph_id,
                "graph_info": graph_info.to_dict(),
                "chunks_processed": total_chunks,
            })

        except Exception as e:
            import traceback
            error_msg = f"{str(e)}\n{traceback.format_exc()}"
            self.task_manager.fail_task(task_id, error_msg)

    def create_graph(
        self,
        name: str,
        *,
        graph_id: str | None = None,
        graph_id_callback: Optional[Callable[[str], None]] = None,
    ) -> str:
        """Create a graph with a caller-durable ID and reconcile lost replies."""

        graph_id = graph_id or f"mirofish_{uuid.uuid4().hex[:16]}"
        if graph_id_callback:
            graph_id_callback(graph_id)

        try:
            self.backend.create_graph(graph_id, name)
        except Exception as error:
            if not is_retryable_zep_error(error):
                raise
            for attempt in range(3):
                if self.backend.graph_exists(graph_id):
                    return graph_id
                if attempt < 2:
                    time.sleep(attempt + 1)
            raise

        return graph_id

    @staticmethod
    def build_operation_id(graph_id: str, chunks: List[str]) -> str:
        payload_hash = hashlib.sha256("\0".join(chunks).encode("utf-8")).hexdigest()
        return hashlib.sha256(
            f"{graph_id}:{payload_hash}".encode("utf-8")
        ).hexdigest()

    def set_ontology(self, graph_id: str, ontology: Dict[str, Any]):
        self.backend.set_ontology(graph_id, ontology)

    def add_text_batches(
        self,
        graph_id: str,
        chunks: List[str],
        batch_size: int = 350,
        progress_callback: Optional[Callable] = None,
        batch_created_callback: Optional[Callable[[str | None, str], None]] = None,
    ) -> BatchSubmission:
        """Submit document chunks through the memory backend.

        ZepCloudBackend uses Batch API create/add/process with reconciliation.
        Other backends ingest sequentially. BatchSubmission is preserved for
        progress/state (local UUID batch_id when the backend has no Batch API).
        """

        if not graph_id:
            raise ValueError("graph_id is required")
        self.validate_batch_chunks(chunks, batch_size=batch_size)

        operation_id = self.build_operation_id(graph_id, chunks)
        items = [
            EpisodeItem(
                content=chunk,
                name=f"chunk_{index}",
                source_description="MiroFish source document chunk",
            )
            for index, chunk in enumerate(chunks)
        ]

        def _progress(done: int, total: int) -> None:
            if not progress_callback:
                return
            progress_callback(
                t(
                    "progress.sendingBatch",
                    current=max((done + batch_size - 1) // batch_size, 1),
                    total=max((total + batch_size - 1) // batch_size, 1),
                    chunks=min(batch_size, max(done, 1)),
                ),
                done / total if total else 1.0,
            )

        # ZepCloudBackend honors Batch kwargs; Fake/Graphiti ignore via **kwargs.
        result = self.backend.add_episodes(
            graph_id,
            items,
            progress_callback=_progress if progress_callback else None,
            message_progress_callback=progress_callback,
            operation_id=operation_id,
            batch_created_callback=batch_created_callback,
            batch_size=batch_size,
        )

        # Non-Zep: synthesize BatchSubmission identity when backend has no batch_id.
        if result.batch_id is None:
            if batch_created_callback:
                # Zep already journaled; only journal for non-batch backends.
                # (Zep journals inside add_episodes; Fake/Graphiti do not.)
                batch_created_callback(None, operation_id)
            batch_id = str(uuid.uuid4())
            if batch_created_callback:
                batch_created_callback(batch_id, operation_id)
        else:
            batch_id = result.batch_id

        return BatchSubmission(
            batch_id=batch_id,
            operation_id=result.operation_id or operation_id,
            episode_uuids=list(result.episode_uuids),
            item_count=result.item_count,
        )

    @staticmethod
    def validate_batch_chunks(chunks: List[str], *, batch_size: int = 350) -> None:
        """Validate every Batch API limit before the first Cloud mutation."""
        ZepCloudBackend.validate_batch_chunks(chunks, batch_size=batch_size)

    def get_batch_summary(self, batch_id: str) -> Any:
        """Read a persisted batch identity for restart reconciliation."""
        getter = getattr(self.backend, "get_batch_summary", None)
        if getter is None:
            raise RuntimeError("Current memory backend does not support Zep batch summary")
        return getter(batch_id)

    def _wait_for_batch(
        self,
        submission: BatchSubmission,
        progress_callback: Optional[Callable] = None,
        timeout: int | None = None,
    ) -> List[str]:
        """Wait for ingest terminal state; Zep polls Batch API, others are sync."""

        waiter = getattr(self.backend, "wait_for_batch", None)
        if waiter is not None:
            return waiter(
                submission.batch_id,
                submission.item_count,
                progress_callback=progress_callback,
                timeout=timeout,
            )

        if progress_callback:
            progress_callback(
                t(
                    "progress.processingComplete",
                    completed=submission.item_count,
                    total=submission.item_count,
                ),
                1.0,
            )
        return list(submission.episode_uuids)

    def _wait_for_episodes(
        self,
        episode_uuids: List[str],
        progress_callback: Optional[Callable] = None,
        timeout: int = ZEP_INGESTION_WAIT_TIMEOUT_SECONDS
    ):
        """等待所有 episode 处理完成（通过查询每个 episode 的 processed 状态）"""
        if not episode_uuids:
            if progress_callback:
                progress_callback(t('progress.noEpisodesWait'), 1.0)
            return

        client = self.client
        if client is None:
            if progress_callback:
                progress_callback(
                    t('progress.processingComplete', completed=len(episode_uuids), total=len(episode_uuids)),
                    1.0,
                )
            return

        from ..utils.zep import call_zep_read_with_retry

        start_time = time.time()
        pending_episodes = set(episode_uuids)
        completed_count = 0
        total_episodes = len(episode_uuids)

        if progress_callback:
            progress_callback(t('progress.waitingEpisodes', count=total_episodes), 0)

        while pending_episodes:
            if time.time() - start_time > timeout:
                if progress_callback:
                    progress_callback(
                        t('progress.episodesTimeout', completed=completed_count, total=total_episodes),
                        completed_count / total_episodes
                    )
                raise TimeoutError(
                    f"Zep episode processing timed out with "
                    f"{len(pending_episodes)} episode(s) still pending"
                )

            for ep_uuid in list(pending_episodes):
                episode = call_zep_read_with_retry(
                    lambda: client.graph.episode.get(uuid_=ep_uuid),
                    operation_name=f"poll episode {ep_uuid}",
                )
                is_processed = getattr(episode, 'processed', False)

                if is_processed:
                    pending_episodes.remove(ep_uuid)
                    completed_count += 1

            elapsed = int(time.time() - start_time)
            if progress_callback:
                progress_callback(
                    t('progress.zepProcessing', completed=completed_count, total=total_episodes, pending=len(pending_episodes), elapsed=elapsed),
                    completed_count / total_episodes if total_episodes else 0
                )

            if pending_episodes:
                time.sleep(3)

        if progress_callback:
            progress_callback(t('progress.processingComplete', completed=completed_count, total=total_episodes), 1.0)

    def _get_graph_info(self, graph_id: str) -> GraphInfo:
        nodes = self.backend.list_nodes(graph_id)
        edges = self.backend.list_edges(graph_id)

        entity_types = set()
        for node in nodes:
            for label in node.labels or []:
                if label not in ["Entity", "Node"]:
                    entity_types.add(label)

        return GraphInfo(
            graph_id=graph_id,
            node_count=len(nodes),
            edge_count=len(edges),
            entity_types=list(entity_types)
        )

    def get_graph_data(self, graph_id: str) -> Dict[str, Any]:
        nodes = self.backend.list_nodes(graph_id)
        edges = self.backend.list_edges(graph_id)

        node_map = {node.uuid: node.name or "" for node in nodes}

        nodes_data = []
        for node in nodes:
            created_at = node.created_at
            if not created_at and node.attributes:
                created_at = node.attributes.get("created_at")
            nodes_data.append({
                "uuid": node.uuid,
                "name": node.name,
                "labels": node.labels or [],
                "summary": node.summary or "",
                "attributes": node.attributes or {},
                "created_at": str(created_at) if created_at else None,
            })

        edges_data = []
        for edge in edges:
            fact_type = edge.name or ""
            edges_data.append({
                "uuid": edge.uuid,
                "name": edge.name or "",
                "fact": edge.fact or "",
                "fact_type": fact_type,
                "source_node_uuid": edge.source_node_uuid,
                "target_node_uuid": edge.target_node_uuid,
                "source_node_name": node_map.get(edge.source_node_uuid, ""),
                "target_node_name": node_map.get(edge.target_node_uuid, ""),
                "attributes": edge.attributes or {},
                "created_at": edge.created_at,
                "valid_at": edge.valid_at,
                "invalid_at": edge.invalid_at,
                "expired_at": edge.expired_at,
                "episodes": list(edge.episodes),
            })

        return {
            "graph_id": graph_id,
            "nodes": nodes_data,
            "edges": edges_data,
            "node_count": len(nodes_data),
            "edge_count": len(edges_data),
        }

    def delete_graph(self, graph_id: str):
        self.backend.delete_graph(graph_id)
