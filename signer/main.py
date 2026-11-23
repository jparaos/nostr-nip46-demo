"""Interfaz de consola del firmante NIP-46.

Uso:
    python -m signer.main init               # crea una bóveda con una clave nueva
    python -m signer.main init --import      # crea una bóveda importando un nsec/hex
    python -m signer.main                    # desbloquea la bóveda e inicia el firmante
    python -m signer.main --ephemeral        # clave temporal sin bóveda (solo pruebas)
    python -m signer.main --relay wss://... --approval-timeout 60 --vault RUTA

Este módulo solo se ocupa de la interacción por consola. La lógica vive en:
    service.py    ciclo de vida de relays y procesamiento de eventos
    approvals.py  cola asíncrona de aprobaciones
    nip46.py      protocolo NIP-46
    vault.py      bóveda cifrada de la clave
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import logging
import sys
import threading
from pathlib import Path

from . import crypto
from .approvals import ApprovalError, ApprovalManager, SignRequest
from .audit import AuditLog
from .service import DEFAULT_RELAYS, SignerService
from .vault import DEFAULT_PATH, Vault, VaultError


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


# ---- bóveda -------------------------------------------------------------------

def ask_new_password() -> str:
    while True:
        password = getpass.getpass("Nueva contraseña de la bóveda (mín. 8): ")
        if password != getpass.getpass("Repite la contraseña: "):
            print("[!] las contraseñas no coinciden")
            continue
        return password


def cmd_init(vault: Vault, import_key: bool) -> int:
    if vault.exists:
        print(f"[!] ya existe una bóveda en {vault.path}. No se sobrescribe.")
        return 1
    try:
        if import_key:
            secret = getpass.getpass("Clave secreta a importar (nsec1… o hex, no se muestra): ")
            pubkey = vault.import_key(secret, ask_new_password())
            del secret
            print(f"[ok] clave importada. pubkey: {pubkey}")
        else:
            created = vault.create_vault(ask_new_password())
            print(f"[ok] bóveda creada. npub: {created.npub}")
            print("\nRESPALDO — se muestra UNA sola vez. Guárdalo fuera de este equipo:")
            print(f"  {created.nsec_backup}\n")
            del created
    except VaultError as exc:
        print(f"[!] {exc}")
        return 1
    finally:
        vault.lock_vault()
    print(f"[ok] guardada cifrada en {vault.path}")
    return 0


def unlock_interactive(vault: Vault):
    if not vault.exists:
        raise SystemExit(f"[!] no hay bóveda en {vault.path}. Créala con: python -m signer.main init")
    for _ in range(3):
        try:
            vault.unlock_vault(getpass.getpass(f"Contraseña de la bóveda ({vault.get_public_key()[:12]}…): "))
            return vault.get_signing_keys()
        except VaultError as exc:
            print(f"[!] {exc}")
    raise SystemExit("[!] demasiados intentos fallidos")


# ---- firmante -----------------------------------------------------------------

async def run_signer(keys, relays: list[str], auto_approve: bool, timeout: float) -> None:
    manager = ApprovalManager(timeout=timeout)
    service = SignerService(keys, manager, relays=relays, audit=AuditLog())

    print(f"[init] pubkey usuario: {keys.public_key().to_bech32()}")
    print(f"[init]             hex: {service.pubkey}")
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
    parser.add_argument("command", nargs="?", choices=["run", "init"], default="run")
    parser.add_argument("--import", dest="import_key", action="store_true", help="init: importar nsec/hex existente")
    parser.add_argument("--vault", type=Path, default=DEFAULT_PATH, help=f"ruta de la bóveda ({DEFAULT_PATH})")
    parser.add_argument("--ephemeral", action="store_true", help="clave temporal sin bóveda (solo pruebas)")
    parser.add_argument("--relay", action="append", help="relay a usar (repetible)")
    parser.add_argument("--auto-approve", action="store_true", help="firma sin preguntar (solo pruebas)")
    parser.add_argument("--approval-timeout", type=float, default=120.0, help="segundos antes de rechazar (120)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="[%(name)s] %(message)s")
    logging.getLogger("service").setLevel(logging.INFO)  # solicitudes y respuestas siempre visibles

    vault = Vault(args.vault)
    if args.command == "init":
        sys.exit(cmd_init(vault, args.import_key))

    if args.ephemeral:
        keys, _ = crypto.load_keys(None)  # nunca se muestra ni se guarda
        print("[init] modo --ephemeral: clave temporal, se pierde al cerrar")
    else:
        keys = unlock_interactive(vault)
    try:
        asyncio.run(run_signer(keys, args.relay or DEFAULT_RELAYS, args.auto_approve, args.approval_timeout))
    except KeyboardInterrupt:
        print("\nFirmante detenido.")
    finally:
        del keys
        vault.lock_vault()


if __name__ == "__main__":
    main()
