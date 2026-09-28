"""Brigid's authenticated dashboard profile API.

Authentication is performed by Caddy's Pocket ID OIDC middleware. The service
only accepts identity headers injected by that reverse proxy and is never
published directly to the host.
"""

from __future__ import annotations

import base64
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT
DATA_DIR = Path(os.getenv("BRIGID_DATA_DIR", "/data"))
DATABASE_PATH = DATA_DIR / "brigid.sqlite3"
IDENTITY_HEADER = os.getenv("BRIGID_IDENTITY_HEADER", "X-Auth-Email").lower()
NAME_HEADER = os.getenv("BRIGID_NAME_HEADER", "X-Auth-Name").lower()
OIDC_ENABLED = os.getenv("BRIGID_OIDC_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}


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
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS profiles (
                identity TEXT PRIMARY KEY REFERENCES users(identity) ON DELETE CASCADE,
                encrypted_state BLOB NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )


@app.on_event("startup")
def startup() -> None:
    initialize_database()


def identity_from(request: Request) -> tuple[str | None, str]:
    if not OIDC_ENABLED:
        return None, "Default dashboard"
    identity = request.headers.get(IDENTITY_HEADER, "").strip().lower()
    if not identity:
        raise HTTPException(status_code=401, detail="Brigid requires Pocket ID authentication")
    display_name = request.headers.get(NAME_HEADER, "").strip() or identity
    return identity, display_name


def encode(payload: ProfilePayload) -> bytes:
    return FERNET.encrypt(payload.model_dump_json().encode("utf-8"))


def decode(value: bytes) -> ProfilePayload:
    try:
        return ProfilePayload.model_validate_json(FERNET.decrypt(value).decode("utf-8"))
    except (InvalidToken, UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(status_code=500, detail="Stored dashboard data cannot be decrypted") from exc


@app.get("/api/health")
def health() -> dict[str, bool | str]:
    return {"status": "ok", "oidcEnabled": OIDC_ENABLED}


@app.get("/api/config")
def configuration() -> dict[str, bool]:
    """Expose only non-sensitive feature flags required by the frontend."""
    return {"oidcEnabled": OIDC_ENABLED, "profilePersistenceEnabled": OIDC_ENABLED}


@app.get("/api/me")
def current_user(request: Request) -> dict[str, str]:
    identity, display_name = identity_from(request)
    if identity is None:
        return {"mode": "default", "displayName": display_name}
    return {"identity": identity, "displayName": display_name}


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
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
