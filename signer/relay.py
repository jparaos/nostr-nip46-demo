"""Conexión a relays Nostr (NIP-01) sobre WebSocket.

Mantiene una conexión por relay, se suscribe a un filtro y entrega los eventos
recibidos (sin duplicados) a una cola. Reintenta la conexión si un relay cae.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets

import websockets

log = logging.getLogger("relay")


class RelayPool:
    def __init__(self, urls: list[str], filters: dict):
        self.urls = urls
        self.filters = filters
        self.events: asyncio.Queue[dict] = asyncio.Queue()
        self._conns: dict[str, websockets.ClientConnection] = {}
        self._seen: set[str] = set()
        self._sub_id = secrets.token_hex(8)
        self._tasks: list[asyncio.Task] = []
        self.ready = asyncio.Event()

    async def start(self) -> None:
        self._tasks = [asyncio.create_task(self._run(url)) for url in self.urls]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def publish(self, event: dict) -> int:
        """Publica en todos los relays conectados. Retorna a cuántos se envió."""
        message = json.dumps(["EVENT", event])
        sent = 0
        for url, ws in list(self._conns.items()):
            try:
                await ws.send(message)
                sent += 1
            except Exception as exc:
                log.warning("no se pudo publicar en %s: %s", url, exc)
        return sent

    async def _run(self, url: str) -> None:
        delay = 1
        while True:
            try:
                async with websockets.connect(url, open_timeout=10) as ws:
                    self._conns[url] = ws
                    await ws.send(json.dumps(["REQ", self._sub_id, self.filters]))
                    log.info("suscrito en %s", url)
                    delay = 1
                    async for raw in ws:
                        self._on_message(url, raw)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # solo el primer fallo es warning, para no ensuciar la consola mientras se pide aprobación
                level = logging.WARNING if delay == 1 else logging.DEBUG
                log.log(level, "relay %s desconectado (%s); reintento en %ss", url, exc, delay)
            finally:
                self._conns.pop(url, None)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)

    def _on_message(self, url: str, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except ValueError:
            return
        kind = msg[0] if msg else None
        if kind == "EVENT" and len(msg) >= 3:
            event = msg[2]
            if event.get("id") not in self._seen:
                self._seen.add(event.get("id"))
                self.events.put_nowait(event)
        elif kind == "EOSE":
            self.ready.set()
        elif kind == "OK" and len(msg) >= 4 and not msg[2]:
            log.warning("%s rechazó el evento %s: %s", url, msg[1][:8], msg[3])
        elif kind in ("NOTICE", "CLOSED"):
            log.warning("%s %s: %s", url, kind, msg[1:])
