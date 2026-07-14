from extras.claude_code.refined_session_carryover import (
    event_text,
    select_refined_events,
)


def dialogue(kind: str, text: str) -> dict:
    return {
        "type": kind,
        "message": {"role": kind, "content": text},
        "sessionId": "source-session",
        "uuid": f"{kind}-{len(text)}",
        "parentUuid": None,
    }


def test_assistant_gold_before_first_user_is_preserved_with_sentinel():
    gold = dialogue("assistant", "Remember this relationship promise and continuity memory.")
    first_real_user = dialogue("user", "Hello again.")

    selected, stats = select_refined_events([gold, first_real_user], tail_events=1)

    assert [event["type"] for event in selected] == ["user", "assistant", "user"]
    assert event_text(selected[0]).startswith("[refined-carryover:")
    assert selected[1] == gold
    assert stats.selected_gold == 1


def test_user_first_selection_does_not_add_sentinel():
    user = dialogue("user", "A normal opening message.")
    assistant = dialogue("assistant", "A normal reply.")

    selected, _ = select_refined_events([user, assistant])

    assert selected == [user, assistant]


def test_exact_runtime_injection_blocks_never_enter_tail():
    events = [
        dialogue("user", "<task-notification>background task finished</task-notification>"),
        dialogue("user", "<system-reminder>injected context</system-reminder>"),
        dialogue("user", "A clean user message."),
        dialogue("assistant", "A clean assistant reply."),
    ]

    selected, _ = select_refined_events(events, tail_events=20)
    joined = "\n".join(event_text(event) for event in selected)

    assert "task-notification" not in joined
    assert "system-reminder" not in joined
    assert "A clean user message." in joined
    assert "A clean assistant reply." in joined
