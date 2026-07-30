from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.memory.graphiti_backend import GraphitiBackend
from app.services.memory.ontology_mapper import map_entity_types
from app.services.memory.types import EpisodeItem


def test_create_graph_registers_group_and_builds_indices():
    client = MagicMock()
    client.driver = None
    backend = GraphitiBackend(client=client)
    backend._ensure_indices = MagicMock()
    gid = backend.create_graph("mirofish_abc", "Demo")
    assert gid == "mirofish_abc"
    assert backend.graph_exists(gid)
    backend._ensure_indices.assert_called_once()


def test_add_episodes_calls_add_episode_with_group_id():
    client = MagicMock()
    client.driver = None
    client.add_episode = AsyncMock(return_value=MagicMock(episode=MagicMock(uuid="ep-1")))
    backend = GraphitiBackend(client=client)
    backend.create_graph("mirofish_abc", "Demo")

    result = backend.add_episodes(
        "mirofish_abc",
        [EpisodeItem(content="Bob likes tea.", name="c0", reference_time=datetime.now(timezone.utc))],
    )
    assert result.item_count == 1
    assert result.episode_uuids == ["ep-1"]
    assert client.add_episode.await_count == 1
    kwargs = client.add_episode.await_args.kwargs
    assert kwargs["group_id"] == "mirofish_abc"
    assert "Bob likes tea." in kwargs["episode_body"]


def test_search_maps_edges_to_search_hits():
    edge = MagicMock()
    edge.uuid = "e1"
    edge.name = "LIKES"
    edge.fact = "Bob likes tea"
    edge.source_node_uuid = "n1"
    edge.target_node_uuid = "n2"
    client = MagicMock()
    client.driver = None
    client.search = AsyncMock(return_value=[edge])
    backend = GraphitiBackend(client=client)
    backend.create_graph("mirofish_abc", "Demo")
    hits = backend.search("mirofish_abc", "tea", limit=5)
    assert client.search.await_args.kwargs["group_ids"] == ["mirofish_abc"]
    assert hits.facts == ["Bob likes tea"]
    assert hits.edges[0]["uuid"] == "e1"
    assert hits.query == "tea"
    assert hits.total_count == 1


def test_search_scope_nodes_keyword_filters_list_nodes():
    from app.services.memory.types import GraphNode

    client = MagicMock()
    client.driver = None
    client.search = AsyncMock(return_value=[])
    backend = GraphitiBackend(client=client)
    backend.create_graph("mirofish_abc", "Demo")
    backend.list_nodes = MagicMock(
        return_value=[
            GraphNode(uuid="n1", name="Alice", labels=["Person"], summary="likes tea"),
            GraphNode(uuid="n2", name="Bob", labels=["Person"], summary="likes coffee"),
        ]
    )
    hits = backend.search("mirofish_abc", "tea", limit=10, scope="nodes")
    client.search.assert_not_called()
    assert [n["uuid"] for n in hits.nodes] == ["n1"]
    assert hits.edges == []
    assert any("tea" in f.lower() for f in hits.facts)


def test_get_node_edges_filters_by_group_id():
    edge_in = MagicMock()
    edge_in.uuid = "e1"
    edge_in.name = "LIKES"
    edge_in.fact = "Bob likes tea"
    edge_in.source_node_uuid = "n1"
    edge_in.target_node_uuid = "n2"
    edge_in.group_id = "mirofish_abc"

    edge_out = MagicMock()
    edge_out.uuid = "e2"
    edge_out.group_id = "other_graph"

    client = MagicMock()
    client.driver = MagicMock()

    with patch(
        "graphiti_core.edges.EntityEdge.get_by_node_uuid",
        new=AsyncMock(return_value=[edge_in, edge_out]),
    ):
        backend = GraphitiBackend(client=client)
        edges = backend.get_node_edges("mirofish_abc", "n1")

    assert len(edges) == 1
    assert edges[0].uuid == "e1"
    assert edges[0].group_id == "mirofish_abc"


def test_map_entity_types_sanitizes_reserved_attributes():
    models = map_entity_types(
        {
            "entity_types": [
                {
                    "name": "Person",
                    "description": "A person",
                    "attributes": [
                        {"name": "uuid", "description": "bad reserved"},
                        {"name": "role", "description": "job role"},
                    ],
                }
            ]
        }
    )
    assert "Person" in models
    fields = models["Person"].model_fields
    assert "uuid" not in fields
    assert "entity_uuid" in fields
    assert "role" in fields
