# Brigid deployment

Brigid delegates sign-in to Pocket ID through Caddy. It creates one encrypted
profile per authenticated identity on first visit. It is deliberately not a
public registration service.

## Authentication modes

`BRIGID_OIDC_ENABLED=true` is the secure default. Caddy/Pocket ID protects the
site and Brigid stores encrypted, per-user server profiles.

Set `BRIGID_OIDC_ENABLED=false` to run a shared default dashboard before you
have user accounts. In this mode Brigid does not require identity headers, does
not create user records, and does not persist server-side profiles. Everyone is
served the built-in dashboard; browser localStorage remains an optional local
cache. Do not expose this mode publicly if users can add sensitive widget data.

## 1. Create persistent application data

Create `/portainer/Files/AppData/Config/brigid/data` and copy
`brigid.env.example` to `/portainer/Files/AppData/Config/brigid/brigid.env`.
Generate the required Fernet key with:

```sh
python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

Set the output as `BRIGID_ENCRYPTION_KEY`. Do not commit this file or paste the
key into the dashboard. Losing it prevents decryption of existing profiles.

## Household starter layout

`default-layout.json` is the version-controlled Dryad household template. It
contains the safe, branded starting cards for Lesflix, Lesseer, BookLore,
Shelfmark, SuggestArr, Pocket ID, Homepage, and Komodo—never API keys or other
credentials.

The compose file mounts it read-only at `/config/default-layout.json`. Brigid
uses it only when a browser has no saved dashboard and then seeds an
authenticated user's first encrypted profile from it. Changing the file affects
new or reset dashboards only; it never overwrites an existing user's layout.

To customize the deployed household baseline, copy the repository's
`default-layout.json` to:

```text
/portainer/Files/AppData/Config/brigid/default-layout.json
```

After editing it, recreate Brigid so the file mount/config is refreshed. A
dashboard reset is the explicit way for an existing user to adopt the new
template.

## 2. Native Pocket ID OIDC

Brigid is an OIDC relying party itself. Caddy only reverse-proxies to port
8000—do not add Caddy forward-auth or identity headers to this route.

Create a confidential Pocket ID client named **Brigid** with PKCE enabled:

```text
Callback URL: https://brigid.dryad.nexus/auth/callback
Scopes: openid profile email groups
```

Copy Pocket ID's client UUID and secret into `brigid.env` along with
`BRIGID_OIDC_ISSUER=https://auth.dryad.nexus` and
`BRIGID_PUBLIC_URL=https://brigid.dryad.nexus`. Brigid completes the
authorization-code exchange server-side, verifies the ID token against Pocket
ID's discovered JWKS, and stores only an opaque HttpOnly session cookie in the
browser.

Set the allowed client groups in Pocket ID and repeat the intended group names
in `BRIGID_ALLOWED_GROUPS`. `BRIGID_ADMIN_GROUPS` controls server-enforced
administrator widgets and API routes such as Komodo and GPU telemetry.

To restrict an individual layout card, set `"adminOnly": true` in that card's
`data` object. Brigid hides those cards for non-admin users and independently
returns `403` from protected server-side telemetry endpoints. The starter
layout marks Komodo, GPU telemetry, and Homepage as administrator-only.

## TTC Commute Sentinel

The optional TTC widget reads the official TTC GTFS-Realtime alert feed on the
server and shows only alerts matching configured route and/or stop IDs. It does
not need a home or work address. Set `TTC_COMMUTE_ROUTES` (for example `1,2`)
and optionally `TTC_COMMUTE_STOPS`; leave both blank until configured, which
makes the card show a safe setup state. The first release is an alert watcher,
not a travel-time prediction engine: it reports route impact and feed outages
without inventing a departure-time estimate.

## Home-screen install

Brigid ships as an installable Progressive Web App. Serve it through the normal
HTTPS Caddy route, open it once online, then use the header phone icon to
install it in Chromium-based browsers. On iPhone/iPad Safari, tap the icon for
the exact **Share → Add to Home Screen** instruction. The service worker never
caches API or authentication routes; it keeps a network-first offline copy of
the dashboard shell only.

## Security model

- Pocket ID owns user creation, sign-in, and MFA/passkeys.
- Brigid stores encrypted per-user dashboards in SQLite at `/data/brigid.db`.
- With OIDC enabled, `/api/*` profile routes reject requests without the
  configured identity header. With OIDC disabled, the API serves the default
  dashboard mode and refuses server-side profile writes.
- Browser localStorage is only an offline cache and migration source. The API
  becomes the canonical profile after the first authenticated load.
- Do not expose Brigid's container port or allow other untrusted containers to
  reach it, because identity headers are a trusted reverse-proxy boundary.

## Komodo server metrics

Brigid includes a **Komodo Server Stats** widget. Add it from the dashboard's
Add App dialog, then configure the backend (never the browser) with
`KOMODO_URL`, `KOMODO_SERVER`, `KOMODO_API_KEY`, and `KOMODO_API_SECRET`.

Create a dedicated Komodo service user/API key with **Read** permission only,
scoped to the intended server. Do not use an administrator key. Brigid calls
Komodo's `GetSystemStats` read operation server-side and tries the current and
legacy read-route formats for compatibility across Komodo releases.

## Homelab stats via Glances

The **Homelab Stats** card is a compact, administrator-only view of CPU, memory,
root-disk usage, load, and uptime from Glances. Brigid requests Glances
server-side; the browser never receives the Glances URL or credentials.

Put Brigid and Glances on the same private Docker network and configure the
complete Glances API root in `brigid.env`:

```env
GLANCES_API_URL=http://glances:61208/api/4
# Only if Glances authentication is enabled:
GLANCES_USERNAME=
GLANCES_PASSWORD=
# Or use the bearer-token mechanism instead:
GLANCES_TOKEN=
```

Do not route Glances through Caddy or expose its API through the public
Cloudflare tunnel. The default layout pairs this Homelab card with the separate
**Cloud Lab** Komodo card; set `KOMODO_SERVER` to the Komodo server name for
your cloud host.

## NVIDIA GPU metrics

The **NVIDIA GPU Stats** card reads an NVIDIA DCGM Exporter Prometheus endpoint
through Brigid's backend. It displays GPU utilization, aggregate VRAM usage,
temperature, and GPU name/count without exposing the exporter to the browser.

Deploy `compose.gpu-exporter.yaml` on the NVIDIA GPU host after installing the
NVIDIA Container Toolkit. Keep port 9400 internal: when the exporter shares
`caddy-homelab` with Brigid, set:

```env
GPU_METRICS_URL=http://dcgm-exporter:9400/metrics
```

For a GPU host on another network, point `GPU_METRICS_URL` at a private
Tailnet-only exporter URL. Do not publish DCGM metrics through the public
Cloudflare tunnel.
