"""Validate that a generated site contains only the approved public artifact."""

from __future__ import annotations

import argparse
from pathlib import Path

ALLOWED_FILES = {
    "index.html",
    "cpurnsa.html",
    "pca.html",
    "chart.html",
    "cpi_bayesian_update_example.html",
    "404.html",
    "CNAME",
    "softs/logs.html",
    "data/cpurnsa_curve_history.json",
    "data/cpurnsa_daily_commentary.json",
    "data/cpurnsa_pca_diagnostics.json",
    "data/health.json",
    "data/softs_diagnostics.json",
}
REQUIRED_FILES = ALLOWED_FILES - {"CNAME"}
FORBIDDEN_NAME_PARTS = ("private", "internal", "secret", ".env", "__pycache__")
FORBIDDEN_CONTENT = (
    "private_key",
    "client_secret",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "raw_observations",
    "posterior_covariance",
    "prior_covariance",
)


def validate(public_dir: Path) -> None:
    if not public_dir.is_dir():
        raise ValueError(f"Public build directory does not exist: {public_dir}")
    actual = {
        path.relative_to(public_dir).as_posix()
        for path in public_dir.rglob("*")
        if path.is_file()
    }
    unexpected = sorted(actual - ALLOWED_FILES)
    if unexpected:
        raise ValueError(f"Unexpected files in public build: {unexpected}")
    missing = sorted(REQUIRED_FILES - actual)
    if missing:
        raise ValueError(f"Required files missing from public build: {missing}")
    for relative in sorted(actual):
        if any(part.lower().find(token) >= 0 for part in Path(relative).parts for token in FORBIDDEN_NAME_PARTS):
            raise ValueError(f"Private-looking path in public build: {relative}")
        path = public_dir / relative
        if path.suffix.lower() in {".html", ".js", ".json", ".css", ".txt"}:
            text = path.read_text(encoding="utf-8")
            leaked = [token for token in FORBIDDEN_CONTENT if token in text]
            if leaked:
                raise ValueError(f"Forbidden content markers in {relative}: {leaked}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("public_dir", type=Path, nargs="?", default=Path("build/public"))
    args = parser.parse_args()
    validate(args.public_dir.resolve())
    print(f"Public build is valid: {args.public_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
