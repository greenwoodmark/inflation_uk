"""Validate the public softs hourly diagnostics artifact."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

SCHEMA = "softs_fit_diagnostics_v1"
SYMBOLS = ["CC", "CT", "KC", "SB"]
FORBIDDEN_FIELDS = {
    "source_uri",
    "source_generation",
    "raw_observations",
    "credentials",
    "private_key",
    "local_path",
    "traceback",
}


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is not allowed: {value}")


def _finite(value: object, field: str) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{field} must be finite numeric or null")


def validate(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    leaked = sorted(field for field in FORBIDDEN_FIELDS if f'"{field}"' in text)
    if leaked:
        raise ValueError(f"forbidden private fields present: {leaked}")
    payload = json.loads(text, parse_constant=_reject_constant)
    if payload.get("schema_version") != SCHEMA:
        raise ValueError("softs diagnostics schema is incompatible")
    if payload.get("symbols") != SYMBOLS:
        raise ValueError("softs diagnostics symbol universe is incompatible")
    sessions = payload.get("sessions")
    if not isinstance(sessions, list):
        raise ValueError("sessions must be a list")
    seen = set()
    for session in sessions:
        if not isinstance(session, dict):
            raise ValueError("session must be an object")
        symbol = session.get("symbol")
        session_date = session.get("session_date")
        key = (symbol, session_date)
        if symbol not in SYMBOLS or key in seen:
            raise ValueError(f"invalid or duplicate session: {key}")
        seen.add(key)
        if session.get("status") not in {"complete", "fit_not_persisted", "no_options_data"}:
            raise ValueError(f"invalid session status: {session.get('status')}")
        hours = session.get("hours")
        fits = session.get("fits")
        if not isinstance(hours, list) or not isinstance(fits, list):
            raise ValueError("hours and fits must be lists")
        for hour in hours:
            if not isinstance(hour, dict):
                raise ValueError("hour must be an object")
            if hour.get("status") not in {"complete", "incomplete", "empty"}:
                raise ValueError("invalid hourly status")
            for field in ("quote_rows", "bid_rows", "ask_rows", "paired_quotes", "valid_mid_quotes", "valid_puts", "valid_calls", "invalid_quotes", "final_quote_rows"):
                value = hour.get(field)
                if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                    raise ValueError(f"invalid hourly count: {field}")
        for fit in fits:
            if not isinstance(fit, dict) or fit.get("fit_status") != "success":
                raise ValueError("invalid fit row")
            for field in ("b", "g", "h", "medcouple", "skew", "kurt", "mad_err", "fwd", "cab"):
                _finite(fit.get(field), field)
            for field in ("DtE", "DtT", "OTM_puts", "OTM_calls"):
                value = fit.get(field)
                if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
                    raise ValueError(f"invalid fit count: {field}")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    payload = validate(args.path)
    print(f"Validated {args.path}: sessions={len(payload['sessions'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
