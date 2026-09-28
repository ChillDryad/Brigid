# Brigid deployment

Brigid delegates sign-in to Pocket ID through Caddy. It creates one encrypted
profile per authenticated identity on first visit. It is deliberately not a
public registration service.

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
- `/api/*` rejects requests without the configured identity header.
- Browser localStorage is only an offline cache and migration source. The API
  becomes the canonical profile after the first authenticated load.
- Do not expose Brigid's container port or allow other untrusted containers to
  reach it, because identity headers are a trusted reverse-proxy boundary.
