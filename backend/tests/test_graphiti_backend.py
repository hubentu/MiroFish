from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from app.services.memory.graphiti_backend import GraphitiBackend
from app.services.memory.ontology_mapper import map_entity_types
from app.services.memory.types import EpisodeItem


def test_create_graph_registers_group_and_builds_indices():
    backend = GraphitiBackend(client=MagicMock())
    backend._ensure_indices = MagicMock()
    gid = backend.create_graph("mirofish_abc", "Demo")
    assert gid == "mirofish_abc"
    assert backend.graph_exists(gid)
    backend._ensure_indices.assert_called_once()


def test_add_episodes_calls_add_episode_with_group_id():
    client = MagicMock()
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
    client.search = AsyncMock(return_value=[edge])
    backend = GraphitiBackend(client=client)
    backend.create_graph("mirofish_abc", "Demo")
    hits = backend.search("mirofish_abc", "tea", limit=5)
    assert hits.facts == ["Bob likes tea"]
    assert hits.edges[0]["uuid"] == "e1"
    assert hits.query == "tea"
    assert hits.total_count == 1


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
