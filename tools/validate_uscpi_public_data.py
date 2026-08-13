"""Validate the committed public USCPI curve and PCA handoff."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path, PurePosixPath

FORBIDDEN_FIELDS = {
    "posterior_mean",
    "prior_mean",
    "posterior_covariance",
    "prior_covariance",
    "source_uri",
    "source_generation",
    "run_id",
    "raw_observations",
    "dissemination_id",
}
PCA_SCHEMA = "cpurnsa_pca_diagnostics_v1"
COMMENTARY_SCHEMA = "cpurnsa_daily_commentary_v1"
TRADE_COUNT_DEFINITION = (
    "Accepted fit-observation endpoint-support counts. Exact reference CPI trades count "
    "once at their endpoint; interpolated trades count once at both final endpoint nodes. "
    "The known base CPI node is excluded."
)
MANIFEST_SCHEMA = "uscpi_public_release_manifest_v1"
SNAPSHOT_REQUIRED = {"as_of_date", "reference_month", "implied_zc_rate", "model_version"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _optional_trade_count(value: object, *, path: Path, row_number: int) -> int | None:
    text = "" if value is None else str(value).strip()
    if not text:
        return None
    if not text.isascii() or not text.isdecimal():
        raise ValueError(f"{path} row {row_number} has a non-negative integer node_trade_count")
    return int(text)


def _validate_snapshot(path: Path) -> dict[str, str]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 12:
        raise ValueError(f"{path} must contain exactly 12 rows")
    header = set(rows[0])
    forbidden = sorted(FORBIDDEN_FIELDS.intersection(header))
    if forbidden:
        raise ValueError(f"private fields present in {path}: {forbidden}")
    missing = sorted(SNAPSHOT_REQUIRED.difference(header))
    if missing:
        raise ValueError(f"{path} is missing columns: {missing}")
    references = [row["reference_month"] for row in rows]
    if len(set(references)) != 12:
        raise ValueError(f"{path} contains duplicate reference months")
    dates = {row["as_of_date"] or path.stem for row in rows}
    if len(dates) != 1:
        raise ValueError(f"{path} contains multiple as_of_date values")
    for row_number, row in enumerate(rows, start=2):
        value = row["implied_zc_rate"].strip()
        if value.lower() not in {"", "nan", "none"} and not math.isfinite(float(value)):
            raise ValueError(f"{path} contains a non-finite implied_zc_rate")
        if "node_trade_count" in header:
            _optional_trade_count(row.get("node_trade_count"), path=path, row_number=row_number)
    return rows[0]


def _validate_pca(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    leaked = sorted(field for field in FORBIDDEN_FIELDS if f'"{field}"' in text)
    if leaked:
        raise ValueError(f"private fields present in PCA JSON: {leaked}")
    payload = json.loads(text)
    if payload.get("schema_version") != PCA_SCHEMA:
        raise ValueError("PCA schema is incompatible")
    if payload.get("diagnostic_kind") != "uscpi_shadow_posterior_driver_pca":
        raise ValueError("PCA diagnostic kind is incompatible")
    if payload.get("frame_policy", {}).get("cross_reseed_transition_included") is not False:
        raise ValueError("PCA must exclude cross-reseed transitions")
    current_frame = payload.get("current_frame") or {}
    current_state_count = int(current_frame.get("state_count", 0))
    if current_state_count < 1:
        raise ValueError("PCA current frame needs at least one state")
    if current_state_count < 2:
        # A publication reseed starts a new coordinate frame. Its first state
        # is valid for publication, but PCA needs a second state before it can
        # produce variance estimates in that frame.
        if current_frame.get("natural_units") is not None or current_frame.get("standardized_units") is not None:
            raise ValueError("single-state PCA frame must not contain variance estimates")
    return payload


def _validate_manifest(
    path: Path,
    snapshot_dir: Path,
    pca_path: Path,
) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != MANIFEST_SCHEMA:
        raise ValueError("release manifest schema is incompatible")
    if payload.get("curve_kind") != "shadow_posterior":
        raise ValueError("release manifest is not a shadow-posterior release")
    files = payload.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("release manifest contains no files")
    seen: set[str] = set()
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise ValueError("invalid release manifest file entry")
        relative = PurePosixPath(item["path"])
        if relative.is_absolute() or ".." in relative.parts or "\\" in item["path"]:
            raise ValueError(f"unsafe manifest path: {item['path']}")
        relative_text = relative.as_posix()
        if relative_text in seen:
            raise ValueError(f"duplicate manifest path: {relative_text}")
        seen.add(relative_text)
        if relative_text == "cpurnsa_pca_diagnostics.json":
            local = pca_path
        elif len(relative.parts) == 2 and relative.parts[0] == "snapshots" and relative.suffix == ".csv":
            local = snapshot_dir / relative.name
            row = _validate_snapshot(local)
            if row.get("curve_kind") != "shadow_posterior":
                raise ValueError(f"release snapshot is not shadow_posterior: {local}")
        else:
            raise ValueError(f"unexpected manifest path: {relative_text}")
        if _sha256(local) != item.get("sha256"):
            raise ValueError(f"manifest hash mismatch: {relative_text}")
    return payload


def _validate_commentary(path: Path, snapshot_dir: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    leaked = sorted(field for field in FORBIDDEN_FIELDS if f'"{field}"' in text)
    if leaked:
        raise ValueError(f"private fields present in commentary JSON: {leaked}")
    payload = json.loads(text)
    if payload.get("schema_version") != COMMENTARY_SCHEMA:
        raise ValueError("commentary schema is incompatible")
    if payload.get("trade_count_definition") != TRADE_COUNT_DEFINITION:
        raise ValueError("commentary trade-count definition is missing or incompatible")
    entries = payload.get("entries")
    if not isinstance(entries, dict) or not entries:
        raise ValueError("commentary contains no entries")
    snapshot_dates = {item.stem for item in snapshot_dir.glob("*.csv")}
    if payload.get("latest_model_date") not in entries:
        raise ValueError("commentary latest_model_date is missing from entries")
    for date, entry in entries.items():
        if date not in snapshot_dates:
            raise ValueError(f"commentary has no matching public snapshot: {date}")
        if not isinstance(entry, dict) or entry.get("trace_id") != date:
            raise ValueError(f"commentary trace_id does not match date: {date}")
        references = entry.get("reference_month")
        moves = entry.get("move_bp")
        trade_counts = entry.get("trade_count")
        if not isinstance(references, list) or not isinstance(moves, list) or not isinstance(trade_counts, list):
            raise ValueError(f"commentary arrays are missing: {date}")
        if len(references) != len(moves) or len(references) != len(trade_counts):
            raise ValueError(f"commentary arrays are misaligned: {date}")
        for trade_count in trade_counts:
            if trade_count is not None and (
                isinstance(trade_count, bool)
                or not isinstance(trade_count, int)
                or trade_count < 0
            ):
                raise ValueError(f"commentary contains an invalid trade count: {date}")
        for move in moves:
            if move is not None and (not isinstance(move, (int, float)) or not math.isfinite(move)):
                raise ValueError(f"commentary contains a non-finite move: {date}")
        max_move = entry.get("max_abs_move_bp")
        finite_moves = [abs(move) for move in moves if move is not None]
        if max_move is not None:
            if not isinstance(max_move, (int, float)) or not math.isfinite(max_move):
                raise ValueError(f"commentary max move is not finite: {date}")
            if not finite_moves or not math.isclose(max_move, max(finite_moves), rel_tol=1e-9, abs_tol=1e-9):
                raise ValueError(f"commentary max move is inconsistent: {date}")
        for field in ("uncertainty_sd_bp", "uncertainty_ratio"):
            value = entry.get(field)
            if value is not None and (not isinstance(value, (int, float)) or not math.isfinite(value)):
                raise ValueError(f"commentary {field} is not finite: {date}")
        if entry.get("magnitude_class") not in {
            "unavailable",
            "within_historical_range",
            "modest",
            "meaningful",
            "sharp",
        }:
            raise ValueError(f"unknown commentary magnitude class: {date}")
        status = entry.get("comparison_status")
        if status not in {
            "available",
            "no_prior_snapshot",
            "unavailable_frame_boundary",
            "insufficient_valid_nodes",
        }:
            raise ValueError(f"unknown commentary comparison status: {date}")
    return {
        "schema_version": payload["schema_version"],
        "entry_count": len(entries),
        "latest_model_date": payload["latest_model_date"],
    }


def validate_public_data(
    *,
    snapshot_dir: Path,
    pca_path: Path,
    manifest_path: Path,
    curve_path: Path | None = None,
    commentary_path: Path | None = None,
) -> dict[str, object]:
    snapshots = sorted(snapshot_dir.glob("*.csv"))
    if not snapshots:
        raise ValueError(f"no public snapshots found in {snapshot_dir}")
    for path in snapshots:
        _validate_snapshot(path)
    pca = _validate_pca(pca_path)
    manifest = _validate_manifest(manifest_path, snapshot_dir, pca_path)
    if curve_path is not None:
        curve = json.loads(curve_path.read_text(encoding="utf-8"))
        if not curve.get("snapshots"):
            raise ValueError("public curve JSON contains no snapshots")
        for snapshot in curve["snapshots"]:
            if len(snapshot.get("reference_month", [])) != 12:
                raise ValueError("public curve snapshot does not contain 12 reference months")
            if len(snapshot.get("yoy_rate_percent", [])) != 12:
                raise ValueError("public curve snapshot does not contain 12 rates")
    commentary = None
    if commentary_path is not None:
        commentary = _validate_commentary(commentary_path, snapshot_dir)
    return {
        "snapshot_count": len(snapshots),
        "release_snapshot_count": manifest.get("snapshot_count"),
        "latest_model_date": manifest.get("latest_model_date"),
        "pca_current_frame_id": pca.get("current_frame_id"),
        "commentary": commentary,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-dir", type=Path, default=Path("data/cpurnsa_snapshots"))
    parser.add_argument("--pca-path", type=Path, default=Path("data/cpurnsa_pca_diagnostics.json"))
    parser.add_argument("--manifest-path", type=Path, default=Path("data/uscpi_release_manifest.json"))
    parser.add_argument("--curve-path", type=Path, default=Path("data/cpurnsa_curve_history.json"))
    parser.add_argument("--commentary-path", type=Path)
    args = parser.parse_args()
    result = validate_public_data(
        snapshot_dir=args.snapshot_dir,
        pca_path=args.pca_path,
        manifest_path=args.manifest_path,
        curve_path=args.curve_path,
        commentary_path=args.commentary_path,
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
