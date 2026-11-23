"""Inicia la aplicación web. Escucha SOLO en 127.0.0.1 (no es configurable).

    python -m app [--port 8046] [--vault RUTA] [--relay wss://...] [--approval-timeout 120]
"""

import argparse
import logging
from pathlib import Path

import uvicorn

from signer.service import DEFAULT_RELAYS

from .main import DEFAULT_PORT, create_app

HOST = "127.0.0.1"


def main() -> None:
    parser = argparse.ArgumentParser(description="Firmante NIP-46 — interfaz web local")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--vault", type=Path, default=None, help="ruta de la bóveda (~/.nip46-signer/vault.json)")
    parser.add_argument("--relay", action="append", help="relay a usar (repetible)")
    parser.add_argument("--approval-timeout", type=float, default=120.0)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="[%(name)s] %(message)s")
    logging.getLogger("relay").setLevel(logging.WARNING)

    app = create_app(vault_path=args.vault, port=args.port, relays=args.relay or DEFAULT_RELAYS,
                     approval_timeout=args.approval_timeout)
    print(f"Abre http://{HOST}:{args.port} en tu navegador")
    uvicorn.run(app, host=HOST, port=args.port, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
