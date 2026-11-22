import importlib.util
from pathlib import Path


def load_verifier():
    source = Path(__file__).resolve().parents[1] / 'verify_coding_snapshot.py'
    spec = importlib.util.spec_from_file_location('coding_manifest', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_published_source_matches_reconciliation_manifest():
    assert load_verifier().verify()['status'] == 'matched'


def test_modified_function_fails_both_snapshot_checks(tmp_path):
    import json
    import subprocess

    import pytest

    module = load_verifier()
    manifest = json.loads((module.MODULE / 'coding-source-20261025.json').read_text())
    for record in manifest['files']:
        name = record['published_path']
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        original = subprocess.check_output(['git', 'show', f'{module.SNAPSHOT}:{name}'],
                                           cwd=module.MODULE)
        path.write_bytes(original)
    assert module.verify(tmp_path)['verified_files'] == len(manifest['files'])
    path = tmp_path / 'extras/aml/input_limits.py'
    before = path.read_text()
    assert 'room = byte_limit - len(marker)' in before
    path.write_text(before.replace('room = byte_limit - len(marker)', 'room = byte_limit'))
    with pytest.raises(SystemExit) as err:
        module.verify(tmp_path)
    assert 'published digest mismatch' in str(err.value)
    assert 'historical function/class mismatch' in str(err.value)
