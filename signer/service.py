"""Servicio del firmante: ciclo de vida de relays y procesamiento de eventos.

No depende de ninguna interfaz. La consola (main.py) y, más adelante, la API
web solo crean el servicio, lo inician/detienen y resuelven aprobaciones a
través del ApprovalHandler que reciben.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Protocol

import nostr_sdk as sdk

from . import crypto
from .approvals import ApprovalHandler
from .audit import AuditLog
from .nip46 import Nip46Signer, bunker_url, new_secret
from .relay import RelayPool

NIP46_KIND = 24133
DEFAULT_RELAYS = ["wss://relay.nsec.app", "wss://relay.damus.io", "wss://nos.lol"]

log = logging.getLogger("service")


class EventSource(Protocol):
    """Lo que el servicio necesita de un pool de relays (permite pruebas sin red)."""

    events: asyncio.Queue

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def publish(self, event: dict) -> int: ...


class SignerService:
    def __init__(
        self,
        keys: sdk.Keys,
        approvals: ApprovalHandler,
        relays: list[str] | None = None,
        audit: AuditLog | None = None,
        pool: EventSource | None = None,
        secret: str | None = None,
    ):
        self.relays = relays or DEFAULT_RELAYS
        self.approvals = approvals
        self.audit = audit or AuditLog(None)
        self.secret = secret or new_secret()
        self.signer = Nip46Signer(keys, self.secret, approvals)
        self.pubkey = self.signer.pubkey
        self.pool: EventSource = pool or RelayPool(
            self.relays, {"kinds": [NIP46_KIND], "#p": [self.pubkey], "since": int(time.time()) - 10}
        )
        self._consumer: asyncio.Task | None = None
        self._inflight: set[asyncio.Task] = set()

    @property
    def bunker_url(self) -> str:
        return bunker_url(self.pubkey, self.relays, self.secret)

    @property
    def running(self) -> bool:
        return self._consumer is not None and not self._consumer.done()

    async def start(self) -> None:
        if self.running:
            return
        await self.pool.start()
        self._consumer = asyncio.create_task(self._consume(), name="signer-consumer")

    async def stop(self) -> None:
        """Detiene el consumo, rechaza lo pendiente y cierra los relays."""
        if self._consumer:
            self._consumer.cancel()
        cancel_all = getattr(self.approvals, "cancel_all", None)
        if cancel_all:
            cancel_all()  # las solicitudes pendientes reciben un rechazo, nunca una firma
        for task in list(self._inflight):
            task.cancel()
        await asyncio.gather(*(t for t in [self._consumer, *self._inflight] if t), return_exceptions=True)
        self._inflight.clear()
        self._consumer = None
        await self.pool.stop()

    async def _consume(self) -> None:
        # Una tarea por evento: una firma esperando aprobación no bloquea ping,
        # get_public_key ni solicitudes de otros clientes.
        while True:
            event = await self.pool.events.get()
            task = asyncio.create_task(self._safe_process(event))
            self._inflight.add(task)
            task.add_done_callback(self._inflight.discard)

    async def _safe_process(self, event: dict) -> None:
        try:
            await self.process(event)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("error procesando evento %s", str(event.get("id", ""))[:8])

    async def process(self, event: dict) -> dict | None:
        """Valida, descifra, despacha y publica la respuesta. Retorna la respuesta NIP-46."""
        client = event.get("pubkey", "")
        if event.get("kind") != NIP46_KIND or not crypto.verify_event(event):
            log.warning("evento descartado (kind o firma inválida): %s", str(event.get("id", ""))[:8])
            return None
        try:
            plaintext, scheme = crypto.decrypt(self.signer.keys, client, event["content"])
            request = json.loads(plaintext)
        except Exception as exc:
            log.warning("no se pudo descifrar/parsear solicitud de %s: %s", client[:8], exc)
            self.audit.write({"dir": "in", "client": client, "error": "decrypt_failed"})
            return None

        method = request.get("method", "")
        log.info("req de=%s… cifrado=%s method=%s", client[:12], scheme, method)
        self.audit.request(client, scheme, request)

        response = await self.signer.handle(client, request)
        self.audit.response(client, scheme, method, response)
        log.info("resp method=%s %s", method, "ERROR " + response["error"] if "error" in response else "ok")

        reply = crypto.build_event(
            self.signer.keys, NIP46_KIND,
            crypto.encrypt(self.signer.keys, client, json.dumps(response), scheme), [["p", client]],
        )
        await self.pool.publish(reply)
        return response
