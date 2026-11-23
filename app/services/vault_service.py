"""Acceso asíncrono a la bóveda.

scrypt tarda ~0,3 s y usa ~128 MiB: se ejecuta en un hilo (asyncio.to_thread)
para no bloquear el event loop, que sigue atendiendo relays y la API.
Un asyncio.Lock evita operaciones simultáneas sobre la misma bóveda.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import nostr_sdk as sdk

from signer.vault import NewVault, Vault


class VaultService:
    def __init__(self, path: Path, scrypt_n: int | None = None):
        self.vault = Vault(path, scrypt_n=scrypt_n) if scrypt_n else Vault(path)
        self._lock = asyncio.Lock()

    @property
    def exists(self) -> bool:
        return self.vault.exists

    @property
    def is_unlocked(self) -> bool:
        return self.vault.is_unlocked

    def public_key(self) -> str | None:
        return self.vault.get_public_key() if self.vault.exists else None

    async def create(self, password: str) -> NewVault:
        async with self._lock:
            return await asyncio.to_thread(self.vault.create_vault, password)

    async def import_key(self, secret: str, password: str) -> str:
        async with self._lock:
            return await asyncio.to_thread(self.vault.import_key, secret, password)

    async def unlock(self, password: str) -> str:
        """Verifica la contraseña. Si ya estaba desbloqueada, igual la exige (nueva sesión)."""
        async with self._lock:
            return await asyncio.to_thread(self.vault.unlock_vault, password)

    def lock(self) -> None:
        self.vault.lock_vault()

    def signing_keys(self) -> sdk.Keys:
        return self.vault.get_signing_keys()
