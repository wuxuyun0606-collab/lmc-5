"""Document the retained lexical guard without changing its semantics."""
import sqlite3
import pytest
from lmc5.store import MemoryStore


def test_64_terms_can_match_but_65_skip_lexical_candidates(tmp_path):
    with MemoryStore(tmp_path / 'memory.sqlite') as store:
        store.init()
        memory, _ = store.add_memory(title='Orion', content='Orion launch plan')
        assert memory.id in [row[0].id for row in store._recall_candidates('Orion ' * 64, 10)]
        assert store._recall_candidates('Orion ' * 65, 10) == []


def test_unrelated_sqlite_errors_still_propagate(tmp_path):
    with MemoryStore(tmp_path / 'memory.sqlite') as store:
        store.init()
        store.conn.close()
        with pytest.raises(sqlite3.ProgrammingError):
            store._recall_candidates('Orion', 10)
