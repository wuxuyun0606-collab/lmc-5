import json
from unittest.mock import MagicMock

from extras.pgvector_backend.hooks.session_end import archive_session_jsonl, extract_session_info


def test_archives_native_and_flat_text_turns(tmp_path):
    path = tmp_path / "turns.jsonl"
    turns = [
        {"type": "user", "message": {"role": "user", "content": "first turn"}},
        {"type": "assistant", "message": {"role": "assistant", "model": "demo",
                                         "content": [{"type": "text", "text": "reply"}]}},
        {"role": "user", "content": "flat turn"},
    ]
    path.write_text("\n".join(json.dumps(turn) for turn in turns), encoding="utf-8")
    for key in ("transcript_path", "session_log", "sessionLog"):
        assert extract_session_info({"session_id": "demo", key: str(path)}) == ("demo", path)

    conn = MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value
    cursor.rowcount = 1
    assert archive_session_jsonl(conn, "demo", path) == 3
    rows = [call.args[1] for call in cursor.execute.call_args_list
            if call.args[0].startswith("INSERT")]
    assert [row[:3] for row in rows] == [
        ("demo", "user", "first turn"), ("demo", "assistant", "reply"), ("demo", "user", "flat turn")
    ]
    assert json.loads(rows[1][3])["message"] == {"role": "assistant", "model": "demo"}
    conn.commit.assert_called_once()

    assert len({row[4] for row in rows}) == 3


def test_archive_preserves_occurrence_and_timestamp(tmp_path):
    path = tmp_path / "turns.jsonl"
    first = {"type": "user", "message": {"content": "same"}, "timestamp": "2025-01-01T10:00:00Z"}
    turns = [first, first, dict(first, uuid="third", timestamp=0)]
    path.write_text("\n".join(json.dumps(turn) for turn in turns))
    def rows():
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.rowcount = 1
        archive_session_jsonl(conn, "session", path)
        return [c.args[1] for c in cur.execute.call_args_list if c.args[0].startswith("INSERT")]
    result = rows()
    assert result == rows()
    assert len({r[4] for r in result}) == 3
    assert [r[5].year for r in result] == [2025, 2025, 1970]
