from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from zep_cloud import NotFoundError

from app.services.memory.types import EpisodeItem
from app.services.memory.zep_cloud_backend import ZepCloudBackend


def test_create_graph_calls_zep_graph_create():
    client = MagicMock()
    with patch("app.services.memory.zep_cloud_backend.get_zep_client", return_value=client):
        backend = ZepCloudBackend(api_key="test-key")
        backend.create_graph("mirofish_z1", "Zep Graph")
    client.graph.create.assert_called_once()
    kwargs = client.graph.create.call_args.kwargs
    assert kwargs["graph_id"] == "mirofish_z1"


def test_search_normalizes_edge_facts():
    client = MagicMock()
    edge = MagicMock(
        fact="A related to B",
        uuid_="e1",
        name="REL",
        source_node_uuid="a",
        target_node_uuid="b",
    )
    client.graph.search.return_value = MagicMock(edges=[edge], nodes=[])
    with patch("app.services.memory.zep_cloud_backend.get_zep_client", return_value=client):
        backend = ZepCloudBackend(api_key="test-key")
        hits = backend.search("mirofish_z1", "related", limit=5)
    assert "A related to B" in hits.facts
    assert hits.edges[0]["uuid"] == "e1"
    assert hits.query == "related"


def test_add_episodes_uses_batch_api_and_returns_ingest_result():
    from types import SimpleNamespace

    client = MagicMock()
    client.batch.create.return_value = SimpleNamespace(batch_id="batch-z1")
    client.batch.add.return_value = [SimpleNamespace(episode_uuid="ep-z1")]
    client.batch.process.return_value = SimpleNamespace(status="queued")
    with patch("app.services.memory.zep_cloud_backend.get_zep_client", return_value=client):
        backend = ZepCloudBackend(api_key="test-key")
        result = backend.add_episodes(
            "mirofish_z1",
            [
                EpisodeItem(
                    content="Bob likes tea.",
                    name="c0",
                    reference_time=datetime(2024, 1, 1, tzinfo=timezone.utc),
                )
            ],
        )
    assert result.item_count == 1
    assert result.episode_uuids == ["ep-z1"]
    assert result.batch_id == "batch-z1"
    assert result.operation_id
    client.batch.create.assert_called_once()
    client.batch.add.assert_called_once()
    client.batch.process.assert_called_once_with(batch_id="batch-z1")
    items = client.batch.add.call_args.kwargs["items"]
    assert items[0].graph_id == "mirofish_z1"
    assert items[0].data == "Bob likes tea."
    assert items[0].type == "graph_episode"


def test_graph_exists_true_when_get_succeeds():
    client = MagicMock()
    client.graph.get.return_value = MagicMock(graph_id="mirofish_z1")
    with patch("app.services.memory.zep_cloud_backend.get_zep_client", return_value=client):
        backend = ZepCloudBackend(api_key="test-key")
        assert backend.graph_exists("mirofish_z1") is True


def test_graph_exists_false_on_not_found():
    client = MagicMock()
    client.graph.get.side_effect = NotFoundError(body="missing")
    with patch("app.services.memory.zep_cloud_backend.get_zep_client", return_value=client):
        backend = ZepCloudBackend(api_key="test-key")
        assert backend.graph_exists("missing") is False


def test_delete_graph_calls_zep_delete():
    client = MagicMock()
    with patch("app.services.memory.zep_cloud_backend.get_zep_client", return_value=client):
        backend = ZepCloudBackend(api_key="test-key")
        backend.delete_graph("mirofish_z1")
    client.graph.delete.assert_called_once_with(graph_id="mirofish_z1")


def test_list_nodes_maps_zep_nodes():
    client = MagicMock()
    node = MagicMock(uuid_="n1", labels=["Person"], summary="likes tea", attributes={"role": "chef"})
    node.name = "Bob"  # MagicMock reserves name= for the mock's own name
    with (
        patch("app.services.memory.zep_cloud_backend.get_zep_client", return_value=client),
        patch(
            "app.services.memory.zep_cloud_backend.fetch_all_nodes",
            return_value=[node],
        ) as fetch_nodes,
    ):
        backend = ZepCloudBackend(api_key="test-key")
        nodes = backend.list_nodes("mirofish_z1")
    fetch_nodes.assert_called_once()
    assert nodes[0].uuid == "n1"
    assert nodes[0].name == "Bob"
    assert nodes[0].labels == ["Person"]
    assert nodes[0].group_id == "mirofish_z1"


def test_list_nodes_maps_created_at():
    from datetime import datetime, timezone

    client = MagicMock()
    created = datetime(2024, 6, 1, tzinfo=timezone.utc)
    node = MagicMock(
        uuid_="n1",
        labels=["Person"],
        summary="likes tea",
        attributes={"role": "chef"},
        created_at=created,
    )
    node.name = "Bob"
    with (
        patch("app.services.memory.zep_cloud_backend.get_zep_client", return_value=client),
        patch(
            "app.services.memory.zep_cloud_backend.fetch_all_nodes",
            return_value=[node],
        ),
    ):
        backend = ZepCloudBackend(api_key="test-key")
        nodes = backend.list_nodes("mirofish_z1")
    assert nodes[0].created_at == created.isoformat()


def test_list_edges_maps_episodes():
    client = MagicMock()
    edge = MagicMock(
        uuid_="e1",
        name="REL",
        fact="A related to B",
        source_node_uuid="a",
        target_node_uuid="b",
        episodes=["ep-1", "ep-2"],
    )
    with (
        patch("app.services.memory.zep_cloud_backend.get_zep_client", return_value=client),
        patch(
            "app.services.memory.zep_cloud_backend.fetch_all_edges",
            return_value=[edge],
        ),
    ):
        backend = ZepCloudBackend(api_key="test-key")
        edges = backend.list_edges("mirofish_z1")
    assert edges[0].episodes == ["ep-1", "ep-2"]


def test_list_edges_maps_episode_ids_fallback():
    client = MagicMock()
    edge = MagicMock(
        uuid_="e1",
        name="REL",
        fact="A related to B",
        source_node_uuid="a",
        target_node_uuid="b",
        episodes=None,
        episode_ids=["ep-x"],
    )
    with (
        patch("app.services.memory.zep_cloud_backend.get_zep_client", return_value=client),
        patch(
            "app.services.memory.zep_cloud_backend.fetch_all_edges",
            return_value=[edge],
        ),
    ):
        backend = ZepCloudBackend(api_key="test-key")
        edges = backend.list_edges("mirofish_z1")
    assert edges[0].episodes == ["ep-x"]


def test_get_node_returns_none_when_missing():
    client = MagicMock()
    client.graph.node.get.side_effect = NotFoundError(body="missing")
    with patch("app.services.memory.zep_cloud_backend.get_zep_client", return_value=client):
        backend = ZepCloudBackend(api_key="test-key")
        assert backend.get_node("missing-uuid") is None


def test_set_ontology_calls_zep_set_ontology():
    client = MagicMock()
    ontology = {
        "entity_types": [
            {
                "name": "Person",
                "description": "A person",
                "attributes": [{"name": "role", "description": "job"}],
            }
        ],
        "edge_types": [],
    }
    with patch("app.services.memory.zep_cloud_backend.get_zep_client", return_value=client):
        backend = ZepCloudBackend(api_key="test-key")
        backend.set_ontology("mirofish_z1", ontology)
    client.graph.set_ontology.assert_called_once()
    kwargs = client.graph.set_ontology.call_args.kwargs
    assert kwargs["graph_ids"] == ["mirofish_z1"]
    assert "Person" in kwargs["entities"]
