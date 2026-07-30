from typing import Any, Callable, Protocol

from .types import EpisodeItem, GraphEdge, GraphNode, IngestResult, SearchHits


class KnowledgeGraphBackend(Protocol):
    def create_graph(self, graph_id: str, name: str) -> str: ...

    def delete_graph(self, graph_id: str) -> None: ...

    def graph_exists(self, graph_id: str) -> bool: ...

    def set_ontology(self, graph_id: str, ontology: dict[str, Any]) -> None: ...

    def add_episodes(
        self,
        graph_id: str,
        items: list[EpisodeItem],
        *,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> IngestResult: ...

    def list_nodes(self, graph_id: str) -> list[GraphNode]: ...

    def list_edges(self, graph_id: str) -> list[GraphEdge]: ...

    def get_node(self, node_uuid: str) -> GraphNode | None: ...

    def get_node_edges(self, graph_id: str, node_uuid: str) -> list[GraphEdge]: ...

    def search(
        self,
        graph_id: str,
        query: str,
        *,
        limit: int = 10,
        scope: str = "edges",
    ) -> SearchHits: ...
