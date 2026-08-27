# Delta Gamma Vega Website — Setup

This is the ordinary project documentation for `www.deltagammavega.com`. The website source of truth is `/home/mark/inflation_uk`; this note is not Kiro steering configuration.

## Hosting and DNS

- Firebase project: `deltagammavega-46877`.
- Firebase Hosting config: `/home/mark/inflation_uk/firebase.json`.
- `.firebaserc` maps the repository's default project to `deltagammavega-46877`.
- `firebase.json` serves the entire repository root (`public: "."`), excluding Firebase config, dotfiles, and `node_modules`.
- `/home/mark/inflation_uk/CNAME` contains `www.deltagammavega.com`.
- Verified DNS: `www.deltagammavega.com` CNAMEs to `deltagammavega-46877.web.app`; authoritative nameservers are Gandi, not Cloudflare.
- The direct Firebase hostname is independently reachable. Treat it as an origin bypass when evaluating any front-door proxy or access gate.

## Public site structure

- `/` or `/index.html`: public landing page.
- `/cpurnsa.html`: US CPURNSA inflation curve; fetches public JSON under `/data/`.
- `/pca.html`: PCA diagnostics; fetches `/data/cpurnsa_pca_diagnostics.json`.
- `/softs/logs.html`: softs diagnostics; fetches `/data/softs_diagnostics.json`.
- `/chart.html`: additional static page.
- `/data/*`: published data files are directly public unless excluded from Firebase Hosting.

There is no authentication, token gate, Cloudflare configuration, Firebase rewrite, redirect, or custom-header rule in the repository. Hiding a link does not protect a route.

## Publication and deployment

- The repository contains static HTML, JavaScript, and generated public data; no application package manifest is required for the site.
- GitHub workflows publish/validate selected public artifacts. Inspect `.github/workflows/` before changing publication behavior.
- Inspect `tools/` for data-generation and public-data validation scripts.
- Never publish credentials, service-account keys, private model state, raw trade observations, or secrets.
- Before changing public output, check both the generated files and the Firebase `public` boundary.

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
4. Do not modify website code or deployment configuration merely to answer an architecture question.
