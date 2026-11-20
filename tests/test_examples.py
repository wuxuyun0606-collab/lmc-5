from __future__ import annotations

import runpy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_demo_example_runs(capsys) -> None:
    runpy.run_path(str(ROOT / "examples" / "demo.py"), run_name="__main__")
    output = capsys.readouterr().out

    assert "surface:" in output


def test_two_hop_graph_example_proves_contract(capsys) -> None:
    runpy.run_path(str(ROOT / "examples" / "two_hop_graph.py"), run_name="__main__")
    output = capsys.readouterr().out

    assert "OK: two-hop typed graph expansion is working" in output
    assert "Hop 1: verification checklist" in output
    assert "Hop 2: incident retrospective" in output
    assert "Blocked:" not in output


def test_seed_file_imports_and_recalls_on_a_fresh_store(tmp_path) -> None:
    from lmc5 import MemoryStore

    seed = (ROOT / "examples" / "seed.jsonl").read_text(encoding="utf-8")
    with MemoryStore(tmp_path / "seed.sqlite") as store:
        store.init()
        assert store.import_jsonl(seed) == (2, 0)
        assert store.import_jsonl(seed) == (0, 2)
        assert "Confirm blast radius before production work" in {
            hit["title"] for hit in store.recall("rollback")
        }
