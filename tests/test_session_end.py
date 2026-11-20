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
    rows = [call.args[1] for call in cursor.execute.call_args_list]
    assert [row[:3] for row in rows] == [
        ("demo", "user", "first turn"), ("demo", "assistant", "reply"), ("demo", "user", "flat turn")
    ]
    assert json.loads(rows[1][3])["message"] == {"role": "assistant", "model": "demo"}
    conn.commit.assert_called_once()
