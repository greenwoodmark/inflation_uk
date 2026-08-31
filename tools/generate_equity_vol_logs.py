#!/usr/bin/env python3
"""Generate the internal TIP/TLT GH3 operational snapshot from structured logs."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

try:
    import pyarrow.dataset as ds
except ImportError:  # pragma: no cover - the website build environment may be log-only
    ds = None

SYMBOLS = ("TIP", "TLT")
DEFAULT_LOG_DIR = Path(os.environ.get("EQUITY_VOL_LOG_DIR", "/home/mark/trading_env/data"))
WEBSITE_ROOT = Path(os.environ.get("ETF_WEBSITE_ROOT", "/home/mark/inflation_uk"))
OUTPUT_PATH = WEBSITE_ROOT / "data" / "equity_vol_logs.json"
OPTIONS_BASE = "gs://systematicpositiveskew/options_data"
EVENT_RE = re.compile(r"^(?P<timestamp>\S+) \[EQUITY_VOL\] (?P<body>\{.*\})$")

# Monthly fit files contain one persisted row per valuation date. Cache them by
# URI because the event log can contain many historical skipped events.
_PERSISTED_FITS: dict[str, dict[str, dict]] = {}
_PYARROW_WARNING_SHOWN = False


def _fit_uri(symbol: str, date: str) -> str:
    persisted_symbol = {"TIP": "TIP_UP", "TLT": "TLT_UP"}[symbol]
    month = str(int(date[5:7]))
    return (
        f"{OPTIONS_BASE}/symbol={persisted_symbol}/opt_expiry=1m/"
        f"year={date[:4]}/month={month}/data.parquet"
    )


def _persisted_fit(event: dict, symbol: str) -> dict:
    """Return persisted diagnostics for a skipped event, if available."""
    global _PYARROW_WARNING_SHOWN
    if ds is None:
        if not _PYARROW_WARNING_SHOWN:
            print("[INFO] PyArrow unavailable; skipped fit enrichment disabled")
            _PYARROW_WARNING_SHOWN = True
        return {}

    date = str(event.get("date", ""))
    if not date:
        return {}
    uri = str(event.get("uri") or _fit_uri(symbol, date))
    if uri not in _PERSISTED_FITS:
        try:
            table = ds.dataset(uri, format="parquet").to_table(
                columns=["date", "mad_err", "OTM_puts", "OTM_calls"]
            )
            columns = table.to_pydict()
            _PERSISTED_FITS[uri] = {
                str(row_date): {
                    "mad_err": mad_err,
                    "OTM_puts": otm_puts,
                    "OTM_calls": otm_calls,
                }
                for row_date, mad_err, otm_puts, otm_calls in zip(
                    columns["date"],
                    columns["mad_err"],
                    columns["OTM_puts"],
                    columns["OTM_calls"],
                    strict=True,
                )
            }
        except Exception as exc:
            print(f"[INFO] Could not enrich skipped fit from {uri}: {exc}")
            _PERSISTED_FITS[uri] = {}
    fit = _PERSISTED_FITS[uri].get(date, {})
    if not fit:
        return {}
    return {
        "rows": int(fit["OTM_puts"] + fit["OTM_calls"]),
        "mad_err": fit["mad_err"],
    }


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
                report_row = {
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
                if (
                    report_row["event"] == "GH3_SKIPPED"
                    and (report_row["rows"] is None or report_row["mad_err"] is None)
                ):
                    persisted = _persisted_fit(event, symbol)
                    if report_row["rows"] is None:
                        report_row["rows"] = persisted.get("rows")
                    if report_row["mad_err"] is None:
                        report_row["mad_err"] = persisted.get("mad_err")
                by_date[date] = report_row
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
