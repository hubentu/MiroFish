import os
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.services.memory.fake_backend import FakeKnowledgeGraphBackend
from app.services.memory.graphiti_backend import GraphitiBackend


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
    assert edges[0].uuid == "e1"
    hits = backend.search("mirofish_new", "Alice", limit=5)
    assert hits.total_count >= 1


def test_fake_hydrate_rejects_missing_edge_endpoints_without_creating_graph():
    backend = FakeKnowledgeGraphBackend()

    with pytest.raises(ValueError, match="bad-edge"):
        backend.hydrate_graph_snapshot(
            "mirofish_invalid",
            {
                "nodes": [{"uuid": "n1"}],
                "edges": [
                    {
                        "uuid": "bad-edge",
                        "source_node_uuid": "n1",
                        "target_node_uuid": "missing",
                    }
                ],
            },
        )

    assert not backend.graph_exists("mirofish_invalid")


def test_graphiti_hydrate_remaps_uuids_to_avoid_collisions():
    """Re-importing the same zip must not fail on leftover source UUIDs."""
    driver = MagicMock()
    driver.execute_query = AsyncMock(
        side_effect=[MagicMock(), MagicMock()]  # nodes MERGE, edges MERGE
    )
    client = MagicMock(driver=driver)
    backend = GraphitiBackend(client=client)
    backend._graphs["destination"] = {"name": "destination"}

    backend.hydrate_graph_snapshot(
        "destination",
        {
            "nodes": [
                {"uuid": "shared-node", "name": "A", "labels": [], "attributes": {}},
                {"uuid": "other-node", "name": "B", "labels": [], "attributes": {}},
            ],
            "edges": [
                {
                    "uuid": "e1",
                    "name": "KNOWS",
                    "fact": "A knows B",
                    "source_node_uuid": "shared-node",
                    "target_node_uuid": "other-node",
                    "attributes": {},
                    "episodes": [],
                }
            ],
        },
    )

    node_call = driver.execute_query.await_args_list[0]
    props = [n["properties"] for n in node_call.kwargs["nodes"]]
    written_uuids = {p["uuid"] for p in props}
    assert "shared-node" not in written_uuids
    assert "other-node" not in written_uuids
    assert len(written_uuids) == 2

    edge_call = driver.execute_query.await_args_list[1]
    edge = edge_call.kwargs["edges"][0]
    assert edge["source_node_uuid"] in written_uuids
    assert edge["target_node_uuid"] in written_uuids
    assert edge["properties"]["uuid"] != "e1"


def test_graphiti_hydrate_writes_datetime_created_at():
    """Graphiti record helpers require Neo4j DateTime (not ISO strings)."""
    from datetime import datetime

    driver = MagicMock()
    driver.execute_query = AsyncMock(
        side_effect=[
            MagicMock(),  # nodes MERGE
            MagicMock(),  # edges MERGE
        ]
    )
    client = MagicMock(driver=driver)
    backend = GraphitiBackend(client=client)
    backend._graphs["mirofish_dt"] = {"name": "mirofish_dt"}

    backend.hydrate_graph_snapshot(
        "mirofish_dt",
        {
            "nodes": [
                {
                    "uuid": "n1",
                    "name": "Alice",
                    "labels": ["Person"],
                    "created_at": "2026-01-02T03:04:05Z",
                    "attributes": {},
                }
            ],
            "edges": [],
        },
    )

    node_call = driver.execute_query.await_args_list[0]
    props = node_call.kwargs["nodes"][0]["properties"]
    assert isinstance(props["created_at"], datetime)
    assert props["created_at"].year == 2026
    assert props["uuid"] != "n1"


@pytest.mark.skipif(
    os.getenv("MIROFISH_NEO4J_INTEGRATION") != "1", reason="needs live Neo4j"
)
def test_graphiti_hydrate_integration():
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
        nodes = backend.list_nodes(graph_id)
        assert {node.name for node in nodes} == {"Alice", "Bob"}
        # UUIDs are remapped on hydrate
        assert {node.uuid for node in nodes}.isdisjoint(set(node_ids))
        edges = backend.list_edges(graph_id)
        assert len(edges) == 1
        assert edges[0].group_id == graph_id
    finally:
        backend.delete_graph(graph_id)
