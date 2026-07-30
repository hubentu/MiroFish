from datetime import datetime, timezone

from app.services.memory.fake_backend import FakeKnowledgeGraphBackend
from app.services.memory.types import EpisodeItem


def test_fake_create_ingest_search_roundtrip():
    backend = FakeKnowledgeGraphBackend()
    gid = backend.create_graph("mirofish_test1", "Test")
    assert backend.graph_exists(gid)

    backend.set_ontology(
        gid,
        {
            "entity_types": [{"name": "Person", "description": "A person", "attributes": []}],
            "edge_types": [],
        },
    )
    result = backend.add_episodes(
        gid,
        [
            EpisodeItem(
                content="Alice works at MiroFish.",
                name="chunk-0",
                reference_time=datetime.now(timezone.utc),
            )
        ],
    )
    assert result.item_count == 1
    assert backend.list_nodes(gid)  # fake seeds a node from content
    hits = backend.search(gid, "Alice", limit=5)
    assert hits.total_count >= 1
    assert any("Alice" in f for f in hits.facts)

    backend.delete_graph(gid)
    assert not backend.graph_exists(gid)
