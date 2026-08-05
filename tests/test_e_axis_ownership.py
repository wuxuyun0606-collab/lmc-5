from lmc5.scoring import priority_score

from extras.pgvector_backend.ob_recall import ob_score


def test_primary_agent_initial_priority_sets_e_ranking_baseline():
    high = {"e_initial_priority": 90, "weight": 1.0, "created_at": None}
    low = {"e_initial_priority": 20, "weight": 9.0, "created_at": None}

    assert priority_score(high) > priority_score(low)
    assert ob_score(high) > ob_score(low)


def test_numeric_emotion_does_not_invent_initial_rank():
    calm = {"weight": 1.0, "arousal": 0.1, "created_at": None}
    intense = {"weight": 1.0, "arousal": 1.0, "created_at": None}

    assert ob_score(calm) == ob_score(intense)
