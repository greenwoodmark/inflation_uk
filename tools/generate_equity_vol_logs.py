#!/usr/bin/env python3
"""Generate the internal TIP/TLT GH3 operational snapshot from structured logs."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

SYMBOLS = ("TIP", "TLT")
DEFAULT_LOG_DIR = Path(os.environ.get("EQUITY_VOL_LOG_DIR", "/home/mark/trading_env/data"))
WEBSITE_ROOT = Path(os.environ.get("ETF_WEBSITE_ROOT", "/home/mark/inflation_uk"))
OUTPUT_PATH = WEBSITE_ROOT / "data" / "equity_vol_logs.json"
EVENT_RE = re.compile(r"^(?P<timestamp>\S+) \[EQUITY_VOL\] (?P<body>\{.*\})$")


def _read_events(symbol: str) -> list[dict]:
    path = DEFAULT_LOG_DIR / f"fit_{symbol}.log"
    if not path.is_file():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = EVENT_RE.match(line.strip())
        if not match:
            continue
        try:
            event = json.loads(match.group("body"))
        except json.JSONDecodeError:
            continue
        event["timestamp_utc"] = match.group("timestamp")
        event["symbol"] = symbol
        events.append(event)
    return events


def _status(event: str) -> str:
    return {
        "GH3_WRITTEN": "fit written",
        "GH3_SKIPPED": "fit already present",
        "GH3_ERROR": "fit error",
        "BACKFILL_WRITTEN": "input ready",
        "ERROR": "input error",
    }.get(event, event.lower().replace("_", " "))


def generate_report() -> dict:
    rows = []
    for symbol in SYMBOLS:
        events = _read_events(symbol)
        by_date: dict[str, dict] = {}
        for event in events:
            date = event.get("date")
            if not date:
                continue
            current = by_date.get(date)
            if current is None or event["timestamp_utc"] >= current["timestamp_utc"]:
                by_date[date] = {
                    "symbol": symbol,
                    "date": date,
                    "status": _status(str(event.get("event", "unknown"))),
                    "event": event.get("event", "unknown"),
                    "timestamp_utc": event["timestamp_utc"],
                    "rows": event.get("rows"),
                    "mad_err": event.get("mad_err"),
                    "error": event.get("error"),
                    "stage": event.get("stage"),
                    "uri": event.get("uri"),
                    "strike_references": event.get("strike_references"),
                }
        rows.extend(by_date.values())
    rows.sort(key=lambda row: (row["date"], row["symbol"]), reverse=True)
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": str(DEFAULT_LOG_DIR),
        "symbols": list(SYMBOLS),
        "event_count": len(rows),
        "rows": rows,
    }


def main() -> int:
    report = generate_report()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUTPUT_PATH.with_name(f".{OUTPUT_PATH.name}.tmp")
    temporary.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, OUTPUT_PATH)
    print(f"Generated {OUTPUT_PATH}: rows={len(report['rows'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
