from __future__ import annotations

from extras.pgvector_backend.recall_pipeline import (
    RecallHit,
    RecallPipeline,
    default_content_fingerprint,
)


EVENT = "苏晚说会和我顶嘴的克霖才是她想要的"


def _duplicate_hits(_query, _top_k):
    return [
        RecallHit(
            source_id=101,
            title="prefixed",
            content=f"[knowledge_base] {EVENT}",
            score=0.9,
            channel="vector",
        ),
        RecallHit(
            source_id=202,
            title="plain",
            content=EVENT,
            score=0.8,
            channel="vector",
        ),
        RecallHit(
            source_id=303,
            title="backfill",
            content="这是另一条足够长而且不同的候选记忆",
            score=0.7,
            channel="vector",
        ),
    ]


def test_same_turn_content_duplicates_with_different_ids_share_one_slot():
    result = RecallPipeline(
        vector_search=_duplicate_hits,
        fusion="raw",
        final_top_k=2,
    ).recall("query")

    assert [hit.source_id for hit in result.hits] == [101, 303]
    assert result.hits[0].metadata["content_duplicates_merged"] == 1
    assert result.trace["cascade"]["content_dedup"] == {
        "enabled": True,
        "suppressed": 1,
        "fingerprint_errors": 0,
        "status": "filtered",
    }


def test_known_source_prefix_variants_have_the_same_fingerprint():
    variants = (
        f"[knowledge_base] {EVENT}",
        f"knowledge-base: {EVENT}",
        f"[pgvector] [knowledge base] {EVENT}",
        EVENT,
    )
    fingerprints = {
        default_content_fingerprint(
            RecallHit(source_id=i, title="", content=content, score=1.0, channel="vector")
        )
        for i, content in enumerate(variants)
    }
    assert len(fingerprints) == 1


def test_digits_remain_significant_and_short_snippets_do_not_collapse():
    first = RecallHit(1, "", "心率是70到93次", 1.0, "vector")
    second = RecallHit(2, "", "心率是71到93次", 1.0, "vector")
    assert default_content_fingerprint(first) != default_content_fingerprint(second)

    result = RecallPipeline(
        vector_search=lambda _q, _k: [
            RecallHit(1, "", "same", 0.9, "vector"),
            RecallHit(2, "", "same", 0.8, "vector"),
        ],
        fusion="raw",
    ).recall("query")
    assert [hit.source_id for hit in result.hits] == [1, 2]


def test_content_dedup_can_be_disabled_for_legacy_callers():
    result = RecallPipeline(
        vector_search=_duplicate_hits,
        fusion="raw",
        content_fingerprint=None,
    ).recall("query")

    assert [hit.source_id for hit in result.hits] == [101, 202, 303]
    assert result.trace["cascade"]["content_dedup"]["status"] == "disabled"


def test_duplicate_rows_do_not_additively_inflate_fusion_score():
    def vector(_query, _top_k):
        return [RecallHit(1, "", f"[knowledge_base] {EVENT}", 0.2, "vector")]

    def fts(_query, _top_k):
        return [RecallHit(2, "", EVENT, 0.9, "fts")]

    result = RecallPipeline(
        vector_search=vector,
        fts_search=fts,
        fusion="rrf",
        fts_floor=0.45,
    ).recall("query")

    assert len(result.hits) == 1
    assert result.hits[0].score == 1.0 / 61
    assert set(result.hits[0].metadata["channels"]) == {"fts", "vector"}


def test_fingerprint_failure_is_fail_open():
    def broken(_hit):
        raise RuntimeError("unavailable")

    result = RecallPipeline(
        vector_search=_duplicate_hits,
        fusion="raw",
        content_fingerprint=broken,
    ).recall("query")

    assert [hit.source_id for hit in result.hits] == [101, 202, 303]
    assert result.trace["cascade"]["content_dedup"] == {
        "enabled": True,
        "suppressed": 0,
        "fingerprint_errors": 3,
        "status": "fail_open",
    }
