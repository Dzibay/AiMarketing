"""Утренний разбор «Где горит»: сравнение со снимком прошлого дня (без БД)."""

from app import problem_review


def _p(key):
    return {"key": key, "kind": key.split(":")[0]}


def test_no_snapshot_no_badges_no_line():
    visible = [_p("balance:a1"), _p("stopped:c5")]
    assert problem_review.compare(None, visible, {"balance:a1", "stopped:c5"}) is None
    assert [p["is_new"] for p in visible] == [False, False]


def test_new_and_resolved():
    visible = [_p("balance:a1"), _p("stopped:c5"), _p("stopped:c6")]
    review = problem_review.compare(["balance:a1", "kpi:w3", "token:oX"], visible, {"balance:a1", "stopped:c5", "stopped:c6"})
    assert review == {"new": 2, "resolved": 2}
    assert [p["is_new"] for p in visible] == [False, True, True]


def test_snoozed_is_not_resolved_and_not_new():
    # kpi:w3 скрыт пользователем — он среди всех ключей, но не среди видимых: не «решился».
    visible = [_p("balance:a1")]
    review = problem_review.compare(["balance:a1", "kpi:w3"], visible, {"balance:a1", "kpi:w3"})
    assert review == {"new": 0, "resolved": 0}


def test_empty_snapshot_means_everything_new():
    visible = [_p("balance:a1")]
    assert problem_review.compare([], visible, {"balance:a1"}) == {"new": 1, "resolved": 0}
    assert visible[0]["is_new"] is True


def _ps(key, severity):
    return {"key": key, "kind": key.split(":")[0], "severity": severity}


def test_worse_severity_counts_as_new():
    # Вчера кампания была «на модерации» (info), сегодня «отклонена» (critical) — тот же ключ, но новое.
    visible = [_ps("moderation:c123", "critical"), _ps("budget:w1", "info")]
    review = problem_review.compare(
        ["moderation:c123|info", "budget:w1|warning"],
        visible,
        {"moderation:c123|critical", "budget:w1|info"},
    )
    assert [p["is_new"] for p in visible] == [True, False]  # стало мягче — не новое
    assert review == {"new": 1, "resolved": 0}  # ключи остались — не «решились»


def test_same_severity_not_new_and_old_entries_without_severity():
    visible = [_ps("balance:a1", "warning"), _ps("stopped:c5", "critical")]
    review = problem_review.compare(
        ["balance:a1|warning", "stopped:c5"],  # старая запись без серьёзности — сравниваем по ключу
        visible,
        {"balance:a1|warning", "stopped:c5|critical"},
    )
    assert [p["is_new"] for p in visible] == [False, False]
    assert review == {"new": 0, "resolved": 0}


def test_entry_roundtrip():
    assert problem_review.entry("kpi:w3", "info") == "kpi:w3|info"
    assert problem_review._split("kpi:oА|Б|warning") == ("kpi:oА|Б", 1)
    assert problem_review._split("kpi:w3") == ("kpi:w3", None)
