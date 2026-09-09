import sys
from pathlib import Path

sys.path.insert(0, str(Path("/home/mark/inflation_uk/tools")))

from generate_equity_vol_logs import GH5_EVENTS, generate_report  # noqa: E402


def test_gh5_event_allowlist_excludes_legacy_and_non_fit_events(monkeypatch):
    import generate_equity_vol_logs as module

    events = [
        {"date": "2026-08-31", "event": "GH3_SKIPPED", "model_version": "gh5_v1", "timestamp_utc": "2026-09-01T00:00:00Z"},
        {"date": "2026-08-31", "event": "BACKFILL_WRITTEN", "timestamp_utc": "2026-09-01T00:01:00Z"},
        {"date": "2026-08-31", "event": "GH5_WRITTEN", "model_version": "gh5_v1", "timestamp_utc": "2026-09-01T00:02:00Z"},
    ]
    monkeypatch.setattr(module, "SYMBOLS", ("TIP",))
    monkeypatch.setattr(module, "_read_events", lambda symbol: events)
    monkeypatch.setattr(module, "_load_persisted_fits", lambda symbol: {})

    report = generate_report()

    assert GH5_EVENTS == {"GH5_WRITTEN", "GH5_SKIPPED", "GH5_ERROR"}
    assert [row["event"] for row in report["rows"]] == ["GH5_WRITTEN"]
    assert all(row["model_version"] == "gh5_v1" for row in report["rows"])


def test_legacy_gh3_event_cannot_replace_a_gh5_event_for_same_date(monkeypatch):
    import generate_equity_vol_logs as module

    events = [
        {"date": "2026-08-31", "event": "GH5_WRITTEN", "model_version": "gh5_v1", "timestamp_utc": "2026-09-01T00:00:00Z"},
        {"date": "2026-08-31", "event": "GH3_SKIPPED", "timestamp_utc": "2026-09-01T00:01:00Z"},
    ]
    monkeypatch.setattr(module, "SYMBOLS", ("TIP",))
    monkeypatch.setattr(module, "_read_events", lambda symbol: events)
    monkeypatch.setattr(module, "_load_persisted_fits", lambda symbol: {})

    report = generate_report()

    assert len(report["rows"]) == 1
    assert report["rows"][0]["event"] == "GH5_WRITTEN"
