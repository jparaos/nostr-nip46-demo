"""Aplicación FastAPI: composición, lifespan y middleware.

    python -m app            # http://127.0.0.1:8046
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from signer.approvals import ApprovalManager
from signer.audit import AuditLog
from signer.vault import DEFAULT_PATH

from .dependencies import AppState
from .routes import approvals, pages, signer, wallet
from .security import LocalOnlyMiddleware, SessionStore
from .services.signer_service import PoolFactory, SignerRuntime
from .services.vault_service import VaultService

DEFAULT_PORT = 8046
STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app(
    vault_path: Path | None = None,
    port: int = DEFAULT_PORT,
    relays: list[str] | None = None,
    approval_timeout: float = 120.0,
    scrypt_n: int | None = None,
    pool_factory: PoolFactory | None = None,
    audit: AuditLog | None = None,
) -> FastAPI:
    vault_path = vault_path or Path(os.environ.get("SIGNER_VAULT_PATH", DEFAULT_PATH))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        approvals_manager = ApprovalManager(timeout=approval_timeout)  # única cola compartida
        app.state.signer = AppState(
            vault=VaultService(vault_path, scrypt_n),
            approvals=approvals_manager,
            runtime=SignerRuntime(approvals_manager, relays=relays, audit=audit, pool_factory=pool_factory),
            sessions=SessionStore(),
            port=port,
        )
        # El firmante NO arranca aquí: necesita la bóveda desbloqueada (lo inicia /api/vault/unlock).
        try:
            yield
        finally:
            state: AppState = app.state.signer
            await state.runtime.stop()  # cancela tareas, rechaza lo pendiente, cierra relays
            state.vault.lock()
            state.sessions.revoke_all()

    app = FastAPI(title="Firmante NIP-46 local", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(LocalOnlyMiddleware, port=port)  # sin CORSMiddleware: ningún origen ajeno
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    for router in (pages.router, wallet.router, signer.router, approvals.router):
        app.include_router(router)
    return app
