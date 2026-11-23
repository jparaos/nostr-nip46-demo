"""Bóveda local cifrada para una clave secreta Nostr.

Diseño (una responsabilidad por sección):
    1. Gestión de claves      generar / validar / importar (nostr-sdk)
    2. Derivación             scrypt (stdlib) con salt aleatorio por bóveda
    3. Cifrado                AES-256-GCM (cryptography) con nonce aleatorio
    4. Persistencia           JSON atómico, sin sobrescritura accidental, permisos restrictivos
    5. Estado de desbloqueo   clase Vault: la clave vive en memoria solo mientras está desbloqueada

Formato del archivo (versión 1):
    {
      "version": 1,
      "pubkey": "<hex>",                                       # público, permite mostrar la identidad bloqueada
      "kdf": {"name": "scrypt", "n": 131072, "r": 8, "p": 1, "salt": "<b64>"},
      "cipher": {"name": "aes-256-gcm", "nonce": "<b64>"},
      "ciphertext": "<b64>"                                    # 32 bytes de clave + 16 de tag GCM
    }
El encabezado (version, pubkey, kdf, cipher) va como datos asociados (AAD) de GCM:
si alguien altera la pubkey o baja el costo de scrypt en el archivo, el descifrado falla.

Ningún mensaje de excepción incluye la contraseña ni la clave; las excepciones de
librerías externas se re-lanzan con `from None` para no arrastrar su contexto.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

import nostr_sdk as sdk
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

VERSION = 1
SCRYPT_N = 2**17  # ~0,3 s y 128 MiB por intento en un equipo actual
SCRYPT_R = 8
SCRYPT_P = 1
SALT_LEN = 16
NONCE_LEN = 12  # recomendado para GCM
KEY_LEN = 32  # AES-256
MIN_PASSWORD_LEN = 8
DEFAULT_PATH = Path.home() / ".nip46-signer" / "vault.json"


# ---- errores ------------------------------------------------------------------

class VaultError(Exception):
    """Error base. Los mensajes nunca contienen secretos."""


class VaultExistsError(VaultError):
    pass


class VaultNotFoundError(VaultError):
    pass


class VaultLockedError(VaultError):
    pass


class WrongPasswordError(VaultError):
    pass


class CorruptVaultError(VaultError):
    pass


class InvalidKeyError(VaultError):
    pass


class WeakPasswordError(VaultError):
    pass


# ---- 1. gestión de claves ----------------------------------------------------

def generate_keys() -> sdk.Keys:
    return sdk.Keys.generate()


def parse_secret_key(value: str) -> sdk.Keys:
    """Valida e importa una clave en formato nsec (bech32) o hex de 64 caracteres."""
    candidate = (value or "").strip()
    is_nsec = candidate.lower().startswith("nsec1")
    is_hex = len(candidate) == 64 and all(c in "0123456789abcdefABCDEF" for c in candidate)
    if not (is_nsec or is_hex):
        raise InvalidKeyError("la clave debe ser un nsec1… o 64 caracteres hexadecimales")
    try:
        return sdk.Keys.parse(candidate)
    except Exception:
        raise InvalidKeyError("clave secreta inválida") from None  # sin eco del valor


def secret_bytes(keys: sdk.Keys) -> bytes:
    return bytes.fromhex(keys.secret_key().to_hex())


def keys_from_bytes(raw: bytes | bytearray) -> sdk.Keys:
    return sdk.Keys(sdk.SecretKey.from_bytes(bytes(raw)))


# ---- 2. derivación de la contraseña -----------------------------------------

def derive_key(password: str, salt: bytes, n: int = SCRYPT_N, r: int = SCRYPT_R, p: int = SCRYPT_P) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p,
                          maxmem=128 * r * n + 16 * 1024 * 1024, dklen=KEY_LEN)


def check_password(password: str) -> None:
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LEN:
        raise WeakPasswordError(f"la contraseña debe tener al menos {MIN_PASSWORD_LEN} caracteres")


# ---- 3. cifrado ------------------------------------------------------------------

def encrypt_secret(key: bytes, plaintext: bytes, aad: bytes) -> tuple[bytes, bytes]:
    nonce = secrets.token_bytes(NONCE_LEN)
    return nonce, AESGCM(key).encrypt(nonce, plaintext, aad)


def decrypt_secret(key: bytes, nonce: bytes, ciphertext: bytes, aad: bytes) -> bytes:
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, aad)
    except InvalidTag:
        # GCM no distingue contraseña incorrecta de archivo alterado; la causa
        # más común es la contraseña.
        raise WrongPasswordError("contraseña incorrecta o bóveda alterada") from None


def _aad(header: dict) -> bytes:
    return json.dumps(header, sort_keys=True, separators=(",", ":")).encode("utf-8")


def seal(keys: sdk.Keys, password: str, n: int = SCRYPT_N) -> dict:
    """Arma el documento cifrado de la bóveda (sin tocar el disco)."""
    salt = secrets.token_bytes(SALT_LEN)
    header = {
        "version": VERSION,
        "pubkey": keys.public_key().to_hex(),
        "kdf": {"name": "scrypt", "n": n, "r": SCRYPT_R, "p": SCRYPT_P, "salt": _b64(salt)},
    }
    key = derive_key(password, salt, n, SCRYPT_R, SCRYPT_P)
    raw = bytearray(secret_bytes(keys))
    try:
        nonce, ciphertext = encrypt_secret(key, bytes(raw), _aad(header))
    finally:
        _wipe(raw)
    return {**header, "cipher": {"name": "aes-256-gcm", "nonce": _b64(nonce)}, "ciphertext": _b64(ciphertext)}


def unseal(document: dict, password: str) -> sdk.Keys:
    """Descifra el documento y verifica que la clave corresponda a la pubkey guardada."""
    try:
        if document["version"] != VERSION:
            raise CorruptVaultError(f"versión de bóveda no soportada: {document['version']}")
        kdf, cipher = document["kdf"], document["cipher"]
        if kdf["name"] != "scrypt" or cipher["name"] != "aes-256-gcm":
            raise CorruptVaultError("algoritmo no soportado")
        header = {"version": document["version"], "pubkey": document["pubkey"], "kdf": kdf}
        salt, nonce, ciphertext = _unb64(kdf["salt"]), _unb64(cipher["nonce"]), _unb64(document["ciphertext"])
        n, r, p = int(kdf["n"]), int(kdf["r"]), int(kdf["p"])
    except VaultError:
        raise
    except Exception:
        raise CorruptVaultError("archivo de bóveda corrupto o incompleto") from None
    # límites: evita que un archivo alterado degrade el costo (n bajo) o agote la memoria (n enorme)
    if len(salt) != SALT_LEN or len(nonce) != NONCE_LEN or not (2**14 <= n <= 2**20) or r != 8 or p != 1:
        raise CorruptVaultError("parámetros criptográficos inválidos")

    key = derive_key(password, salt, n, r, p)
    raw = bytearray(decrypt_secret(key, nonce, ciphertext, _aad(header)))
    try:
        keys = keys_from_bytes(raw)
    except Exception:
        raise CorruptVaultError("contenido descifrado inválido") from None
    finally:
        _wipe(raw)
    if keys.public_key().to_hex() != document["pubkey"]:
        raise CorruptVaultError("la clave no corresponde a la pubkey de la bóveda")
    return keys


# ---- 4. persistencia -----------------------------------------------------------

def write_vault_file(path: Path, document: dict, overwrite: bool = False) -> None:
    """Escritura atómica: archivo temporal en el mismo directorio + os.replace.

    El temporal solo contiene el documento ya cifrado. Sin overwrite=True nunca
    reemplaza una bóveda existente.
    """
    path = Path(path)
    if path.exists() and not overwrite:
        raise VaultExistsError(f"ya existe una bóveda en {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    _restrict(path.parent, directory=True)
    fd, tmp = tempfile.mkstemp(prefix=".vault-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(document, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        _restrict(Path(tmp))
        if not overwrite and path.exists():  # carrera: otro proceso la creó entretanto
            raise VaultExistsError(f"ya existe una bóveda en {path}")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def read_vault_file(path: Path) -> dict:
    path = Path(path)
    if not path.exists():
        raise VaultNotFoundError(f"no existe una bóveda en {path}")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise CorruptVaultError("archivo de bóveda corrupto (JSON inválido)") from None
    if not isinstance(document, dict):
        raise CorruptVaultError("archivo de bóveda corrupto (estructura inválida)")
    return document


def _restrict(path: Path, directory: bool = False) -> None:
    """Permisos solo para el dueño. En POSIX: 700/600. En Windows, os.chmod solo
    controla el atributo de solo lectura; la protección real la da la ACL del
    perfil de usuario donde vive la bóveda por defecto (~/.nip46-signer)."""
    if os.name == "posix":
        os.chmod(path, stat.S_IRWXU if directory else stat.S_IRUSR | stat.S_IWUSR)


# ---- 5. estado de desbloqueo ---------------------------------------------------

@dataclass(frozen=True)
class NewVault:
    """Resultado de create_vault. `nsec_backup` se entrega UNA sola vez para respaldo."""

    pubkey: str
    npub: str
    nsec_backup: str

    def __repr__(self) -> str:  # evita que el nsec aparezca si se imprime/loguea el objeto
        return f"NewVault(pubkey={self.pubkey!r}, npub={self.npub!r}, nsec_backup=<oculto>)"


class Vault:
    def __init__(self, path: Path | str = DEFAULT_PATH, scrypt_n: int = SCRYPT_N):
        self.path = Path(path)
        self._scrypt_n = scrypt_n
        self._keys: sdk.Keys | None = None

    def __repr__(self) -> str:
        return f"Vault(path={str(self.path)!r}, unlocked={self.is_unlocked})"

    @property
    def exists(self) -> bool:
        return self.path.exists()

    @property
    def is_unlocked(self) -> bool:
        return self._keys is not None

    def create_vault(self, password: str, overwrite: bool = False) -> NewVault:
        """Genera una clave nueva, la guarda cifrada y deja la bóveda desbloqueada."""
        check_password(password)
        keys = generate_keys()
        self._store(keys, password, overwrite)
        return NewVault(keys.public_key().to_hex(), keys.public_key().to_bech32(), keys.secret_key().to_bech32())

    def import_key(self, secret: str, password: str, overwrite: bool = False) -> str:
        """Importa un nsec/hex existente, lo guarda cifrado y deja la bóveda desbloqueada.
        Retorna la pubkey hex. El secreto importado nunca se devuelve."""
        check_password(password)
        keys = parse_secret_key(secret)
        self._store(keys, password, overwrite)
        return keys.public_key().to_hex()

    def unlock_vault(self, password: str) -> str:
        self._keys = unseal(read_vault_file(self.path), password)
        return self._keys.public_key().to_hex()

    def lock_vault(self) -> None:
        # Python no permite borrar la memoria de forma garantizada; se suelta la
        # única referencia para que el objeto (y su copia en Rust) se libere.
        self._keys = None

    def get_public_key(self) -> str:
        """Pubkey hex. Funciona con la bóveda bloqueada (es información pública)."""
        if self._keys is not None:
            return self._keys.public_key().to_hex()
        document = read_vault_file(self.path)
        pubkey = document.get("pubkey")
        if not isinstance(pubkey, str) or len(pubkey) != 64:
            raise CorruptVaultError("pubkey inválida en la bóveda")
        return pubkey

    def get_signing_keys(self) -> sdk.Keys:
        """Claves para el firmante. Solo con la bóveda desbloqueada."""
        if self._keys is None:
            raise VaultLockedError("la bóveda está bloqueada")
        return self._keys

    def _store(self, keys: sdk.Keys, password: str, overwrite: bool) -> None:
        if self.exists and not overwrite:
            raise VaultExistsError(f"ya existe una bóveda en {self.path}")
        write_vault_file(self.path, seal(keys, password, self._scrypt_n), overwrite)
        self._keys = keys


# ---- utilidades ----------------------------------------------------------------

def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.b64decode(text, validate=True)


def _wipe(buffer: bytearray) -> None:
    for i in range(len(buffer)):
        buffer[i] = 0
