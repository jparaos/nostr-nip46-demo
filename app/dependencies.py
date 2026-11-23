"""Dependencias de FastAPI: acceso al estado compartido y controles de acceso."""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request

from signer.approvals import ApprovalManager

from .security import CSRF_COOKIE, CSRF_HEADER, SESSION_COOKIE, SessionStore
from .services.signer_service import SignerRuntime
from .services.vault_service import VaultService


@dataclass
class AppState:
    vault: VaultService
    approvals: ApprovalManager  # la ÚNICA cola: la usan el firmante y la API
    runtime: SignerRuntime
    sessions: SessionStore
    port: int


def get_state(request: Request) -> AppState:
    return request.app.state.signer


def has_session(request: Request, state: AppState) -> bool:
    return state.vault.is_unlocked and state.sessions.valid(request.cookies.get(SESSION_COOKIE))


def require_csrf(request: Request) -> None:
    cookie = request.cookies.get(CSRF_COOKIE)
    header = request.headers.get(CSRF_HEADER)
    if not cookie or not header or not secrets.compare_digest(cookie, header):
        raise HTTPException(status_code=403, detail="token CSRF inválido; recarga la página")


def require_session(request: Request, state: AppState = Depends(get_state)) -> AppState:
    if not has_session(request, state):
        raise HTTPException(status_code=401, detail="sesión no válida o bóveda bloqueada")
    return state
