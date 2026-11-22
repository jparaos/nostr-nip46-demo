"""Registro de auditoría (JSON Lines) con minimización de datos.

Qué NO se guarda y por qué:
- El `secret` de connect: permite autorizar un cliente.
- El evento firmado completo: un evento kind 27235 (NIP-98) firmado funciona
  como credencial de inicio de sesión mientras el desafío esté vigente.
  Se guarda solo su id.
- El contenido de los eventos: puede ser privado. Se guarda su largo y un hash.
- Textos cifrados o descifrados de terceros.
Sí se guarda lo necesario para reconstruir qué pasó: cliente, método,
esquema de cifrado, kind, nombres de tags y resultado.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path


class AuditLog:
    def __init__(self, path: Path | str | None = Path("logs/session.jsonl")):
        self.path = Path(path) if path else None

    def write(self, record: dict) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **record}, ensure_ascii=False)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def request(self, client: str, scheme: str, request: dict) -> None:
        method = request.get("method", "")
        self.write({"dir": "in", "client": client, "scheme": scheme, "id": request.get("id"),
                    "method": method, "params": sanitize_params(method, request.get("params") or [])})

    def response(self, client: str, scheme: str, method: str, response: dict) -> None:
        record = {"dir": "out", "client": client, "scheme": scheme, "id": response.get("id"), "method": method}
        if "error" in response:
            record["error"] = response["error"]
        else:
            record["result"] = sanitize_result(method, response.get("result", ""))
        self.write(record)


def sanitize_params(method: str, params: list) -> list | dict:
    if method == "connect":
        return {"target": params[0] if params else "", "secret_present": len(params) > 1 and bool(params[1])}
    if method == "sign_event" and params:
        return summarize_event(params[0])
    if method in ("get_public_key", "ping", "switch_relays"):
        return params
    return {"count": len(params)}  # métodos no soportados: no se guarda su contenido


def sanitize_result(method: str, result: str) -> str | dict:
    if method == "sign_event":
        try:
            return {"signed_event_id": json.loads(result)["id"]}
        except (ValueError, KeyError, TypeError):
            return {"signed_event_id": None}
    return result


def summarize_event(raw) -> dict:
    try:
        event = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except (ValueError, TypeError):
        return {"invalid": True}
    content = str(event.get("content", ""))
    return {
        "kind": event.get("kind"),
        "tag_names": [t[0] for t in event.get("tags") or [] if t],
        "content_len": len(content),
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest()[:16],
        "created_at": event.get("created_at"),
    }
