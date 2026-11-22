"""Interfaz de consola del firmante NIP-46.

Uso:
    python -m signer.main                    # genera una clave nueva
    NSEC=nsec1... python -m signer.main      # usa una clave existente
    python -m signer.main --relay wss://... --approval-timeout 60

Este módulo solo se ocupa de la interacción por consola. La lógica vive en:
    service.py    ciclo de vida de relays y procesamiento de eventos
    approvals.py  cola asíncrona de aprobaciones
    nip46.py      protocolo NIP-46
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import threading

from . import crypto
from .approvals import ApprovalError, ApprovalManager, SignRequest
from .audit import AuditLog
from .service import DEFAULT_RELAYS, SignerService


async def ainput(prompt: str) -> str:
    """input() en un hilo daemon: no bloquea el event loop ni impide salir con Ctrl+C."""
    loop = asyncio.get_running_loop()
    future: asyncio.Future[str] = loop.create_future()

    def reader() -> None:
        try:
            value = input(prompt)
        except EOFError:
            value = ""
        loop.call_soon_threadsafe(lambda: future.done() or future.set_result(value))

    threading.Thread(target=reader, daemon=True).start()
    return await future


class ConsoleApprover:
    """Atiende las solicitudes del ApprovalManager preguntando en la consola."""

    def __init__(self, manager: ApprovalManager, auto_approve: bool = False):
        self.manager = manager
        self.auto_approve = auto_approve

    async def run(self) -> None:
        while True:
            request = await self.manager.next_request()
            if request.status.value != "pending":
                continue  # expiró o se canceló antes de llegar a mostrarse
            self._show(request)
            if self.auto_approve:
                print("--auto-approve activo: firmando sin preguntar")
                self._decide(request, True)
                continue
            answer = await ainput("¿Firmar este evento? [s/N] ")
            self._decide(request, answer.strip().lower() in ("s", "si", "sí", "y", "yes"))

    def _show(self, request: SignRequest) -> None:
        print("\n" + "=" * 70)
        print(f"Solicitud de firma {request.id[:8]} del cliente {request.client[:16]}…")
        print(json.dumps({"kind": request.kind, "content": request.content, "tags": request.tags,
                          "created_at": request.created_at}, indent=2, ensure_ascii=False))
        print(f"(se rechaza automáticamente en {self.manager.timeout:g}s)")

    def _decide(self, request: SignRequest, approve: bool) -> None:
        try:
            if approve:
                self.manager.approve_request(request.id)
                print("[ok] solicitud aprobada")
            else:
                self.manager.reject_request(request.id)
                print("[ok] solicitud rechazada")
        except ApprovalError:
            print(f"[!] la solicitud {request.id[:8]} ya no está pendiente (expiró o se canceló)")


async def run(relays: list[str], auto_approve: bool, timeout: float) -> None:
    keys, is_new = crypto.load_keys(os.environ.get("NSEC"))
    manager = ApprovalManager(timeout=timeout)
    service = SignerService(keys, manager, relays=relays, audit=AuditLog())

    print(f"[init] pubkey usuario: {keys.public_key().to_bech32()}")
    print(f"[init]             hex: {service.pubkey}")
    if is_new:
        print("[init] Clave NUEVA generada. Para reutilizarla define NSEC con este valor (no lo compartas):")
        print(f"       {keys.secret_key().to_bech32()}")
    print(f"[init] relays: {', '.join(relays)}")
    print("[init] Pega esta URL en Stacker News (login con Nostr):\n")
    print(f"  {service.bunker_url}\n")

    await service.start()
    console = asyncio.create_task(ConsoleApprover(manager, auto_approve).run())
    try:
        await asyncio.Event().wait()  # corre hasta Ctrl+C
    finally:
        console.cancel()
        await service.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Firmante remoto NIP-46 (consola)")
    parser.add_argument("--relay", action="append", help="relay a usar (repetible)")
    parser.add_argument("--auto-approve", action="store_true", help="firma sin preguntar (solo pruebas)")
    parser.add_argument("--approval-timeout", type=float, default=120.0, help="segundos antes de rechazar (120)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="[%(name)s] %(message)s")
    logging.getLogger("service").setLevel(logging.INFO)  # solicitudes y respuestas siempre visibles
    try:
        asyncio.run(run(args.relay or DEFAULT_RELAYS, args.auto_approve, args.approval_timeout))
    except KeyboardInterrupt:
        print("\nFirmante detenido.")


if __name__ == "__main__":
    main()
