"""Fetch and verify one sanitized USCPI release from GCS."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path, PurePosixPath

POINTER_SCHEMA = "uscpi_public_release_pointer_v1"
MANIFEST_SCHEMA = "uscpi_public_release_manifest_v1"
PCA_SCHEMA = "cpurnsa_pca_diagnostics_v1"
FORBIDDEN_PUBLIC_FIELDS = {
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
RELEASE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _split_gs_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith("gs://"):
        raise ValueError(f"release base must be a gs:// URI: {uri}")
    parts = uri[5:].rstrip("/").split("/", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(f"release base must include bucket and prefix: {uri}")
    return parts[0], parts[1]


def _gcs_uri(bucket: str, path: str) -> str:
    return f"gs://{bucket}/{path.lstrip('/')}"


def _download(source: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["gcloud", "storage", "cp", source, str(destination)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative_path(value: object) -> PurePosixPath:
    if not isinstance(value, str) or not value:
        raise ValueError("release file path must be a non-empty string")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "\\" in value:
        raise ValueError(f"unsafe release file path: {value}")
    return path


def fetch_release(*, release_base: str, output_dir: Path) -> dict[str, object]:
    bucket, prefix = _split_gs_uri(release_base)
    output_dir.mkdir(parents=True, exist_ok=True)
    pointer_path = output_dir / "latest.json"
    _download(_gcs_uri(bucket, f"{prefix}/latest.json"), pointer_path)
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    if pointer.get("schema_version") != POINTER_SCHEMA:
        raise ValueError("latest release pointer schema is incompatible")
    release_id = pointer.get("release_id")
    if not isinstance(release_id, str) or not RELEASE_ID_RE.fullmatch(release_id):
        raise ValueError("latest release pointer has an invalid release ID")
    manifest_path = pointer.get("manifest_path")
    expected_manifest_path = f"releases/{release_id}/manifest.json"
    if manifest_path != expected_manifest_path:
        raise ValueError("latest release pointer manifest path is inconsistent")

    manifest_local = output_dir / "manifest.json"
    _download(_gcs_uri(bucket, f"{prefix}/{manifest_path}"), manifest_local)
    if _sha256(manifest_local) != pointer.get("manifest_sha256"):
        raise ValueError("release manifest hash does not match latest pointer")
    manifest = json.loads(manifest_local.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != MANIFEST_SCHEMA:
        raise ValueError("release manifest schema is incompatible")
    if manifest.get("release_id") != release_id:
        raise ValueError("release manifest release ID mismatch")
    if manifest.get("curve_kind") != "shadow_posterior":
        raise ValueError("release is not explicitly a shadow-posterior release")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("release manifest contains no files")

    seen: set[str] = set()
    for item in files:
        if not isinstance(item, dict):
            raise ValueError("release manifest file entry is invalid")
        relative = _safe_relative_path(item.get("path"))
        relative_text = relative.as_posix()
        if relative_text in seen:
            raise ValueError(f"duplicate release file: {relative_text}")
        seen.add(relative_text)
        if not (relative_text == "cpurnsa_pca_diagnostics.json" or relative_text.startswith("snapshots/") and relative.suffix == ".csv"):
            raise ValueError(f"unexpected public release file: {relative_text}")
        local = output_dir / relative
        _download(_gcs_uri(bucket, f"{prefix}/releases/{release_id}/{relative_text}"), local)
        if _sha256(local) != item.get("sha256"):
            raise ValueError(f"release file hash mismatch: {relative_text}")

    pca_path = output_dir / "cpurnsa_pca_diagnostics.json"
    pca_payload = json.loads(pca_path.read_text(encoding="utf-8"))
    if pca_payload.get("schema_version") != PCA_SCHEMA:
        raise ValueError("PCA diagnostic schema is incompatible")
    pca_text = pca_path.read_text(encoding="utf-8")
    leaked = [field for field in FORBIDDEN_PUBLIC_FIELDS if f'"{field}"' in pca_text]
    if leaked:
        raise ValueError(f"private fields present in PCA diagnostic: {leaked}")

    return {
        "release_id": release_id,
        "latest_model_date": pointer.get("latest_model_date"),
        "snapshot_count": manifest.get("snapshot_count"),
        "output_dir": str(output_dir),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-base", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(fetch_release(release_base=args.release_base, output_dir=args.output_dir), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
