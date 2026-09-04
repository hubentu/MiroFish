import os
from uuid import uuid4

import pytest

from app.services.memory.fake_backend import FakeKnowledgeGraphBackend


def test_fake_hydrate_round_trip():
    backend = FakeKnowledgeGraphBackend()
    snapshot = {
        "nodes": [
            {"uuid": "n1", "name": "Alice", "labels": ["Person"], "summary": "researcher", "attributes": {}},
            {"uuid": "n2", "name": "Bob", "labels": ["Person"], "summary": "", "attributes": {}},
        ],
        "edges": [
            {
                "uuid": "e1",
                "name": "KNOWS",
                "fact": "Alice knows Bob",
                "source_node_uuid": "n1",
                "target_node_uuid": "n2",
                "attributes": {},
                "episodes": [],
            }
        ],
    }
    backend.hydrate_graph_snapshot("mirofish_new", snapshot)
    nodes = backend.list_nodes("mirofish_new")
    edges = backend.list_edges("mirofish_new")
    assert {n.uuid for n in nodes} == {"n1", "n2"}
    assert len(edges) == 1
    hits = backend.search("mirofish_new", "Alice", limit=5)
    assert hits.total_count >= 1


@pytest.mark.skipif(
    os.getenv("MIROFISH_NEO4J_INTEGRATION") != "1", reason="needs live Neo4j"
)
def test_graphiti_hydrate_integration():
    from app.services.memory.graphiti_backend import GraphitiBackend

    suffix = uuid4().hex
    graph_id = f"mirofish_hydrate_test_{suffix}"
    node_ids = [f"{suffix}-n1", f"{suffix}-n2"]
    backend = GraphitiBackend()
    try:
        backend.hydrate_graph_snapshot(
            graph_id,
            {
                "nodes": [
                    {
                        "uuid": node_ids[0],
                        "name": "Alice",
                        "labels": ["Person"],
                        "summary": "researcher",
                        "attributes": {},
                    },
                    {
                        "uuid": node_ids[1],
                        "name": "Bob",
                        "labels": ["Person"],
                        "summary": "",
                        "attributes": {},
                    },
                ],
                "edges": [
                    {
                        "uuid": f"{suffix}-e1",
                        "name": "KNOWS",
                        "fact": "Alice knows Bob",
                        "source_node_uuid": node_ids[0],
                        "target_node_uuid": node_ids[1],
                        "attributes": {},
                        "episodes": [],
                    }
                ],
            },
        )
        assert {node.uuid for node in backend.list_nodes(graph_id)} == set(node_ids)
        edges = backend.list_edges(graph_id)
        assert len(edges) == 1
        assert edges[0].group_id == graph_id
    finally:
        backend.delete_graph(graph_id)
