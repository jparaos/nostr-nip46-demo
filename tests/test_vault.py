"""Pruebas de la bóveda cifrada. Ejecutar: python -m unittest -v"""

import base64
import json
import os
import stat
import tempfile
import traceback
import unittest
from pathlib import Path

import nostr_sdk as sdk

from signer import crypto, vault as v
from signer.approvals import ApprovalManager
from signer.nip46 import Nip46Signer

PASSWORD = "correcta-horse-battery"
FAST_N = 2**14  # costo mínimo aceptado, para que las pruebas sean rápidas


class VaultTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "sub" / "vault.json"
        self.vault = v.Vault(self.path, scrypt_n=FAST_N)
        self.known = sdk.Keys.generate()
        self.nsec = self.known.secret_key().to_bech32()
        self.hex = self.known.secret_key().to_hex()

    def tearDown(self):
        self.tmp.cleanup()

    def reopen(self):
        """Simula un reinicio del proceso: nueva instancia, mismo archivo."""
        return v.Vault(self.path, scrypt_n=FAST_N)

    # ---- generación e importación -------------------------------------------

    def test_create_generates_key_and_returns_backup_once(self):
        created = self.vault.create_vault(PASSWORD)
        self.assertTrue(created.nsec_backup.startswith("nsec1"))
        self.assertTrue(created.npub.startswith("npub1"))
        self.assertEqual(self.vault.get_public_key(), created.pubkey)
        self.assertEqual(self.vault.get_signing_keys().secret_key().to_bech32(), created.nsec_backup)
        self.assertNotIn(created.nsec_backup, repr(created))  # no se filtra al imprimir/loguear

    def test_import_nsec(self):
        pubkey = self.vault.import_key(self.nsec, PASSWORD)
        self.assertEqual(pubkey, self.known.public_key().to_hex())

    def test_import_hex_and_whitespace(self):
        pubkey = self.vault.import_key(f"  {self.hex.upper()}\n", PASSWORD)
        self.assertEqual(pubkey, self.known.public_key().to_hex())

    def test_reject_invalid_keys_without_echo(self):
        bad_values = ["", "hola", "npub1" + "q" * 58, self.nsec[:-1] + ("q" if self.nsec[-1] != "q" else "p"),
                      "zz" * 32, "0" * 64, self.hex[:-2]]
        for bad in bad_values:
            with self.subTest(bad=bad[:10]):
                with self.assertRaises(v.InvalidKeyError) as ctx:
                    self.vault.import_key(bad, PASSWORD)
                printed = "".join(traceback.format_exception(ctx.exception))
                if bad:
                    self.assertNotIn(bad, printed)  # ni en el mensaje ni en el traceback impreso
                if ctx.exception.__context__ is not None:
                    self.assertTrue(ctx.exception.__suppress_context__)  # error de nostr-sdk oculto
        self.assertFalse(self.path.exists())

    def test_weak_password_rejected(self):
        with self.assertRaises(v.WeakPasswordError):
            self.vault.create_vault("corta")
        self.assertFalse(self.path.exists())

    # ---- cifrado y persistencia ---------------------------------------------

    def test_file_contains_no_secret(self):
        self.vault.import_key(self.nsec, PASSWORD)
        text = self.path.read_text(encoding="utf-8")
        for secret in (self.nsec, self.hex, self.hex.upper(), PASSWORD):
            self.assertNotIn(secret, text)
        raw = self.path.read_bytes()
        self.assertNotIn(bytes.fromhex(self.hex), raw)
        doc = json.loads(text)
        self.assertEqual(doc["cipher"]["name"], "aes-256-gcm")
        self.assertEqual(doc["kdf"]["name"], "scrypt")
        self.assertEqual(len(base64.b64decode(doc["ciphertext"])), 32 + 16)  # clave + tag GCM
        self.assertEqual(list(self.path.parent.glob(".vault-*.tmp")), [])  # sin temporales

    def test_random_salt_and_nonce_per_vault(self):
        a = v.seal(self.known, PASSWORD, FAST_N)
        b = v.seal(self.known, PASSWORD, FAST_N)
        self.assertNotEqual(a["kdf"]["salt"], b["kdf"]["salt"])
        self.assertNotEqual(a["cipher"]["nonce"], b["cipher"]["nonce"])
        self.assertNotEqual(a["ciphertext"], b["ciphertext"])

    def test_encrypt_decrypt_roundtrip(self):
        doc = v.seal(self.known, PASSWORD, FAST_N)
        self.assertEqual(v.unseal(doc, PASSWORD).secret_key().to_hex(), self.hex)

    def test_restart_and_recover(self):
        created = self.vault.create_vault(PASSWORD)
        self.vault.lock_vault()
        again = self.reopen()
        self.assertFalse(again.is_unlocked)
        self.assertEqual(again.get_public_key(), created.pubkey)  # visible sin desbloquear
        again.unlock_vault(PASSWORD)
        self.assertEqual(again.get_signing_keys().secret_key().to_bech32(), created.nsec_backup)

    def test_no_accidental_overwrite(self):
        self.vault.import_key(self.nsec, PASSWORD)
        before = self.path.read_bytes()
        with self.assertRaises(v.VaultExistsError):
            self.reopen().create_vault(PASSWORD)
        with self.assertRaises(v.VaultExistsError):
            self.reopen().import_key(self.hex, PASSWORD)
        self.assertEqual(self.path.read_bytes(), before)

    def test_explicit_overwrite(self):
        self.vault.create_vault(PASSWORD)
        pubkey = self.reopen().import_key(self.nsec, PASSWORD, overwrite=True)
        self.assertEqual(self.reopen().get_public_key(), pubkey)

    @unittest.skipUnless(os.name == "posix", "permisos POSIX")
    def test_restrictive_permissions(self):
        self.vault.create_vault(PASSWORD)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.path.parent.stat().st_mode), 0o700)

    # ---- errores ----------------------------------------------------------------

    def test_wrong_password(self):
        self.vault.import_key(self.nsec, PASSWORD)
        again = self.reopen()
        with self.assertRaises(v.WrongPasswordError) as ctx:
            again.unlock_vault("incorrecta-123")
        self.assertFalse(again.is_unlocked)
        self.assertNotIn("incorrecta-123", "".join(traceback.format_exception(ctx.exception)))

    def test_corrupt_files(self):
        self.vault.import_key(self.nsec, PASSWORD)
        good = json.loads(self.path.read_text(encoding="utf-8"))

        def tampered(fn):
            doc = json.loads(json.dumps(good))
            fn(doc)
            return json.dumps(doc)

        cases = {
            "json roto": "{no es json",
            "no es objeto": "[]",
            "sin ciphertext": tampered(lambda d: d.pop("ciphertext")),
            "base64 inválido": tampered(lambda d: d.update(ciphertext="***")),
            "nonce corto": tampered(lambda d: d["cipher"].update(nonce=base64.b64encode(b"x").decode())),
            "versión desconocida": tampered(lambda d: d.update(version=99)),
            "costo scrypt degradado": tampered(lambda d: d["kdf"].update(n=2)),
            "costo scrypt enorme": tampered(lambda d: d["kdf"].update(n=2**30)),
        }
        for name, content in cases.items():
            with self.subTest(name):
                self.path.write_text(content, encoding="utf-8")
                with self.assertRaises(v.CorruptVaultError):
                    self.reopen().unlock_vault(PASSWORD)

    def test_tampered_header_or_ciphertext_fails_authentication(self):
        self.vault.import_key(self.nsec, PASSWORD)
        good = json.loads(self.path.read_text(encoding="utf-8"))
        other_pub = sdk.Keys.generate().public_key().to_hex()
        ct = bytearray(base64.b64decode(good["ciphertext"]))
        ct[0] ^= 1
        for name, change in {"pubkey cambiada": {"pubkey": other_pub},
                             "ciphertext alterado": {"ciphertext": base64.b64encode(bytes(ct)).decode()}}.items():
            with self.subTest(name):
                self.path.write_text(json.dumps({**good, **change}), encoding="utf-8")
                with self.assertRaises(v.WrongPasswordError):  # GCM detecta la alteración (AAD/tag)
                    self.reopen().unlock_vault(PASSWORD)

    def test_missing_vault(self):
        with self.assertRaises(v.VaultNotFoundError):
            self.vault.unlock_vault(PASSWORD)

    # ---- bloqueo e integración --------------------------------------------------

    def test_lock(self):
        self.vault.import_key(self.nsec, PASSWORD)
        self.assertTrue(self.vault.is_unlocked)
        self.vault.lock_vault()
        self.assertFalse(self.vault.is_unlocked)
        with self.assertRaises(v.VaultLockedError):
            self.vault.get_signing_keys()
        self.assertEqual(self.vault.get_public_key(), self.known.public_key().to_hex())
        self.assertNotIn(self.nsec, repr(self.vault))

    def test_signer_uses_vault_keys(self):
        self.vault.import_key(self.nsec, PASSWORD)
        self.vault.lock_vault()
        again = self.reopen()
        again.unlock_vault(PASSWORD)
        signer = Nip46Signer(again.get_signing_keys(), "s", ApprovalManager())
        signed = crypto.sign_event(signer.keys, {"kind": 27235, "content": "", "tags": [], "created_at": 1})
        self.assertEqual(signed["pubkey"], self.known.public_key().to_hex())
        self.assertTrue(crypto.verify_event(signed))

    def test_default_cost_parameters(self):
        doc = v.seal(self.known, PASSWORD)  # parámetros de producción (n = 2**17)
        self.assertEqual(doc["kdf"]["n"], 2**17)
        self.assertEqual(v.unseal(doc, PASSWORD).secret_key().to_hex(), self.hex)


if __name__ == "__main__":
    unittest.main()
