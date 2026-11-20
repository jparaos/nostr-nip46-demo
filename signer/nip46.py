"""Lógica del firmante remoto NIP-46.

Recibe solicitudes ya descifradas ({"id", "method", "params"}) y produce la
respuesta ({"id", "result"} o {"id", "result", "error"}). No sabe nada de
relays ni de cifrado: eso queda en relay.py y main.py.
"""

from __future__ import annotations

import json
import secrets
from typing import Awaitable, Callable
from urllib.parse import quote

import nostr_sdk as sdk

from . import crypto

# Recibe el evento sin firmar y el pubkey del cliente; retorna True si se aprueba.
ApproveFn = Callable[[dict, str], Awaitable[bool]]


def new_secret() -> str:
    return secrets.token_hex(16)


def bunker_url(signer_pubkey: str, relays: list[str], secret: str) -> str:
    query = "&".join(f"relay={quote(r, safe='')}" for r in relays)
    return f"bunker://{signer_pubkey}?{query}&secret={secret}"


class Nip46Signer:
    """Firmante NIP-46 mínimo.

    Supuesto S1: la clave del firmante (transporte) y la del usuario son la misma.
    Supuesto S5: el secret es de un solo uso; luego se recuerdan los clientes
    autorizados durante la vida del proceso.
    """

    def __init__(self, keys: sdk.Keys, secret: str, approve: ApproveFn):
        self.keys = keys
        self.pubkey = crypto.pubkey_hex(keys)
        self._secret: str | None = secret
        self._approve = approve
        self.authorized: set[str] = set()

    async def handle(self, client: str, request: dict) -> dict:
        req_id = str(request.get("id", ""))
        method = request.get("method")
        params = request.get("params") or []

        handlers = {
            "connect": self._connect,
            "ping": self._ping,
            "get_public_key": self._get_public_key,
            "sign_event": self._sign_event,
            "switch_relays": self._switch_relays,
        }
        handler = handlers.get(method)
        if handler is None:
            return _error(req_id, f"method not supported: {method}")
        if method not in ("connect", "ping") and client not in self.authorized:
            return _error(req_id, "unauthorized: send connect with a valid secret first")
        try:
            return {"id": req_id, "result": await handler(client, params)}
        except Exception as exc:  # cualquier fallo se informa al cliente, nunca se cae el firmante
            return _error(req_id, str(exc))

    async def _connect(self, client: str, params: list) -> str:
        target = params[0] if params else ""
        given = params[1] if len(params) > 1 else ""
        if target and target != self.pubkey:
            raise ValueError("connect addressed to a different signer")
        if client in self.authorized:
            return "ack"
        if self._secret is None or not secrets.compare_digest(given or "", self._secret):
            raise PermissionError("invalid or already used secret")
        self._secret = None  # un solo uso
        self.authorized.add(client)
        return "ack"

    async def _ping(self, client: str, params: list) -> str:
        return "pong"

    async def _get_public_key(self, client: str, params: list) -> str:
        return self.pubkey

    async def _switch_relays(self, client: str, params: list) -> str:
        return "null"  # NIP-46: null = el cliente sigue en los relays actuales

    async def _sign_event(self, client: str, params: list) -> str:
        if not params:
            raise ValueError("sign_event requires an event")
        unsigned = json.loads(params[0]) if isinstance(params[0], str) else params[0]
        if not await self._approve(unsigned, client):
            raise PermissionError("user rejected the signature")
        return json.dumps(crypto.sign_event(self.keys, unsigned), ensure_ascii=False)


def _error(req_id: str, message: str) -> dict:
    return {"id": req_id, "result": "", "error": message}
