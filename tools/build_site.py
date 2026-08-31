"""Assemble the public Firebase site and the local internal site."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

PUBLIC_FILES = (
    "index.html",
    "cpurnsa.html",
    "pca.html",
    "chart.html",
    "cpi_bayesian_update_example.html",
    "404.html",
    "CNAME",
)
PUBLIC_DATA_FILES = (
    "cpurnsa_curve_history.json",
    "cpurnsa_daily_commentary.json",
    "cpurnsa_pca_diagnostics.json",
    "health.json",
)
INTERNAL_FILES = (
    "logs.html",
    "bars/logs.html",
    "softs/logs.html",
    "model/logs.html",
)
INTERNAL_DATA_FILES = (
    "softs_diagnostics.json",
    "bars_etf_logs.json",
    "equity_vol_logs.json",
)

PUBLIC_LOG_TEXT = '<text x="950.48" y="110.08" font-size="192.05px" transform="rotate(-30.83, 950.48, 110.08)">a</text>'
INTERNAL_LOG_LINK = f'<a href="logs.html" aria-label="Open logs">{PUBLIC_LOG_TEXT}</a>'


def _copy(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise FileNotFoundError(f"Required site source is missing: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _clean_directory(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def build_variant(root: Path, output: Path, *, include_internal: bool) -> None:
    shared = root / "site" / "shared"
    data = root / "data"
    internal = root / "site" / "internal"
    _clean_directory(output)

    for relative in PUBLIC_FILES:
        _copy(shared / relative, output / relative)
    for filename in PUBLIC_DATA_FILES:
        _copy(data / filename, output / "data" / filename)

    if include_internal:
        # Keep the logs link on the internal landing page only. The shared
        # source is deliberately link-free so a direct public copy is safe.
        internal_index = output / "index.html"
        index_text = internal_index.read_text(encoding="utf-8")
        if PUBLIC_LOG_TEXT not in index_text:
            raise ValueError("Shared landing page is missing its final letter")
        internal_index.write_text(index_text.replace(PUBLIC_LOG_TEXT, INTERNAL_LOG_LINK), encoding="utf-8")
        for relative in INTERNAL_FILES:
            _copy(shared / relative, output / relative)
        for filename in INTERNAL_DATA_FILES:
            _copy(data / filename, output / "data" / filename)

    if include_internal:
        # Keep the shared CPURNSA page available to the internal overlay without
        # exposing this helper in the Firebase public artifact.
        _copy(shared / "cpurnsa.html", output / "cpurnsa_base.html")
        base_page = output / "cpurnsa_base.html"
        base_text = base_page.read_text(encoding="utf-8")
        pca_link = '<p><a href="pca.html">View six-driver PCA diagnostics →</a></p>'
        internal_links = (
            '<div style="display:flex; justify-content:space-between; align-items:center;">'
            '<a href="pca.html">View six-driver PCA diagnostics →</a>'
            '<a href="cpurnsa_example.html">Example of daily update →</a>'
            '</div>'
        )
        if pca_link not in base_text:
            raise ValueError("Shared CPURNSA page is missing its PCA link")
        base_page.write_text(base_text.replace(pca_link, internal_links), encoding="utf-8")
        # Present the existing shared Bayesian CPI example at the internal
        # CPURNSA example URL without changing its public URL.
        _copy(shared / "cpi_bayesian_update_example.html", output / "cpurnsa_example.html")
        if internal.exists():
            for source in internal.rglob("*"):
                if source.is_file():
                    _copy(source, output / source.relative_to(internal))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=("public", "internal", "all"), default="all")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    root = args.root.resolve()
    build_dir = root / "build"
    if args.target in {"public", "all"}:
        build_variant(root, build_dir / "public", include_internal=False)
    if args.target in {"internal", "all"}:
        build_variant(root, build_dir / "internal", include_internal=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
