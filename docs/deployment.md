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

## 2. Caddy and Pocket ID

Protect `brigid.dryad.nexus` with the same Pocket ID OIDC pattern already used
by your homelab, then inject the authenticated user's stable identity into the
header configured by `BRIGID_IDENTITY_HEADER` (default: `X-Auth-Email`).
Brigid must not be published with a host port; only Caddy should reach port 8000.

Conceptual reverse-proxy portion after your OIDC authentication directive:

```caddy
reverse_proxy brigid:8000 {
    header_up X-Auth-Email {http.auth.user.id}
    header_up X-Auth-Name {http.auth.user.name}
}
```

Header placeholder names vary by your OIDC module. Confirm them against the
module you use; the security requirement is that Caddy, not the browser,
injects the identity header after Pocket ID authentication.

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
