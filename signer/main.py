"""Punto de entrada del firmante NIP-46 (prototipo).

Uso:
    python -m signer.main                    # genera una clave nueva
    NSEC=nsec1... python -m signer.main      # usa una clave existente
    python -m signer.main --relay wss://... --relay wss://...
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import time
from pathlib import Path

from . import crypto
from .nip46 import Nip46Signer, bunker_url, new_secret
from .relay import RelayPool

NIP46_KIND = 24133
DEFAULT_RELAYS = ["wss://relay.nsec.app", "wss://relay.damus.io", "wss://nos.lol"]
LOG_FILE = Path("logs/session.jsonl")

log = logging.getLogger("signer")


def audit(record: dict) -> None:
    """Registro de cada solicitud/respuesta. Nunca incluye el nsec ni el secret."""
    LOG_FILE.parent.mkdir(exist_ok=True)
    record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **record}
    with LOG_FILE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def summarize(method: str, params: list) -> str:
    if method == "connect":
        return "connect (secret recibido)" if len(params) > 1 and params[1] else "connect (sin secret)"
    if method == "sign_event" and params:
        try:
            ev = json.loads(params[0])
            return f"sign_event kind={ev.get('kind')} tags={ev.get('tags')} content={ev.get('content')!r}"
        except (ValueError, TypeError):
            pass
    return f"{method} params={params}"


def redact(method: str, params: list) -> list:
    if method == "connect" and len(params) > 1:
        return [params[0], "<secret>", *params[2:]]
    return params


def make_approver(auto: bool):
    async def approve(unsigned: dict, client: str) -> bool:
        print("\n" + "=" * 70)
        print(f"Solicitud de firma del cliente {client[:16]}…")
        print(json.dumps(unsigned, indent=2, ensure_ascii=False))
        if auto:
            print("--auto-approve activo: firmando sin preguntar")
            return True
        answer = await asyncio.to_thread(input, "¿Firmar este evento? [s/N] ")
        return answer.strip().lower() in ("s", "si", "sí", "y", "yes")

    return approve


async def run(relays: list[str], auto_approve: bool) -> None:
    keys, is_new = crypto.load_keys(os.environ.get("NSEC"))
    secret = new_secret()
    signer = Nip46Signer(keys, secret, make_approver(auto_approve))

    print(f"[init] pubkey usuario: {keys.public_key().to_bech32()}")
    print(f"[init]             hex: {signer.pubkey}")
    if is_new:
        print("[init] Clave NUEVA generada. Para reutilizarla define NSEC con este valor (no lo compartas):")
        print(f"       {keys.secret_key().to_bech32()}")
    print(f"[init] relays: {', '.join(relays)}")
    print("[init] Pega esta URL en Stacker News (login con Nostr):\n")
    print(f"  {bunker_url(signer.pubkey, relays, secret)}\n")

    pool = RelayPool(relays, {"kinds": [NIP46_KIND], "#p": [signer.pubkey], "since": int(time.time()) - 10})
    await pool.start()
    try:
        while True:
            event = await pool.events.get()
            await process(event, signer, pool)
    finally:
        await pool.stop()


async def process(event: dict, signer: Nip46Signer, pool: RelayPool) -> None:
    client = event.get("pubkey", "")
    if event.get("kind") != NIP46_KIND or not crypto.verify_event(event):
        log.warning("evento descartado (kind o firma inválida): %s", event.get("id", "")[:8])
        return
    try:
        plaintext, scheme = crypto.decrypt(signer.keys, client, event["content"])
        request = json.loads(plaintext)
    except Exception as exc:
        log.warning("no se pudo descifrar/parsear solicitud de %s: %s", client[:8], exc)
        audit({"dir": "in", "client": client, "error": f"decrypt: {exc}"})
        return

    method = request.get("method", "")
    params = request.get("params") or []
    print(f"[req]  de={client[:12]}… cifrado={scheme} {summarize(method, params)}")
    audit({"dir": "in", "client": client, "scheme": scheme, "id": request.get("id"),
           "method": method, "params": redact(method, params)})

    response = await signer.handle(client, request)
    shown = response.get("error") or response["result"]
    print(f"[resp] {'ERROR ' if 'error' in response else ''}{shown[:120]}")
    audit({"dir": "out", "client": client, "scheme": scheme, **response})

    reply = crypto.build_event(
        signer.keys, NIP46_KIND, crypto.encrypt(signer.keys, client, json.dumps(response), scheme), [["p", client]]
    )
    await pool.publish(reply)


def main() -> None:
    parser = argparse.ArgumentParser(description="Firmante remoto NIP-46 (prototipo)")
    parser.add_argument("--relay", action="append", help="relay a usar (repetible)")
    parser.add_argument("--auto-approve", action="store_true", help="firma sin preguntar (solo pruebas)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="[%(name)s] %(message)s")
    try:
        asyncio.run(run(args.relay or DEFAULT_RELAYS, args.auto_approve))
    except KeyboardInterrupt:
        print("\nFirmante detenido.")


if __name__ == "__main__":
    main()
