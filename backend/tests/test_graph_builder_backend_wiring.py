from app.services.graph_builder import GraphBuilderService
from app.services.memory.fake_backend import FakeKnowledgeGraphBackend


def test_create_graph_uses_injected_backend():
    fake = FakeKnowledgeGraphBackend()
    svc = GraphBuilderService(backend=fake)
    gid = svc.create_graph("wired", graph_id="mirofish_wire1")
    assert gid == "mirofish_wire1"
    assert fake.graph_exists(gid)


def test_set_ontology_delegates():
    fake = FakeKnowledgeGraphBackend()
    svc = GraphBuilderService(backend=fake)
    svc.create_graph("wired", graph_id="mirofish_wire1")
    ontology = {"entity_types": [{"name": "Org", "description": "org", "attributes": []}], "edge_types": []}
    svc.set_ontology("mirofish_wire1", ontology)
    assert fake._graphs["mirofish_wire1"]["ontology"]["entity_types"][0]["name"] == "Org"


def test_add_text_batches_returns_batch_submission_via_backend():
    fake = FakeKnowledgeGraphBackend()
    svc = GraphBuilderService(backend=fake)
    svc.create_graph("wired", graph_id="mirofish_wire1")
    submission = svc.add_text_batches("mirofish_wire1", ["Acme hired Bob."])
    assert submission.item_count == 1
    assert len(submission.episode_uuids) == 1
    assert submission.batch_id
    assert len(submission.operation_id) == 64
    assert len(fake._graphs["mirofish_wire1"]["episodes"]) == 1


def test_delete_and_get_graph_data_delegate():
    fake = FakeKnowledgeGraphBackend()
    svc = GraphBuilderService(backend=fake)
    svc.create_graph("wired", graph_id="mirofish_wire1")
    svc.add_text_batches("mirofish_wire1", ["Acme hired Bob."])
    data = svc.get_graph_data("mirofish_wire1")
    assert data["graph_id"] == "mirofish_wire1"
    assert data["node_count"] >= 1
    assert data["edge_count"] >= 1
    svc.delete_graph("mirofish_wire1")
    assert not fake.graph_exists("mirofish_wire1")
