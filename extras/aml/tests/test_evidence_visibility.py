import importlib.util
from pathlib import Path

p = Path(__file__).resolve().parents[1] / 'shell/evidence_visibility.py'
spec = importlib.util.spec_from_file_location('evidence_visibility', p)
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


def test_merge_includes_full_multiline_neighbors():
    rows = [{'id': 'a', 'stored': '[date] A: first\ncontinued'},
            {'id': 'b', 'stored': '[date] B: second'}]
    assert m.visible_sources('\n'.join(r['stored'] for r in rows), rows) == ['a', 'b']
    assert m.visible_sources(rows[0]['stored'] + '\n[date] B: sec…', rows) == ['a']


def test_fragments_and_different_timestamps_do_not_count():
    rows = [{'id': 'a', 'stored': '[day1] A: hello'}]
    for text in ['prefix [day1] A: hello', '[day1] A: hello again', '[day2] A: hello']:
        assert m.visible_sources(text, rows) == []
