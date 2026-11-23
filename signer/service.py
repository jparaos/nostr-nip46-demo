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
MAX_EVENT_AGE = 600  # segundos de tolerancia para created_at de solicitudes NIP-46
# Relays alcanzables verificados el 22-11-2026; relay.nsec.app no respondía desde la red de prueba.
DEFAULT_RELAYS = ["wss://nos.lol", "wss://relay.primal.net", "wss://relay.damus.io"]

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
        # Diagnóstico sin datos sensibles: permite ver si las solicitudes llegan y por qué se descartan.
        self.activity = {"received": 0, "answered": 0, "unsent_replies": 0, "dropped": {}, "last_event_at": None}

    def _drop(self, reason: str, event: dict) -> None:
        dropped = self.activity["dropped"]
        dropped[reason] = dropped.get(reason, 0) + 1
        log.warning("evento descartado (%s): %s", reason, str(event.get("id", ""))[:8])
        self.audit.write({"dir": "drop", "client": str(event.get("pubkey", "")), "reason": reason,
                          "event_id": str(event.get("id", ""))[:16], "created_at": event.get("created_at")})

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
        self.activity["received"] += 1
        self.activity["last_event_at"] = time.time()
        if event.get("kind") != NIP46_KIND or not crypto.verify_event(event):
            self._drop("firma o kind inválido", event)
            return None
        if self.signer.pubkey not in [t[1] for t in event.get("tags", []) if len(t) > 1 and t[0] == "p"]:
            self._drop("no dirigido a este firmante", event)
            return None
        if abs(time.time() - event.get("created_at", 0)) > MAX_EVENT_AGE:
            # un relay puede reenviar eventos antiguos; también evita repetir solicitudes capturadas
            self._drop("fuera de la ventana de tiempo", event)
            return None
        try:
            plaintext, scheme = crypto.decrypt(self.signer.keys, client, event["content"])
            request = json.loads(plaintext)
        except Exception:
            self._drop("no se pudo descifrar", event)
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
        if await self.pool.publish(reply):
            self.activity["answered"] += 1
        else:
            # ningún relay conectado: el cliente remoto nunca recibirá esta respuesta
            self.activity["unsent_replies"] += 1
            log.warning("respuesta a %s no enviada: ningún relay conectado", method)
        return response
