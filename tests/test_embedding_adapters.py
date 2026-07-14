from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from extras.pgvector_backend.embedders import (
    gemini_embedder,
    openai_embedder,
    voyage_embedder,
)


class _Response:
    def __init__(self, payload: dict[str, object]):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self._payload


@pytest.mark.parametrize(
    ("env_name", "factory"),
    [
        ("GEMINI_API_KEY", gemini_embedder),
        ("VOYAGE_API_KEY", voyage_embedder),
        ("OPENAI_API_KEY", openai_embedder),
    ],
)
def test_remote_embedder_is_optional_without_api_key(monkeypatch, env_name, factory) -> None:
    monkeypatch.delenv(env_name, raising=False)

    assert factory() is None


def test_gemini_redacts_text_before_remote_call(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def post(_url, **kwargs):
        captured.update(kwargs)
        return _Response({"embedding": {"values": [0.1, 0.2]}})

    monkeypatch.setitem(sys.modules, "requests", SimpleNamespace(post=post))
    embed = gemini_embedder(api_key="fake-provider-key")

    assert embed is not None
    assert embed("note api_key=fake-secret-value host=192.0.2.44") == [0.1, 0.2]
    sent = captured["json"]["content"]["parts"][0]["text"]
    assert "fake-secret-value" not in sent
    assert "192.0.2.44" not in sent
    assert "[REDACTED]" in sent


def test_voyage_redacts_text_before_remote_call(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def post(_url, **kwargs):
        captured.update(kwargs)
        return _Response({"data": [{"embedding": [0.3, 0.4]}]})

    monkeypatch.setitem(sys.modules, "requests", SimpleNamespace(post=post))
    embed = voyage_embedder(api_key="fake-provider-key")

    assert embed is not None
    assert embed("Authorization: Bearer fake-bearer-token") == [0.3, 0.4]
    sent = captured["json"]["input"][0]
    assert "fake-bearer-token" not in sent
    assert "[REDACTED]" in sent


def test_openai_redacts_text_before_remote_call(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def post(_url, **kwargs):
        captured.update(kwargs)
        return _Response({"data": [{"embedding": [0.5, 0.6]}]})

    monkeypatch.setitem(sys.modules, "requests", SimpleNamespace(post=post))
    embed = openai_embedder(api_key="fake-provider-key")

    assert embed is not None
    assert embed("postgresql://demo:fake-password@db.example/test") == [0.5, 0.6]
    sent = captured["json"]["input"]
    assert "fake-password" not in sent
    assert "postgresql://[REDACTED_DSN]" in sent
