"""Ciclo de vida del firmante dentro de la aplicación web.

Reutiliza signer.service.SignerService (que a su vez usa el Nip46Signer existente)
y comparte UNA instancia de ApprovalManager con los endpoints de aprobación.
"""

from __future__ import annotations

import time
from typing import Callable

import nostr_sdk as sdk

from signer.approvals import ApprovalManager
from signer.audit import AuditLog
from signer.service import DEFAULT_RELAYS, EventSource, SignerService

PoolFactory = Callable[[list[str], dict], EventSource]


def short(pubkey: str) -> str:
    return f"{pubkey[:8]}…{pubkey[-4:]}" if len(pubkey) > 12 else pubkey


class SignerRuntime:
    def __init__(self, approvals: ApprovalManager, relays: list[str] | None = None,
                 audit: AuditLog | None = None, pool_factory: PoolFactory | None = None):
        self.approvals = approvals
        self.relays = relays or DEFAULT_RELAYS
        self.audit = audit or AuditLog()
        self._pool_factory = pool_factory
        self.service: SignerService | None = None
        self.started_at: float | None = None

    @property
    def running(self) -> bool:
        return self.service is not None and self.service.running

    async def start(self, keys: sdk.Keys) -> None:
        if self.running:
            return
        pool = None
        if self._pool_factory:
            pubkey = keys.public_key().to_hex()
            pool = self._pool_factory(self.relays, {"kinds": [24133], "#p": [pubkey], "since": int(time.time()) - 10})
        self.service = SignerService(keys, self.approvals, relays=self.relays, audit=self.audit, pool=pool)
        await self.service.start()
        self.started_at = time.time()

    async def stop(self) -> None:
        """Detiene relays y tareas; lo pendiente se rechaza (nunca se firma)."""
        if self.service:
            await self.service.stop()
        self.service = None
        self.started_at = None

    async def restart(self, keys: sdk.Keys) -> None:
        """Genera una URL bunker:// nueva (nuevo secret) y olvida los clientes autorizados."""
        await self.stop()
        await self.start(keys)

    def status(self) -> dict:
        if not self.service:
            return {"running": False, "bunker_url": None, "secret_used": False, "relays": [], "clients": [],
                    "activity": None}
        connected = set(getattr(self.service.pool, "connected", []))
        received = getattr(self.service.pool, "received", {})
        authorized = sorted(self.service.signer.authorized)
        return {
            "running": self.running,
            "started_at": self.started_at,
            "bunker_url": self.service.bunker_url,
            # el único camino para autorizar un cliente es un connect con el secret (de un solo uso)
            "secret_used": bool(authorized),
            "relays": [{"url": url, "connected": url in connected, "events": received.get(url, 0)}
                       for url in self.relays],
            "clients": [short(c) for c in authorized],  # solo identificadores públicos abreviados
            "activity": dict(self.service.activity),  # contadores y motivos de descarte, sin contenido
        }
