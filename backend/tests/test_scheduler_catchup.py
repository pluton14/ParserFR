"""Автосбор: 00:30 по умолчанию, окно — три завершённых дня до сегодняшнего."""

import sys
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TODAY = date(2026, 10, 6)


def _day(day, status):
    from app.models import HarvestedDay

    return HarvestedDay(day=day, sitemap_url="x", total_urls=1, status=status)


def _run_catch_up(scheduler):
    with patch.object(scheduler, "_today", return_value=TODAY), patch.object(
        scheduler.registry, "active_of_type", return_value=None
    ), patch.object(scheduler.registry, "submit") as submit:
        scheduler.catch_up_on_start()
    return submit


def test_defaults_are_0030_paris_and_window_is_three_days():
    from app.config import Settings

    s = Settings()
    assert (s.scheduler_hour, s.scheduler_minute, s.scheduler_timezone) == (0, 30, "Europe/Paris")
    assert s.scheduler_catchup_on_start is True
    assert s.scheduler_catchup_max_days == 3


def test_window_excludes_today():
    from app.services.scheduler import window

    # вчера, позавчера, позапозавчера — сегодняшнего (6 октября) нет
    assert window(TODAY) == (date(2026, 10, 3), date(2026, 10, 5))


def test_plan_with_huge_gap_still_covers_only_the_window(app_env):
    from app.db import session_scope
    from app.services.scheduler import plan_catch_up

    with session_scope() as db:
        db.add(_day(date(2026, 9, 1), "done"))
        db.add(_day(date(2026, 10, 4), "failed"))  # failed не считается собранным
    plan = plan_catch_up(TODAY)
    assert (plan["start"], plan["end"]) == (date(2026, 10, 3), date(2026, 10, 5))
    assert plan["missing"] == [date(2026, 10, 3), date(2026, 10, 4), date(2026, 10, 5)]
    assert plan["skipped_older"] is True
    assert TODAY not in plan["missing"]


def test_plan_window_done_when_all_three_days_done(app_env):
    from app.db import session_scope
    from app.services.scheduler import plan_catch_up

    with session_scope() as db:
        for d in (3, 4, 5):
            db.add(_day(date(2026, 10, d), "done"))
    plan = plan_catch_up(TODAY)
    assert plan["window_done"] is True and plan["missing"] == []


def test_plan_only_missing_day_inside_window(app_env):
    from app.db import session_scope
    from app.services.scheduler import plan_catch_up

    with session_scope() as db:
        db.add(_day(date(2026, 10, 3), "done"))
        db.add(_day(date(2026, 10, 4), "done"))
    plan = plan_catch_up(TODAY)
    assert plan["missing"] == [date(2026, 10, 5)]
    assert plan["skipped_older"] is False


def test_catch_up_submits_window_job_and_never_includes_today(app_env):
    from app.db import session_scope
    from app.services import scheduler

    with session_scope() as db:
        db.add(_day(date(2026, 9, 20), "done"))
    submit = _run_catch_up(scheduler)
    submit.assert_called_once()
    params = submit.call_args.kwargs["params"]
    assert params["trigger"] == "startup_catchup"
    assert params["start_date"] == "2026-10-03" and params["end_date"] == "2026-10-05"


def test_catch_up_does_nothing_when_window_is_done(app_env):
    from app.db import session_scope
    from app.services import scheduler

    with session_scope() as db:
        for d in (3, 4, 5):
            db.add(_day(date(2026, 10, d), "done"))
    _run_catch_up(scheduler).assert_not_called()


def test_catch_up_skipped_right_after_another_harvest(app_env):
    """Серия перезапусков хостинга не должна запускать сбор снова и снова."""
    from app.db import session_scope
    from app.models import Job
    from app.services import scheduler

    with session_scope() as db:
        db.add(Job(id="recent", type="harvest", created_at=datetime.utcnow()))
    _run_catch_up(scheduler).assert_not_called()
