"""Brigid's native Pocket ID OIDC dashboard profile API.

Brigid is an OpenID Connect relying party. It owns authorization-code + PKCE
handling, opaque server-side sessions, encrypted profiles, and role checks;
Caddy only provides TLS and reverse-proxy routing.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import sqlite3
import time
from urllib.parse import urlencode
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
from authlib.jose import jwt
from cryptography.fernet import Fernet, InvalidToken
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT
DATA_DIR = Path(os.getenv("BRIGID_DATA_DIR", "/data"))
DATABASE_PATH = DATA_DIR / "brigid.sqlite3"
IDENTITY_HEADER = os.getenv("BRIGID_IDENTITY_HEADER", "X-Auth-Email").lower()
NAME_HEADER = os.getenv("BRIGID_NAME_HEADER", "X-Auth-Name").lower()
OIDC_ENABLED = os.getenv("BRIGID_OIDC_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}
OIDC_ISSUER = os.getenv("BRIGID_OIDC_ISSUER", "").strip().rstrip("/")
OIDC_CLIENT_ID = os.getenv("BRIGID_OIDC_CLIENT_ID", "").strip()
OIDC_CLIENT_SECRET = os.getenv("BRIGID_OIDC_CLIENT_SECRET", "").strip()
PUBLIC_URL = os.getenv("BRIGID_PUBLIC_URL", "").strip().rstrip("/")
ALLOWED_GROUPS = {group.strip() for group in os.getenv("BRIGID_ALLOWED_GROUPS", "").split(",") if group.strip()}
ADMIN_GROUPS = {group.strip() for group in os.getenv("BRIGID_ADMIN_GROUPS", "").split(",") if group.strip()}
SESSION_MAX_AGE = int(os.getenv("BRIGID_SESSION_MAX_AGE_HOURS", "168")) * 3600
COOKIE_SECURE = os.getenv("BRIGID_COOKIE_SECURE", "true").strip().lower() not in {"0", "false", "no", "off"}
KOMODO_URL = os.getenv("KOMODO_URL", "").strip().rstrip("/")
KOMODO_API_KEY = os.getenv("KOMODO_API_KEY", "").strip()
KOMODO_API_SECRET = os.getenv("KOMODO_API_SECRET", "").strip()
KOMODO_SERVER = os.getenv("KOMODO_SERVER", "local").strip()
DEFAULT_LAYOUT_FILE = Path(os.getenv("BRIGID_DEFAULT_LAYOUT_FILE", ROOT / "default-layout.json"))
GPU_METRICS_URL = os.getenv("GPU_METRICS_URL", "").strip()


class ProfilePayload(BaseModel):
    apps: list[dict[str, Any]] = Field(default_factory=list)
    settings: dict[str, Any] = Field(default_factory=dict)


def encryption_key() -> bytes:
    """Load the mandatory Fernet key without ever returning it to clients."""
    raw = os.getenv("BRIGID_ENCRYPTION_KEY", "").strip()
    if not raw:
        raise RuntimeError("BRIGID_ENCRYPTION_KEY must be configured")
    try:
        key = raw.encode("ascii")
        Fernet(key)
        return key
    except (UnicodeEncodeError, ValueError) as exc:
        raise RuntimeError("BRIGID_ENCRYPTION_KEY must be a Fernet key") from exc


FERNET = Fernet(encryption_key())
app = FastAPI(title="Brigid", docs_url=None, redoc_url=None)


@app.middleware("http")
async def browser_cache_policy(request: Request, call_next: Any) -> Response:
    """Always revalidate frontend code; dashboard API responses stay private."""
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    elif path == "/" or path.endswith((".html", ".js", ".css", ".webmanifest", ".png", ".svg")):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response


@app.middleware("http")
async def native_oidc_api_guard(request: Request, call_next: Any) -> Response:
    if OIDC_ENABLED and request.url.path.startswith("/api/") and request.url.path != "/api/health":
        if session_user_from(request) is None:
            return JSONResponse(status_code=401, content={"detail": "Brigid requires Pocket ID authentication"})
    return await call_next(request)


@contextmanager
def database():
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def initialize_database() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with database() as connection:
        connection.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS users (
                identity TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                email TEXT,
                groups_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS profiles (
                identity TEXT PRIMARY KEY REFERENCES users(identity) ON DELETE CASCADE,
                encrypted_state BLOB NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                identity TEXT NOT NULL REFERENCES users(identity) ON DELETE CASCADE,
                expires_at INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS oidc_transactions (
                state TEXT PRIMARY KEY,
                nonce TEXT NOT NULL,
                code_verifier TEXT NOT NULL,
                expires_at INTEGER NOT NULL
            );
            """
        )
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(users)")}
        if "email" not in columns:
            connection.execute("ALTER TABLE users ADD COLUMN email TEXT")
        if "groups_json" not in columns:
            connection.execute("ALTER TABLE users ADD COLUMN groups_json TEXT NOT NULL DEFAULT '[]'")


@app.on_event("startup")
def startup() -> None:
    initialize_database()


def oidc_configured() -> bool:
    return bool(OIDC_ISSUER and OIDC_CLIENT_ID and OIDC_CLIENT_SECRET and PUBLIC_URL)


def oidc_redirect_uri() -> str:
    return f"{PUBLIC_URL}/auth/callback"


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def oidc_discovery() -> dict[str, Any]:
    if not oidc_configured():
        raise HTTPException(status_code=503, detail="Native OIDC is not configured")
    async with httpx.AsyncClient(timeout=httpx.Timeout(8.0), follow_redirects=False) as client:
        response = await client.get(f"{OIDC_ISSUER}/.well-known/openid-configuration")
        response.raise_for_status()
    metadata = response.json()
    if metadata.get("issuer") != OIDC_ISSUER:
        raise HTTPException(status_code=502, detail="OIDC discovery issuer mismatch")
    return metadata


def session_user_from(request: Request) -> dict[str, Any] | None:
    token = request.cookies.get("brigid_session")
    if not token:
        return None
    with database() as connection:
        connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (int(time.time()),))
        row = connection.execute(
            """SELECT users.identity, users.display_name, users.email, users.groups_json FROM sessions
               JOIN users ON users.identity = sessions.identity
               WHERE sessions.token_hash = ? AND sessions.expires_at > ?""",
            (token_hash(token), int(time.time())),
        ).fetchone()
    if not row:
        return None
    groups = json.loads(row["groups_json"] or "[]")
    return {
        "identity": row["identity"],
        "displayName": row["display_name"],
        "email": row["email"],
        "groups": groups if isinstance(groups, list) else [],
        "isAdmin": bool(ADMIN_GROUPS.intersection(groups)),
    }


def identity_from(request: Request) -> tuple[str | None, str]:
    if not OIDC_ENABLED:
        return None, "Default dashboard"
    session = session_user_from(request)
    if not session:
        raise HTTPException(status_code=401, detail="Brigid requires Pocket ID authentication")
    return session["identity"], session["displayName"]


def require_admin(request: Request) -> None:
    if not OIDC_ENABLED:
        return
    session = session_user_from(request)
    if not session or not session["isAdmin"]:
        raise HTTPException(status_code=403, detail="Brigid administrator permission required")


def encode(payload: ProfilePayload) -> bytes:
    return FERNET.encrypt(payload.model_dump_json().encode("utf-8"))


def decode(value: bytes) -> ProfilePayload:
    try:
        return ProfilePayload.model_validate_json(FERNET.decrypt(value).decode("utf-8"))
    except (InvalidToken, UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(status_code=500, detail="Stored dashboard data cannot be decrypted") from exc


def default_layout() -> ProfilePayload:
    """Load the safe, version-controlled household starter dashboard."""
    try:
        return ProfilePayload.model_validate_json(DEFAULT_LAYOUT_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail="Default dashboard layout is unavailable") from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=500, detail="Default dashboard layout is invalid") from exc


def komodo_configured() -> bool:
    return bool(KOMODO_URL and KOMODO_API_KEY and KOMODO_API_SECRET and KOMODO_SERVER)


def gpu_metrics_configured() -> bool:
    return bool(GPU_METRICS_URL)


PROMETHEUS_SAMPLE = re.compile(
    r'^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{([^}]*)\})?\s+([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)'
)
PROMETHEUS_LABEL = re.compile(r'(\w+)="((?:\\.|[^"\\])*)"')


def parse_gpu_metrics(metrics: str) -> dict[str, Any]:
    """Reduce NVIDIA DCGM's Prometheus payload to a dashboard-safe summary."""
    fields = {
        "DCGM_FI_DEV_GPU_UTIL": "utilization",
        "DCGM_FI_DEV_FB_USED": "vramUsedMiB",
        "DCGM_FI_DEV_FB_FREE": "vramFreeMiB",
        "DCGM_FI_DEV_FB_TOTAL": "vramTotalMiB",
        "DCGM_FI_DEV_GPU_TEMP": "temperatureC",
        "DCGM_FI_DEV_POWER_USAGE": "powerW",
    }
    gpus: dict[str, dict[str, Any]] = {}
    for line in metrics.splitlines():
        match = PROMETHEUS_SAMPLE.match(line)
        if not match or match.group(1) not in fields:
            continue
        labels = {key: value.replace('\\"', '"') for key, value in PROMETHEUS_LABEL.findall(match.group(2) or "")}
        gpu_id = labels.get("UUID") or labels.get("gpu")
        if gpu_id is None:
            continue
        gpu = gpus.setdefault(gpu_id, {"id": gpu_id, "name": labels.get("modelName") or labels.get("model") or f"GPU {labels.get('gpu', '?')}"})
        gpu[fields[match.group(1)]] = float(match.group(3))

    result = list(gpus.values())
    if not result:
        raise HTTPException(status_code=502, detail="GPU exporter returned no NVIDIA GPU metrics")
    for gpu in result:
        if "vramTotalMiB" not in gpu and "vramUsedMiB" in gpu and "vramFreeMiB" in gpu:
            gpu["vramTotalMiB"] = gpu["vramUsedMiB"] + gpu["vramFreeMiB"]

    values = lambda key: [float(gpu[key]) for gpu in result if key in gpu]
    used, total = values("vramUsedMiB"), values("vramTotalMiB")
    return {
        "gpus": result,
        "summary": {
            "utilization": sum(values("utilization")) / len(values("utilization")) if values("utilization") else None,
            "vramUsedMiB": sum(used) if used else None,
            "vramTotalMiB": sum(total) if total else None,
            "temperatureC": max(values("temperatureC"), default=None),
            "powerW": sum(values("powerW")) if values("powerW") else None,
        },
    }


async def read_gpu_stats() -> dict[str, Any]:
    if not gpu_metrics_configured():
        raise HTTPException(status_code=503, detail="GPU statistics are not configured")
    async with httpx.AsyncClient(timeout=httpx.Timeout(5.0), follow_redirects=False) as client:
        response = await client.get(GPU_METRICS_URL)
        response.raise_for_status()
    return parse_gpu_metrics(response.text)


async def read_komodo_system_stats() -> dict[str, Any]:
    """Call Komodo's read-only system-stat endpoint without exposing its key."""
    if not komodo_configured():
        raise HTTPException(status_code=503, detail="Komodo statistics are not configured")

    headers = {
        "X-Api-Key": KOMODO_API_KEY,
        "X-Api-Secret": KOMODO_API_SECRET,
        "Content-Type": "application/json",
    }
    attempts = (
        (f"{KOMODO_URL}/read", {"type": "GetSystemStats", "params": {"server": KOMODO_SERVER}}),
        (f"{KOMODO_URL}/read/GetSystemStats", {"server": KOMODO_SERVER}),
    )
    async with httpx.AsyncClient(timeout=httpx.Timeout(8.0), follow_redirects=False) as client:
        last_response: httpx.Response | None = None
        for url, payload in attempts:
            response = await client.post(url, headers=headers, json=payload)
            if response.status_code != 404:
                response.raise_for_status()
                result = response.json()
                return result if isinstance(result, dict) else {"data": result}
            last_response = response
    status = last_response.status_code if last_response is not None else 502
    raise HTTPException(status_code=502, detail=f"Komodo endpoint unavailable ({status})")


@app.get("/api/health")
def health() -> dict[str, bool | str]:
    return {"status": "ok", "oidcEnabled": OIDC_ENABLED}


@app.get("/auth/login")
async def login() -> RedirectResponse:
    if not OIDC_ENABLED:
        return RedirectResponse("/")
    metadata = await oidc_discovery()
    state, nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    with database() as connection:
        connection.execute("DELETE FROM oidc_transactions WHERE expires_at <= ?", (int(time.time()),))
        connection.execute(
            "INSERT INTO oidc_transactions(state, nonce, code_verifier, expires_at) VALUES (?, ?, ?, ?)",
            (state, nonce, verifier, int(time.time()) + 600),
        )
    query = urlencode({
        "response_type": "code", "client_id": OIDC_CLIENT_ID, "redirect_uri": oidc_redirect_uri(),
        "scope": "openid profile email groups", "state": state, "nonce": nonce,
        "code_challenge": challenge, "code_challenge_method": "S256",
    })
    return RedirectResponse(f"{metadata['authorization_endpoint']}?{query}", status_code=302)


@app.get("/auth/callback")
async def callback(request: Request) -> RedirectResponse:
    if request.query_params.get("error"):
        raise HTTPException(status_code=401, detail="Pocket ID sign-in was denied")
    state, code = request.query_params.get("state", ""), request.query_params.get("code", "")
    with database() as connection:
        transaction = connection.execute("SELECT * FROM oidc_transactions WHERE state = ?", (state,)).fetchone()
        connection.execute("DELETE FROM oidc_transactions WHERE state = ?", (state,))
    if not transaction or not code or transaction["expires_at"] <= int(time.time()):
        raise HTTPException(status_code=400, detail="OIDC login transaction expired or invalid")
    metadata = await oidc_discovery()
    async with httpx.AsyncClient(timeout=httpx.Timeout(8.0), follow_redirects=False) as client:
        token_response = await client.post(metadata["token_endpoint"], data={
            "grant_type": "authorization_code", "code": code, "redirect_uri": oidc_redirect_uri(),
            "client_id": OIDC_CLIENT_ID, "client_secret": OIDC_CLIENT_SECRET,
            "code_verifier": transaction["code_verifier"],
        })
        token_response.raise_for_status()
        token = token_response.json()
        jwks_response = await client.get(metadata["jwks_uri"])
        jwks_response.raise_for_status()
    claims = jwt.decode(token["id_token"], jwks_response.json())
    claims.validate()
    audience = claims.get("aud", [])
    audience = [audience] if isinstance(audience, str) else audience
    if claims.get("iss") != OIDC_ISSUER or OIDC_CLIENT_ID not in audience or claims.get("nonce") != transaction["nonce"]:
        raise HTTPException(status_code=401, detail="Pocket ID token validation failed")
    groups = claims.get("groups", [])
    groups = [groups] if isinstance(groups, str) else groups
    groups = [str(group) for group in groups]
    if ALLOWED_GROUPS and not ALLOWED_GROUPS.intersection(groups):
        raise HTTPException(status_code=403, detail="Your Pocket ID group cannot access Brigid")
    identity, display_name = str(claims["sub"]), str(claims.get("name") or claims.get("preferred_username") or claims["sub"])
    email = str(claims.get("email") or "")
    bearer = secrets.token_urlsafe(32)
    with database() as connection:
        connection.execute(
            """INSERT INTO users(identity, display_name, email, groups_json) VALUES (?, ?, ?, ?)
               ON CONFLICT(identity) DO UPDATE SET display_name=excluded.display_name, email=excluded.email,
               groups_json=excluded.groups_json, updated_at=CURRENT_TIMESTAMP""",
            (identity, display_name, email, json.dumps(groups)),
        )
        connection.execute("INSERT INTO sessions(token_hash, identity, expires_at) VALUES (?, ?, ?)",
                           (token_hash(bearer), identity, int(time.time()) + SESSION_MAX_AGE))
    response = RedirectResponse("/", status_code=302)
    response.set_cookie("brigid_session", bearer, max_age=SESSION_MAX_AGE, httponly=True,
                        secure=COOKIE_SECURE, samesite="lax", path="/")
    return response


@app.get("/auth/logout")
@app.post("/auth/logout")
def logout(request: Request) -> RedirectResponse:
    token = request.cookies.get("brigid_session")
    if token:
        with database() as connection:
            connection.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash(token),))
    response = RedirectResponse("/", status_code=302)
    response.delete_cookie("brigid_session", path="/")
    return response


@app.get("/api/config")
def configuration() -> dict[str, bool]:
    """Expose only non-sensitive feature flags required by the frontend."""
    return {
        "oidcEnabled": OIDC_ENABLED,
        "profilePersistenceEnabled": OIDC_ENABLED,
        "komodoConfigured": komodo_configured(),
        "gpuMetricsConfigured": gpu_metrics_configured(),
        "defaultLayoutConfigured": DEFAULT_LAYOUT_FILE.is_file(),
    }


@app.get("/api/me")
def current_user(request: Request) -> dict[str, Any]:
    identity, display_name = identity_from(request)
    if identity is None:
        return {"mode": "default", "displayName": display_name, "isAdmin": True}
    user = session_user_from(request)
    return {"identity": identity, "displayName": display_name, "isAdmin": bool(user and user["isAdmin"])}


@app.get("/api/komodo/stats")
async def komodo_stats(request: Request) -> dict[str, Any]:
    require_admin(request)
    return await read_komodo_system_stats()


@app.get("/api/gpu/stats")
async def gpu_stats(request: Request) -> dict[str, Any]:
    require_admin(request)
    return await read_gpu_stats()


@app.get("/api/default-layout")
def household_default_layout() -> dict[str, Any]:
    return default_layout().model_dump()


@app.get("/api/profile")
def get_profile(request: Request, response: Response) -> dict[str, Any]:
    identity, display_name = identity_from(request)
    if identity is None:
        response.status_code = 204
        return {}
    with database() as connection:
        connection.execute(
            """
            INSERT INTO users(identity, display_name) VALUES (?, ?)
            ON CONFLICT(identity) DO UPDATE SET display_name=excluded.display_name,
                updated_at=CURRENT_TIMESTAMP
            """,
            (identity, display_name),
        )
        row = connection.execute(
            "SELECT encrypted_state FROM profiles WHERE identity = ?", (identity,)
        ).fetchone()
    if row is None:
        response.status_code = 204
        return {}
    return decode(row["encrypted_state"]).model_dump()


@app.put("/api/profile")
def put_profile(request: Request, payload: ProfilePayload) -> dict[str, str]:
    identity, display_name = identity_from(request)
    if identity is None:
        raise HTTPException(status_code=403, detail="Profile persistence requires OIDC")
    encrypted_state = encode(payload)
    with database() as connection:
        connection.execute(
            """
            INSERT INTO users(identity, display_name) VALUES (?, ?)
            ON CONFLICT(identity) DO UPDATE SET display_name=excluded.display_name,
                updated_at=CURRENT_TIMESTAMP
            """,
            (identity, display_name),
        )
        connection.execute(
            """
            INSERT INTO profiles(identity, encrypted_state) VALUES (?, ?)
            ON CONFLICT(identity) DO UPDATE SET encrypted_state=excluded.encrypted_state,
                updated_at=CURRENT_TIMESTAMP
            """,
            (identity, encrypted_state),
        )
    return {"status": "saved"}


@app.delete("/api/profile", status_code=204)
def delete_profile(request: Request) -> Response:
    identity, _ = identity_from(request)
    if identity is None:
        return Response(status_code=204)
    with database() as connection:
        connection.execute("DELETE FROM profiles WHERE identity = ?", (identity,))
    return Response(status_code=204)


@app.get("/")
def index(request: Request) -> Response:
    if OIDC_ENABLED and session_user_from(request) is None:
        return RedirectResponse("/auth/login", status_code=302)
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
