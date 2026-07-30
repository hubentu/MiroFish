from datetime import datetime, timezone

from app.services.memory.fake_backend import FakeKnowledgeGraphBackend
from app.services.memory.types import EpisodeItem
from app.services.oasis_profile_generator import OasisProfileGenerator
from app.services.zep_entity_reader import EntityNode, ZepEntityReader
from app.services.zep_graph_memory_updater import AgentActivity, ZepGraphMemoryUpdater
from app.services.zep_tools import ZepToolsService


def _seeded_fake():
    fake = FakeKnowledgeGraphBackend()
    fake.create_graph("g1", "g")
    fake.add_episodes("g1", [EpisodeItem(content="Carol founded Acme.")])
    return fake


def test_entity_reader_lists_fake_nodes():
    fake = _seeded_fake()
    reader = ZepEntityReader(backend=fake)
    nodes = reader.get_all_nodes("g1")
    assert len(nodes) >= 1
    assert nodes[0]["name"] == "Carol"


def test_entity_reader_lists_fake_edges():
    fake = _seeded_fake()
    reader = ZepEntityReader(backend=fake)
    edges = reader.get_all_edges("g1")
    assert len(edges) >= 1
    assert "Carol founded Acme." in edges[0]["fact"]


def test_tools_search_uses_backend():
    fake = _seeded_fake()
    tools = ZepToolsService(backend=fake, llm_client=None)
    result = tools.search_graph("g1", "Carol", limit=5)
    assert result.total_count >= 1
    assert any("Carol" in fact for fact in result.facts)


def test_tools_get_all_nodes_and_edges_use_backend():
    fake = _seeded_fake()
    tools = ZepToolsService(backend=fake, llm_client=None)
    nodes = tools.get_all_nodes("g1")
    edges = tools.get_all_edges("g1")
    assert len(nodes) >= 1
    assert len(edges) >= 1
    detail = tools.get_node_detail(nodes[0].uuid)
    assert detail is not None
    assert detail.name == nodes[0].name
    node_edges = tools.get_node_edges("g1", nodes[0].uuid)
    assert len(node_edges) >= 1


def test_memory_updater_sends_via_backend_add_episodes():
    fake = FakeKnowledgeGraphBackend()
    fake.create_graph("g1", "g")
    updater = ZepGraphMemoryUpdater("g1", backend=fake)
    activity = AgentActivity(
        platform="twitter",
        agent_id=1,
        agent_name="Carol",
        action_type="CREATE_POST",
        action_args={"content": "Carol founded Acme."},
        round_num=1,
        timestamp=datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc).isoformat(),
    )
    updater._send_batch_activities([activity], "twitter")
    assert len(fake._graphs["g1"]["episodes"]) >= 1
    assert updater.get_stats()["items_sent"] == 1


def test_memory_updater_skips_episode_poll_for_fake():
    fake = FakeKnowledgeGraphBackend()
    fake.create_graph("g1", "g")
    updater = ZepGraphMemoryUpdater("g1", backend=fake)
    updater._pending_episode_uuids = ["ep-1"]
    updater._wait_for_pending_episodes(deadline=None)
    assert updater._pending_episode_uuids == []


def test_oasis_profile_search_uses_backend():
    fake = _seeded_fake()
    calls = []
    original_search = fake.search

    def tracking_search(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return original_search(*args, **kwargs)

    fake.search = tracking_search  # type: ignore[method-assign]
    generator = OasisProfileGenerator.__new__(OasisProfileGenerator)
    generator.backend = fake
    generator.graph_id = "g1"
    entity = EntityNode(
        uuid="n1",
        name="Carol",
        labels=["Entity", "Person"],
        summary="founder",
        attributes={},
    )
    generator._search_zep_for_entity(entity)
    assert len(calls) == 2
    assert {c["kwargs"].get("scope") for c in calls} == {"edges", "nodes"}
