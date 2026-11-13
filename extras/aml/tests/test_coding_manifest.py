import importlib.util
from pathlib import Path


def test_published_source_matches_reconciliation_manifest():
    source = Path(__file__).resolve().parents[1] / 'verify_coding_snapshot.py'
    spec = importlib.util.spec_from_file_location('coding_manifest', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.verify()['status'] == 'matched'
