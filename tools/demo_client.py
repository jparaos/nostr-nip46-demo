"""Cliente NIP-46 de prueba: simula lo que hace Stacker News con una URL bunker://.

Uso:
    python -m tools.demo_client "bunker://<pubkey>?relay=...&secret=..."

Envía connect -> get_public_key -> sign_event (evento de autenticación de ejemplo)
y verifica la firma devuelta. Sirve para probar el firmante sin depender del sitio.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import sys
import time
from urllib.parse import parse_qs, urlparse

from signer import crypto
from signer.relay import RelayPool

NIP46_KIND = 24133


def parse_bunker(url: str) -> tuple[str, list[str], str | None]:
    parsed = urlparse(url)
    if parsed.scheme != "bunker":
        raise ValueError("se esperaba una URL bunker://")
    query = parse_qs(parsed.query)
    return parsed.netloc, query.get("relay", []), (query.get("secret") or [None])[0]


async def main(url: str) -> None:
    signer_pk, relays, secret = parse_bunker(url)
    client_keys, _ = crypto.load_keys(None)  # clave temporal del cliente, como hace Stacker News
    client_pk = crypto.pubkey_hex(client_keys)
    pool = RelayPool(relays, {"kinds": [NIP46_KIND], "#p": [client_pk], "since": int(time.time()) - 10})
    await pool.start()
    await asyncio.wait_for(pool.ready.wait(), 15)

    async def call(method: str, params: list) -> str:
        req = {"id": secrets.token_hex(4), "method": method, "params": params}
        content = crypto.encrypt(client_keys, signer_pk, json.dumps(req), crypto.NIP44)
        await pool.publish(crypto.build_event(client_keys, NIP46_KIND, content, [["p", signer_pk]]))
        print(f"-> {method}")
        while True:
            event = await asyncio.wait_for(pool.events.get(), 60)
            if event["pubkey"] != signer_pk or not crypto.verify_event(event):
                continue
            resp = json.loads(crypto.decrypt(client_keys, signer_pk, event["content"])[0])
            if resp.get("id") == req["id"]:
                if resp.get("error"):
                    raise RuntimeError(f"{method}: {resp['error']}")
                print(f"<- {resp['result'][:100]}")
                return resp["result"]

    try:
        assert await call("connect", [signer_pk, secret or ""]) in ("ack", secret)
        user_pk = await call("get_public_key", [])
        challenge = secrets.token_hex(16)
        unsigned = {"kind": 22242, "created_at": int(time.time()), "content": "",
                    "tags": [["challenge", challenge]]}
        signed = json.loads(await call("sign_event", [json.dumps(unsigned)]))
        ok = crypto.verify_event(signed) and signed["pubkey"] == user_pk and ["challenge", challenge] in signed["tags"]
        print("\nRESULTADO:", "OK - firma válida del usuario" if ok else "FALLO - firma inválida")
    finally:
        await pool.stop()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
