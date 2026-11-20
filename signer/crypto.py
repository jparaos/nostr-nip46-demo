"""Primitivas criptográficas de Nostr.

nostr-sdk se usa solo para lo que no conviene reimplementar (curva secp256k1,
firma Schnorr, cifrado NIP-44/NIP-04). El armado y la serialización de eventos
(NIP-01) se hace aquí para que el flujo sea explícito y revisable.
"""

from __future__ import annotations

import hashlib
import json
import time

import nostr_sdk as sdk

NIP04 = "nip04"
NIP44 = "nip44"


def load_keys(nsec_or_hex: str | None) -> tuple[sdk.Keys, bool]:
    """Carga una clave existente (nsec o hex) o genera una nueva.

    Retorna (keys, es_nueva).
    """
    if nsec_or_hex:
        return sdk.Keys.parse(nsec_or_hex.strip()), False
    return sdk.Keys.generate(), True


def pubkey_hex(keys: sdk.Keys) -> str:
    return keys.public_key().to_hex()


def event_id(event: dict) -> str:
    """Id de un evento según NIP-01: sha256 de la serialización canónica."""
    serialized = json.dumps(
        [0, event["pubkey"], event["created_at"], event["kind"], event["tags"], event["content"]],
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def sign_event(keys: sdk.Keys, unsigned: dict) -> dict:
    """Completa pubkey, id y sig de un evento sin firmar."""
    event = {
        "pubkey": pubkey_hex(keys),
        "created_at": int(unsigned.get("created_at") or time.time()),
        "kind": int(unsigned["kind"]),
        "tags": unsigned.get("tags") or [],
        "content": unsigned.get("content") or "",
    }
    event["id"] = event_id(event)
    event["sig"] = keys.sign_schnorr(bytes.fromhex(event["id"]))
    return event


def build_event(keys: sdk.Keys, kind: int, content: str, tags: list[list[str]]) -> dict:
    return sign_event(keys, {"kind": kind, "content": content, "tags": tags, "created_at": int(time.time())})


def verify_event(event: dict) -> bool:
    """Verifica id y firma de un evento recibido desde un relay."""
    try:
        return event_id(event) == event["id"] and sdk.Event.from_json(json.dumps(event)).verify()
    except Exception:
        return False


def detect_scheme(payload: str) -> str:
    """NIP-04 tiene la forma '<base64>?iv=<base64>'; NIP-44 es base64 plano."""
    return NIP04 if "?iv=" in payload else NIP44


def encrypt(keys: sdk.Keys, peer_hex: str, plaintext: str, scheme: str) -> str:
    peer = sdk.PublicKey.parse(peer_hex)
    if scheme == NIP04:
        return sdk.nip04_encrypt(keys.secret_key(), peer, plaintext)
    return sdk.nip44_encrypt(keys.secret_key(), peer, plaintext, sdk.Nip44Version.V2)


def decrypt(keys: sdk.Keys, peer_hex: str, payload: str) -> tuple[str, str]:
    """Descifra y retorna (texto, esquema usado) para responder con el mismo."""
    peer = sdk.PublicKey.parse(peer_hex)
    scheme = detect_scheme(payload)
    if scheme == NIP04:
        return sdk.nip04_decrypt(keys.secret_key(), peer, payload), NIP04
    return sdk.nip44_decrypt(keys.secret_key(), peer, payload), NIP44
