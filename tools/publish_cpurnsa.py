"""Build the public CPURNSA curve JSON from sanitized daily CSV snapshots."""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

LABELS = ("t", "t-1", "t-2", "t-7", "t-30")
OFFSETS = (0, 1, 2, 7, 30)
REQUIRED = {"as_of_date", "reference_month", "implied_zc_rate", "posterior_sd_bp"}


def read_snapshot(path: Path) -> dict:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 12:
        raise ValueError(f"{path} must contain exactly 12 rows")
    missing = REQUIRED.difference(rows[0])
    if missing:
        raise ValueError(f"{path} is missing columns: {sorted(missing)}")
    rows.sort(key=lambda row: row["reference_month"])
    date = rows[0]["as_of_date"]
    for row in rows:
        if row["as_of_date"] != date:
            raise ValueError(f"{path} contains multiple as_of_date values")
        float(row["implied_zc_rate"])
        if float(row["posterior_sd_bp"]) < 0:
            raise ValueError(f"{path} contains negative uncertainty")
    rates = [float(row["implied_zc_rate"]) * 100.0 for row in rows]
    sd = [float(row["posterior_sd_bp"]) / 100.0 for row in rows]
    return {
        "model_date": date,
        "status": rows[0].get("node_status", "unknown"),
        "model_version": rows[0].get("model_version", "unknown"),
        "training_cutoff": rows[0].get("training_cutoff", "unknown"),
        "source_trade_count": int(float(rows[0].get("source_trade_count", 0))),
        "reference_month": [row["reference_month"] for row in rows],
        "yoy_rate_percent": rates,
        "lower_percent": [rate - error for rate, error in zip(rates, sd, strict=True)],
        "upper_percent": [rate + error for rate, error in zip(rates, sd, strict=True)],
        "node_status": [row.get("node_status", "unknown") for row in rows],
    }


def publish(input_dir: Path, output_dir: Path) -> None:
    paths = sorted(input_dir.glob("*.csv"))
    snapshots_by_date = {snapshot["model_date"]: snapshot for snapshot in (read_snapshot(path) for path in paths)}
    snapshots = [snapshots_by_date[date] for date in sorted(snapshots_by_date)]
    if not snapshots:
        raise ValueError(f"No CSV snapshots found in {input_dir}")
    selected = []
    for label, offset in zip(LABELS, OFFSETS, strict=True):
        index = len(snapshots) - 1 - offset
        if index >= 0:
            snapshot = dict(snapshots[index])
            snapshot["label"] = label
            selected.append(snapshot)
    warning = None if len(selected) == len(LABELS) else "Insufficient historical snapshots for all requested offsets"
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / "cpurnsa_curve_history.json"
    previous = json.loads(target.read_text()) if target.exists() else None
    payload = {
        "schema_version": "cpurnsa_curve_history_v1",
        "generated_at_utc": previous.get("generated_at_utc") if previous and previous.get("snapshots") == selected and previous.get("warning") == warning else datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "latest_model_date": snapshots[-1]["model_date"],
        "available_snapshot_count": len(snapshots),
        "requested_labels": list(LABELS),
        "snapshots": selected,
        "warning": warning,
    }
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (output_dir / "health.json").write_text(json.dumps({
        "generated_at_utc": payload["generated_at_utc"],
        "latest_model_date": payload["latest_model_date"],
        "status": snapshots[-1]["status"],
        "model_version": snapshots[-1]["model_version"],
        "warning": warning,
    }, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", default="data/cpurnsa_snapshots")
    parser.add_argument("--output-dir", default="data")
    args = parser.parse_args()
    publish(Path(args.input_dir), Path(args.output_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
