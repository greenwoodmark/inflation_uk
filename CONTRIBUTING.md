# Contributing

## Git workflow for generated public data

The CPURNSA public JSON files below are generated outputs owned by the GitHub Actions workflow in `.github/workflows/update-cpurnsa-data.yml`:

- `data/cpurnsa_curve_history.json`
- `data/cpurnsa_daily_commentary.json`
- `data/health.json`

Commit the source inputs, not a locally rebuilt copy of these files. After the source commit reaches `main`, the workflow validates the inputs, rebuilds the public JSON, validates the public build, and commits the generated outputs as `github-actions[bot]`.

Avoid `git add .` for routine updates. Stage the intended source files explicitly, for example:

```bash
git pull --rebase origin main
git add data/cpurnsa_snapshots/ data/cpurnsa_pca_diagnostics.json data/uscpi_release_manifest.json
git commit -m "Update CPURNSA source data"
git push origin main
```

If you intentionally change a generator or its inputs, do not also stage the three generated CPURNSA files. Let the workflow regenerate them. Wait for the workflow to finish before starting another update or syncing again.

## If sync reports conflicts

Do not create another commit while a rebase is paused. First inspect the state:

```bash
git status
git diff --name-only --diff-filter=U
```

For the three generated CPURNSA files, the conflict is normally safe to resolve by retaining one complete generated version, then continuing the rebase:

```bash
git add data/cpurnsa_curve_history.json \
        data/cpurnsa_daily_commentary.json \
        data/health.json
GIT_EDITOR=true git rebase --continue
```

If the conflict contains substantive data differences rather than only generation metadata, stop and compare the source inputs and the workflow run before choosing a side. Never resolve by blindly combining JSON fragments.

Before committing or syncing, confirm that the working tree is not in a rebase or merge and that the remote is current:

```bash
git status --short --branch
git pull --rebase origin main
```
