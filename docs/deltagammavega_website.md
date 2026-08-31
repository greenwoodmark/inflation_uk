
# Delta Gamma Vega Website — Setup

This is the ordinary project documentation for `www.deltagammavega.com`. The website source of truth is `/home/mark/inflation_uk`; this note is not Kiro steering configuration.

## Hosting and DNS

- Firebase project: `deltagammavega-46877`.
- Firebase Hosting config: `/home/mark/inflation_uk/firebase.json`.
- Firebase Hosting deploys only the generated `/home/mark/inflation_uk/build/public` directory (`public: "build/public"`). The repository root and `build/internal` are not Hosting inputs.
- `.firebaserc` maps the repository's default project to `deltagammavega-46877`.
- `/home/mark/inflation_uk/site/shared/CNAME` contains `www.deltagammavega.com`.
- Verified DNS: `www.deltagammavega.com` CNAMEs to `deltagammavega-46877.web.app`; authoritative nameservers are Gandi, not Cloudflare.
- The direct Firebase hostname is independently reachable. Treat it as an origin bypass when evaluating any front-door proxy or access gate.

## Public site structure

- `/` or `/index.html`: public landing page.
- `/cpurnsa.html`: US CPURNSA inflation curve; fetches public JSON under `/data/`.
- `/pca.html`: PCA diagnostics; fetches `/data/cpurnsa_pca_diagnostics.json`.
- `/chart.html`: additional static page.
- `/cpi_bayesian_update_example.html`: public Bayesian CPI update example.
- `/data/*`: published data files are directly public unless excluded from the generated public build. The public build allowlist contains only the approved JSON data files used by the public pages.
- The logs hub and its two subpages are internal-only: `/logs.html`, `/bars/logs.html`, and `/softs/logs.html`. Their backing payloads (`softs_diagnostics.json` and `bars_etf_logs.json`) are also copied only to `build/internal`.

There is no authentication, token gate, Cloudflare configuration, Firebase rewrite, redirect, or custom-header rule in the repository. Hiding a link does not protect a route.

## Two-build workflow

The repository keeps common page source in `/home/mark/inflation_uk/site/shared`. The source pages retain their deployed layout when assembled, so existing relative links and data URLs continue to work.

The logs hub and its two category pages are copied only into `build/internal`, together with their JSON payloads. The public build removes the landing-page logs anchor as well as those HTML and JSON resources, so Firebase Hosting does not expose this section.

### Routine build steps

Run these commands from `/home/mark/inflation_uk`:

1. Build both variants. This removes and recreates the generated directories, so stale files cannot remain in the public artifact:

   ```bash
   python3 tools/build_site.py --target all
   ```

2. Validate the generated public data:

   ```bash
   python3 tools/validate_uscpi_public_data.py \
     --snapshot-dir data/cpurnsa_snapshots \
     --pca-path data/cpurnsa_pca_diagnostics.json \
     --manifest-path data/uscpi_release_manifest.json \
     --curve-path data/cpurnsa_curve_history.json \
     --commentary-path data/cpurnsa_daily_commentary.json
   python3 tools/validate_softs_diagnostics.py data/softs_diagnostics.json
   ```

3. Validate the Firebase artifact and inspect its complete file list:

   ```bash
   python3 tools/validate_public_build.py build/public
   find build/public -type f -printf '%P\n' | sort
   ```

   The public build must contain only the allowlisted pages and five approved JSON files. It must not contain `site/internal`, `build/internal`, `tools`, `docs`, snapshots, credentials, secrets, or Python bytecode.

4. Browse the internal version locally when required:

   ```bash
   python3 -m http.server 8000 --directory build/internal
   ```

   Open `http://127.0.0.1:8000`. Stop the server with `Ctrl-C`. Put local-only resources in the ignored `site/internal/` directory before rebuilding; they will be copied only to `build/internal`.

5. Deploy only the validated public artifact. Firebase uses `build/public` because `/home/mark/inflation_uk/firebase.json` sets `hosting.public` to that directory:

   ```bash
   firebase deploy --only hosting
   ```

   Review `find build/public ...` before every deployment. Do not deploy the repository root or `build/internal`. After deployment, check both `https://www.deltagammavega.com` and `https://deltagammavega-46877.web.app`.

For a public-only rebuild, use `python3 tools/build_site.py --target public`. For an internal-only rebuild, use `python3 tools/build_site.py --target internal`.

### One-command internal launcher

A short Bash function named `dgv` is available in `/home/mark/.bashrc`. From any directory, run:

```bash
dgv
```

If this terminal was already open when the function was added, load it once with:

```bash
source ~/.bashrc
```

This rebuilds `build/internal` and starts the local server at:

```text
http://127.0.0.1:8000
```

Keep the terminal open while browsing the site. Press `Ctrl-C` to stop the server. The underlying launcher remains available from the repository with:

```bash
./run_internal_site.sh
```

The launcher is equivalent to:

```bash
python3 tools/build_site.py --target internal
python3 -m http.server 8000 --directory build/internal
```

### Validated production deployment

To deploy the public Firebase site from any directory, first load the Bash function in an existing terminal if necessary:

```bash
source ~/.bashrc
```

Then run:

```bash
dgv-deploy
```

This command:

1. Rebuilds `build/public` from the shared public source.
2. Validates the CPURNSA data and softs diagnostics.
3. Validates the public Firebase allowlist.
4. Prints every file that will be deployed.
5. Requires you to type `DEPLOY` explicitly.
6. Runs `firebase deploy --only hosting` only after confirmation.

Any response other than exactly `DEPLOY` cancels the deployment. The underlying script is `/home/mark/inflation_uk/deploy_public_site.sh`.


- The repository contains static HTML, JavaScript, and generated public data; no application package manifest is required for the site.
- GitHub workflows publish/validate selected public artifacts. Inspect `.github/workflows/` before changing publication behavior.
- Inspect `tools/` for data-generation and public-data validation scripts.
- Never publish credentials, service-account keys, private model state, raw trade observations, or secrets.
- Before changing public output, check both the generated files and the Firebase `public` boundary.

## Internal CPURNSA overlay

The public CPURNSA page remains at `site/shared/cpurnsa.html`. The internal build overlays `site/internal/cpurnsa.html`, which displays the shared page and adds an internal-only `Example of daily update →` link immediately beside the existing `View six-driver PCA diagnostics →` link. That link opens the existing shared `site/shared/cpi_bayesian_update_example.html` at the internal URL `cpurnsa_example.html`; the public Bayesian example URL is unchanged.

These files are copied only into `build/internal`; they are not included in `build/public` or Firebase Hosting. To test the split:

```bash
python3 tools/build_site.py --target all
python3 -m http.server 8000 --directory build/internal
```

The internal overlay can later be replaced with a richer private implementation without changing the public `site/shared/cpurnsa.html`.


These Bash functions are configured in `/home/mark/.bashrc`:

| Command | Purpose |
|---|---|
| `dgv` | Rebuilds the local internal site and starts the HTTP server on `http://127.0.0.1:8000`. If port 8000 is already occupied, it reports the existing server instead of raising a traceback. |
| `DGV_PORT=8001 dgv` | Rebuilds and serves the internal site on an alternate port when 8000 is unavailable. |
| `dgv-deploy` | Rebuilds and validates the public Firebase artifact, displays the 12 files to be deployed, and requires typing `DEPLOY` before production deployment. |

From an existing terminal, load the functions after any change to `.bashrc` with:

```bash
source ~/.bashrc
```

Use `Ctrl-C` to stop the local server started by `dgv`. The `dgv-deploy` command is deliberately separate so browsing the internal site cannot accidentally deploy to Firebase.

## Access-gate guidance

Cloudflare is not currently in the verified DNS/request path. A Cloudflare Access design would require DNS delegation to Cloudflare and proxied traffic, but protecting only the custom domain would not protect the directly reachable Firebase `web.app` hostname.

- Cloudflare One-Time PIN is email-based authentication, not a shared arbitrary password.
- Cloudflare Service Tokens are for controlled clients/services and must not be embedded in browser code.
- A shared browser token requires custom Worker/application logic, secure cookies, rotation, rate limiting, and explicit origin-bypass treatment.
- If only the landing page should be public, protect all secondary HTML and `/data/*` paths; do not rely on navigation links as access control.
- Treat Cloudflare Access as incomplete for sensitive content until the Firebase origin bypass is resolved or explicitly accepted.

## TODO — optional shared-password gate

Public access is acceptable for now; do not implement this as part of routine website changes. If access restriction becomes useful later, estimate **6–12 hours for a working MVP** and **1–2 days for a hardened version**.

Preferred approach without changing DNS delegation:

- Keep `/` and `/index.html` public.
- Use Firebase Hosting rewrites to route all secondary HTML pages and `/data/*` through an HTTPS Cloud Function or Cloud Run service.
- Add a shared password form and a signed, secure, HTTP-only session cookie.
- Store the password as a deployment secret, never in frontend code or Git.
- Move protected files outside the Firebase Hosting public directory, or otherwise verify that rewrites cannot fall through to public static files.
- Test both `www.deltagammavega.com` and `deltagammavega-46877.web.app` so the Firebase hostname cannot bypass the gate.
- Add rate limiting, password rotation, expiry/revocation handling, and deployment rollback checks before treating it as a meaningful security boundary.

The existing pages use relative data URLs, so preserving their current paths should minimise page-code changes. A frontend-only password prompt is not sufficient because users could still request the HTML and JSON files directly.

## Working rules

1. Read the relevant files in `/home/mark/inflation_uk` before proposing changes.
2. Verify live DNS and both the custom and `web.app` hostnames when investigating hosting or access behavior.
3. Distinguish verified facts from assumptions about Cloudflare account ownership, DNS control, and Firebase deployment state.
4. Keep private resources outside `build/public`; use the build and validation commands before deployment.
