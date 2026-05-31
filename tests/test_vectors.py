from lmc5.store import MemoryStore
from lmc5.vector import cosine_similarity, toy_embed


def test_cosine_similarity_orders_same_direction_higher():
    assert cosine_similarity([1, 0], [1, 0]) > cosine_similarity([1, 0], [0, 1])


def test_upsert_and_search_memory_vector(tmp_path):
    db = tmp_path / "memory.sqlite"
    with MemoryStore(db) as store:
        store.init()
        first, _ = store.add_memory(
            title="Deployment rollback",
            content="Confirm rollback before deployment.",
        )
        second, _ = store.add_memory(
            title="Recipe preference",
            content="Prefer low-salt soup.",
        )
        store.upsert_vector(
            owner_type="memory",
            owner_id=first.id,
            vector=toy_embed("deployment rollback", dimensions=32),
            provider="local",
            model="toy-hash-32",
        )
        store.upsert_vector(
            owner_type="memory",
            owner_id=second.id,
            vector=toy_embed("soup recipe", dimensions=32),
            provider="local",
            model="toy-hash-32",
        )

        rows = store.search_vectors(
            query_vector=toy_embed("deployment rollback", dimensions=32),
            provider="local",
            model="toy-hash-32",
            owner_type="memory",
        )

    assert rows[0]["owner_id"] == first.id
    assert rows[0]["score"] > rows[1]["score"]
    assert rows[0]["record"]["title"] == "Deployment rollback"


def test_upsert_vector_replaces_same_owner_model(tmp_path):
    db = tmp_path / "memory.sqlite"
    with MemoryStore(db) as store:
        store.init()
        event, _ = store.log_event(role="user", content="old deployment note")
        first = store.upsert_vector(
            owner_type="event",
            owner_id=event.id,
            vector=[1.0, 0.0],
            provider="local",
            model="manual",
        )
        second = store.upsert_vector(
            owner_type="event",
            owner_id=event.id,
            vector=[0.0, 1.0],
            provider="local",
            model="manual",
        )
        rows = store.list_vectors(owner_type="event")

    assert first.id == second.id
    assert len(rows) == 1
