"""Build public CPURNSA curve JSON and rolling daily-move uncertainty bands."""

from __future__ import annotations

import argparse
import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path

LABELS = ("t", "t-1", "t-2", "t-7", "t-30")
OFFSETS = (0, 1, 2, 7, 30)
SNAPSHOT_REQUIRED = {"as_of_date", "reference_month", "implied_zc_rate", "model_version"}
LEGACY_COLUMNS = (
    "as_of_date",
    "reference_month",
    "implied_zc_rate",
    "posterior_sd_bp",
    "node_status",
    "model_version",
    "training_cutoff",
    "source_trade_count",
)
MOVE_WINDOW = 60
MIN_MOVE_OBSERVATIONS = 20
UNCERTAINTY_SOURCE = "historical_model_implied_moves"


def _number(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _rows_from_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        raw_rows = list(csv.reader(handle))
    if not raw_rows:
        return []
    if "as_of_date" in raw_rows[0]:
        header = raw_rows[0]
        return [dict(zip(header, row)) for row in raw_rows[1:]]
    if len(raw_rows[0]) != len(LEGACY_COLUMNS):
        raise ValueError(f"{path} has no recognised CSV header")
    return [dict(zip(LEGACY_COLUMNS, row)) for row in raw_rows]


def read_snapshot(path: Path) -> dict:
    rows = _rows_from_csv(path)
    if len(rows) != 12:
        raise ValueError(f"{path} must contain exactly 12 rows")
    missing = SNAPSHOT_REQUIRED.difference(rows[0])
    if missing:
        raise ValueError(f"{path} is missing columns: {sorted(missing)}")
    rows.sort(key=lambda row: row["reference_month"])
    date = rows[0]["as_of_date"] or path.stem
    references = [row["reference_month"] for row in rows]
    if len(set(references)) != len(references):
        raise ValueError(f"{path} contains duplicate reference months")
    rates = [_number(row["implied_zc_rate"]) for row in rows]
    if not any(rate is not None for rate in rates):
        raise ValueError(f"{path} contains no fitted curve nodes")
    for row in rows:
        if (row["as_of_date"] or date) != date:
            raise ValueError(f"{path} contains multiple as_of_date values")
    source_count = _number(rows[0].get("source_trade_count", rows[0].get("observation_count", 0)))
    model_version = rows[0].get("model_version", "unknown")
    curve_kind = rows[0].get("curve_kind") or (
        "canonical_deterministic" if model_version == "uscpi_six_driver_v1" else "historical_broker_marks"
    )
    return {
        "model_date": date,
        "status": rows[0].get("node_status", "unknown"),
        "fit_status": rows[0].get("fit_status", "unknown"),
        "curve_kind": curve_kind,
        "model_version": model_version,
        "matrix_version": rows[0].get("matrix_version", "unknown"),
        "base_month": rows[0].get("base_month", "unknown"),
        "training_cutoff": rows[0].get("training_cutoff", date),
        "source_trade_count": int(source_count or 0),
        "reference_month": references,
        "yoy_rate_percent": [rate * 100.0 if rate is not None else None for rate in rates],
        "node_status": [row.get("node_status", "unknown") for row in rows],
    }


def _eligible_for_moves(snapshot: dict) -> bool:
    return snapshot["fit_status"] in {"fit_succeeded", "unknown"} and snapshot["status"] not in {
        "rank_deficient",
        "ill_conditioned",
        "no_observation",
    }


def _same_frame(left: dict, right: dict) -> bool:
    left_base = left.get("base_month", "unknown")
    right_base = right.get("base_month", "unknown")
    return left_base == "unknown" or right_base == "unknown" or left_base == right_base


def _rate_for_reference(snapshot: dict, reference_month: str) -> float | None:
    try:
        index = snapshot["reference_month"].index(reference_month)
    except ValueError:
        return None
    return _number(snapshot["yoy_rate_percent"][index])


FALLBACK_UNCERTAINTY_SOURCE = "historical_barclays_curve_moves"


def _calculate_move_uncertainty(
    snapshot: dict,
    history: list[dict],
    *,
    by_reference_month: bool,
    same_frame_only: bool,
) -> dict:
    standard_deviations: list[float | None] = []
    observation_counts: list[int] = []
    series_count = len(snapshot["reference_month"])
    for series_index in range(series_count):
        points: list[float] = []
        previous: float | None = None
        for candidate in history:
            if not _eligible_for_moves(candidate):
                continue
            if same_frame_only and not _same_frame(candidate, snapshot):
                continue
            if by_reference_month:
                rate = _rate_for_reference(candidate, snapshot["reference_month"][series_index])
            elif series_index >= len(candidate["yoy_rate_percent"]):
                rate = None
            else:
                rate = _number(candidate["yoy_rate_percent"][series_index])
            if rate is None:
                continue
            if previous is not None:
                points.append(rate - previous)
            previous = rate
        moves_bp = [move * 100.0 for move in points[-MOVE_WINDOW:]]
        observation_counts.append(len(moves_bp))
        if len(moves_bp) < MIN_MOVE_OBSERVATIONS:
            standard_deviations.append(None)
            continue
        mean = sum(moves_bp) / len(moves_bp)
        variance = sum((move - mean) ** 2 for move in moves_bp) / (len(moves_bp) - 1)
        standard_deviations.append(math.sqrt(variance))
    available = sum(value is not None for value in standard_deviations)
    return {
        "daily_move_sd_bp": standard_deviations,
        "daily_move_observation_count": observation_counts,
        "uncertainty_source": UNCERTAINTY_SOURCE,
        "uncertainty_window": MOVE_WINDOW,
        "uncertainty_min_observations": MIN_MOVE_OBSERVATIONS,
        "uncertainty_status": "available" if available == len(standard_deviations) else "insufficient_history",
    }


def _move_uncertainty(snapshot: dict, history: list[dict]) -> dict:
    same_month = _calculate_move_uncertainty(
        snapshot, history, by_reference_month=True, same_frame_only=True
    )
    if same_month["uncertainty_status"] == "available":
        same_month["uncertainty_alignment"] = "same_reference_month"
        return same_month
    node_position = _calculate_move_uncertainty(
        snapshot, history, by_reference_month=False, same_frame_only=False
    )
    if node_position["uncertainty_status"] == "available":
        node_position["uncertainty_source"] = FALLBACK_UNCERTAINTY_SOURCE
        node_position["uncertainty_alignment"] = "node_position"
        return node_position
    same_month["uncertainty_alignment"] = "same_reference_month"
    return same_month


def _decorate_snapshots(snapshots: list[dict]) -> list[dict]:
    decorated = []
    for index, snapshot in enumerate(snapshots):
        item = dict(snapshot)
        item.update(_move_uncertainty(snapshot, snapshots[: index + 1]))
        decorated.append(item)
    return decorated


def discover_snapshots(input_dir: Path) -> list[dict]:
    candidates = sorted(input_dir.glob("*.csv"))
    snapshots = [read_snapshot(path) for path in candidates]
    unique = {snapshot["model_date"]: snapshot for snapshot in snapshots}
    return _decorate_snapshots([unique[date] for date in sorted(unique)])


def select_history(snapshots: list[dict]) -> list[dict]:
    if not snapshots:
        raise ValueError("No CSV snapshots found")
    selected = []
    for label, offset in zip(LABELS, OFFSETS, strict=True):
        index = len(snapshots) - 1 - offset
        if index >= 0:
            snapshot = dict(snapshots[index])
            snapshot["label"] = label
            selected.append(snapshot)
    return selected


def publish(input_dir: Path, output_dir: Path) -> None:
    snapshots = discover_snapshots(input_dir)
    selected = select_history(snapshots)
    history_warning = None if len(selected) == len(LABELS) else "Insufficient historical snapshots for all requested offsets"
    latest = snapshots[-1]
    uncertainty_warning = None if latest["uncertainty_status"] == "available" else "Daily move uncertainty unavailable until at least 20 valid moves per reference month"
    warnings = [warning for warning in (history_warning, uncertainty_warning) if warning]
    warning = "; ".join(warnings) or None
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / "cpurnsa_curve_history.json"
    previous = None
    if target.exists():
        try:
            previous = json.loads(target.read_text())
        except json.JSONDecodeError:
            previous = None
    stable_payload = {
        "snapshots": selected,
        "warning": warning,
        "available_snapshot_count": len(snapshots),
    }
    generated_at = (
        previous.get("generated_at_utc")
        if previous and all(previous.get(key) == value for key, value in stable_payload.items())
        else datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    )
    payload = {
        "schema_version": "cpurnsa_curve_history_v2",
        "generated_at_utc": generated_at,
        "latest_model_date": latest["model_date"],
        "available_snapshot_count": len(snapshots),
        "requested_labels": list(LABELS),
        "uncertainty_definition": "One-standard-deviation rolling moves in basis points, using same reference months when available and otherwise relative curve-node positions",
        "snapshots": selected,
        "warning": warning,
    }
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (output_dir / "health.json").write_text(json.dumps({
        "generated_at_utc": payload["generated_at_utc"],
        "latest_model_date": payload["latest_model_date"],
        "status": latest["status"],
        "model_version": latest["model_version"],
        "uncertainty_status": latest["uncertainty_status"],
        "uncertainty_source": latest["uncertainty_source"],
        "uncertainty_alignment": latest["uncertainty_alignment"],
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
