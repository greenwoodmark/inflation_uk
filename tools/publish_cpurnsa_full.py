"""Publish the private Bayesian shadow full-curve history for the internal site."""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EXPECTED_TERMS = list(range(1, 361))
EXPECTED_NODE_STATUSES = {"front_fixed", "long_shadow_posterior"}
HISTORY_LABELS = ("t", "t-1", "t-2", "t-7", "t-30")
HISTORY_OFFSETS = (0, 1, 2, 7, 30)
DEFAULT_SOURCE = Path(
    "/home/mark/trading_env/artifacts/uscpi_long_shadow_history_v4/snapshots"
)
DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "site/internal/data/cpurnsa_full_curve_history.json"


def _finite_number(value: Any, *, field: str, date: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{date}: {field} contains a non-finite value")
    return number


def _optional_finite_number(value: Any, *, field: str, date: str) -> float | None:
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _normalise_snapshot(path: Path) -> dict[str, Any]:
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    date = str(snapshot.get("model_date") or path.stem)
    if snapshot.get("synthetic_prior") is not True:
        raise ValueError(f"{date}: snapshot is not marked synthetic_prior")
    if snapshot.get("status") != "long_shadow_posterior":
        raise ValueError(f"{date}: unexpected status {snapshot.get('status')!r}")

    terms = snapshot.get("term_months")
    rates = snapshot.get("inflation_rate_percent")
    statuses = snapshot.get("node_status")
    labels = snapshot.get("knot_label")
    if terms != EXPECTED_TERMS:
        raise ValueError(f"{date}: term_months must be exactly 1 through 360")
    if not isinstance(rates, list) or len(rates) != len(EXPECTED_TERMS):
        raise ValueError(f"{date}: inflation_rate_percent must contain 360 values")
    if not isinstance(statuses, list) or len(statuses) != len(EXPECTED_TERMS):
        raise ValueError(f"{date}: node_status must contain 360 values")
    if not isinstance(labels, list) or len(labels) != len(EXPECTED_TERMS):
        raise ValueError(f"{date}: knot_label must contain 360 values")
    if set(statuses) - EXPECTED_NODE_STATUSES:
        raise ValueError(f"{date}: unexpected node status values")

    return {
        "model_date": date,
        "status": snapshot["status"],
        "curve_kind": snapshot.get("curve_kind", "short_plus_long_shadow_posterior"),
        "schema_version": snapshot.get("schema_version"),
        "prior_version": snapshot.get("prior_version"),
        "synthetic_prior": True,
        "synthetic_prior_annual_rate": _finite_number(
            snapshot.get("synthetic_prior_annual_rate"),
            field="synthetic_prior_annual_rate",
            date=date,
        ),
        "model_version": snapshot.get("model_version"),
        "base_month": snapshot.get("base_month"),
        "term_months": terms,
        "inflation_rate_percent": [
            _finite_number(value, field="inflation_rate_percent", date=date)
            for value in rates
        ],
        "node_status": statuses,
        "knot_label": labels,
        "standardized_observation_count": int(snapshot.get("standardized_observation_count", 0)),
        "missing_conditional_count": int(snapshot.get("missing_conditional_count", 0)),
        "observed_terms": [int(value) for value in snapshot.get("observed_terms", [])],
        "observation_rank": int(snapshot.get("observation_rank", 0)),
        "posterior_rmse_bp": _optional_finite_number(
            snapshot.get("posterior_rmse_bp"), field="posterior_rmse_bp", date=date
        ),
    }


def publish(source_dir: Path, output_path: Path) -> dict[str, Any]:
    paths = sorted(source_dir.glob("*.json"))
    if not paths:
        raise FileNotFoundError(f"No shadow snapshots found in {source_dir}")
    all_snapshots = [_normalise_snapshot(path) for path in paths]
    all_snapshots.sort(key=lambda snapshot: snapshot["model_date"], reverse=True)
    dates = [snapshot["model_date"] for snapshot in all_snapshots]
    if len(dates) != len(set(dates)):
        raise ValueError("Shadow snapshot dates are not unique")
    snapshots = all_snapshots
    payload = {
        "schema_version": "uscpi_full_curve_shadow_history_v1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "history_kind": "private_shadow_only",
        "synthetic_prior": True,
        "synthetic_prior_annual_rate": snapshots[0]["synthetic_prior_annual_rate"],
        "source_directory": str(source_dir),
        "available_snapshot_count": len(snapshots),
        "snapshot_count": len(snapshots),
        "requested_labels": list(HISTORY_LABELS),
        "requested_offsets": list(HISTORY_OFFSETS),
        "latest_model_date": snapshots[0]["model_date"],
        "earliest_model_date": snapshots[-1]["model_date"],
        "snapshots": snapshots,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    payload = publish(args.source_dir, args.output)
    print(
        f"Published {payload['snapshot_count']} private shadow snapshots "
        f"({payload['earliest_model_date']} to {payload['latest_model_date']}) to {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
