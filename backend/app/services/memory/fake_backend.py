import re
import uuid
from typing import Any, Callable

from .types import EpisodeItem, GraphEdge, GraphNode, IngestResult, SearchHits


def _first_capitalized_token(content: str) -> str:
    match = re.search(r"\b([A-Z][a-zA-Z0-9]*)\b", content)
    return match.group(1) if match else "Entity"


class FakeKnowledgeGraphBackend:
    def __init__(self) -> None:
        self._graphs: dict[str, dict[str, Any]] = {}

    def create_graph(self, graph_id: str, name: str) -> str:
        self._graphs[graph_id] = {
            "name": name,
            "ontology": None,
            "nodes": {},
            "edges": {},
            "episodes": [],
        }
        return graph_id

    def delete_graph(self, graph_id: str) -> None:
        self._graphs.pop(graph_id, None)

    def graph_exists(self, graph_id: str) -> bool:
        return graph_id in self._graphs

    def set_ontology(self, graph_id: str, ontology: dict[str, Any]) -> None:
        self._graphs[graph_id]["ontology"] = ontology

    def add_episodes(
        self,
        graph_id: str,
        items: list[EpisodeItem],
        *,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> IngestResult:
        graph = self._graphs[graph_id]
        episode_uuids: list[str] = []
        total = len(items)

        for index, item in enumerate(items, start=1):
            episode_uuid = str(uuid.uuid4())
            episode_uuids.append(episode_uuid)
            graph["episodes"].append(episode_uuid)

            node_name = _first_capitalized_token(item.content)
            node_uuid = str(uuid.uuid4())
            graph["nodes"][node_uuid] = GraphNode(
                uuid=node_uuid,
                name=node_name,
                labels=["Entity"],
                summary=item.content,
                group_id=graph_id,
            )

            edge_uuid = str(uuid.uuid4())
            graph["edges"][edge_uuid] = GraphEdge(
                uuid=edge_uuid,
                name="RELATES_TO",
                fact=item.content,
                source_node_uuid=node_uuid,
                target_node_uuid=node_uuid,
                group_id=graph_id,
            )

            if progress_callback is not None:
                progress_callback(index, total)

        return IngestResult(episode_uuids=episode_uuids, item_count=len(items))

    def list_nodes(self, graph_id: str) -> list[GraphNode]:
        return list(self._graphs[graph_id]["nodes"].values())

    def list_edges(self, graph_id: str) -> list[GraphEdge]:
        return list(self._graphs[graph_id]["edges"].values())

    def get_node(self, node_uuid: str) -> GraphNode | None:
        for graph in self._graphs.values():
            node = graph["nodes"].get(node_uuid)
            if node is not None:
                return node
        return None

    def get_node_edges(self, graph_id: str, node_uuid: str) -> list[GraphEdge]:
        return [
            edge
            for edge in self._graphs[graph_id]["edges"].values()
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
        del scope  # ponytail: fake ignores scope; real backends filter by edges/nodes/both
        graph = self._graphs[graph_id]
        query_lower = query.lower()

        matched_facts: list[str] = []
        matched_edges: list[dict[str, Any]] = []
        matched_nodes: list[dict[str, Any]] = []

        for edge in graph["edges"].values():
            if query_lower in edge.fact.lower():
                matched_facts.append(edge.fact)
                matched_edges.append(
                    {
                        "uuid": edge.uuid,
                        "name": edge.name,
                        "fact": edge.fact,
                        "source_node_uuid": edge.source_node_uuid,
                        "target_node_uuid": edge.target_node_uuid,
                    }
                )

        for node in graph["nodes"].values():
            if query_lower in node.name.lower() or query_lower in node.summary.lower():
                matched_nodes.append(
                    {
                        "uuid": node.uuid,
                        "name": node.name,
                        "labels": node.labels,
                        "summary": node.summary,
                    }
                )

        total_count = len(matched_facts) + len(matched_nodes)
        return SearchHits(
            facts=matched_facts[:limit],
            edges=matched_edges[:limit],
            nodes=matched_nodes[:limit],
            query=query,
            total_count=total_count,
        )
