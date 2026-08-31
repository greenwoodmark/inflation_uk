#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

python3 tools/build_site.py --target public

python3 tools/validate_uscpi_public_data.py \
  --snapshot-dir data/cpurnsa_snapshots \
  --pca-path data/cpurnsa_pca_diagnostics.json \
  --manifest-path data/uscpi_release_manifest.json \
  --curve-path data/cpurnsa_curve_history.json \
  --commentary-path data/cpurnsa_daily_commentary.json

python3 tools/validate_softs_diagnostics.py data/softs_diagnostics.json
python3 tools/validate_public_build.py build/public

echo
echo "Files prepared for Firebase production deployment:"
find build/public -type f -printf '%P\n' | sort
echo
echo "This will deploy build/public to the live Firebase Hosting site."
if [[ "${DGV_DEPLOY_CONFIRMATION:-}" == "DEPLOY" ]]; then
  confirmation="DEPLOY"
else
  read -r -p "Type DEPLOY to continue: " confirmation
fi
if [[ "$confirmation" != "DEPLOY" ]]; then
  echo "Deployment cancelled."
  exit 0
fi

firebase deploy --only hosting
