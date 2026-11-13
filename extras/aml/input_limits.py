"""Bound provider copies, never stored text. No approximate char/token conversion.

Without shipping a model tokenizer onto the 2 GiB serving host, use conservative
UTF-8 byte ceilings (byte-level tokenization cannot produce more text tokens
than bytes). Leave additional room for provider framing. These are deliberately
not advertised as exact token counts. Keep both ends, where task instructions
often occur. Short text is returned byte-for-byte unchanged.
"""

EMBED_QUERY_BYTES = 7680  # below even the documented 8192-token regional limit
JEV_STATE_BYTES = 30000  # below 32k per-question; room for QUESTIONS + framing
MARKER = "\n[... middle omitted for input length ...]\n"


def bound_text(text: str, byte_limit: int) -> str:
    raw = text.encode("utf-8")
    if len(raw) <= byte_limit:
        return text
    marker = MARKER.encode("utf-8")
    if byte_limit < len(marker) + 8:
        return raw[:max(0, byte_limit)].decode("utf-8", errors="ignore")
    room = byte_limit - len(marker)
    head = (room + 1) // 2
    tail = room - head
    return (raw[:head].decode("utf-8", errors="ignore") + MARKER
            + raw[-tail:].decode("utf-8", errors="ignore"))


def jev_state(question: str, memory: str) -> str:
    framing = "Question: " + "\nMemory: "
    room = JEV_STATE_BYTES - len(framing.encode("utf-8"))
    # Preserve the entire candidate unless it alone leaves insufficient room
    # for the query. This is a request-only copy; no stored memory is changed.
    query_reserve = min(len(question.encode("utf-8")), 7680)
    memory_copy = bound_text(memory, room - query_reserve)
    query_copy = bound_text(question, room - len(memory_copy.encode("utf-8")))
    return "Question: " + query_copy + "\nMemory: " + memory_copy
