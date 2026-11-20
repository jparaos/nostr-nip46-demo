"""Pruebas offline de la lógica NIP-46 (sin relays). Ejecutar: python -m unittest -v"""

import asyncio
import json
import unittest

from signer import crypto
from signer.nip46 import Nip46Signer, bunker_url


async def yes(unsigned, client):
    return True


async def no(unsigned, client):
    return False


def run(coro):
    return asyncio.run(coro)


class Nip46SignerTest(unittest.TestCase):
    def setUp(self):
        self.keys, _ = crypto.load_keys(None)
        self.signer = Nip46Signer(self.keys, "s3cr3t", yes)
        self.pk = self.signer.pubkey

    def connect(self, client="c1", secret="s3cr3t"):
        return run(self.signer.handle(client, {"id": "1", "method": "connect", "params": [self.pk, secret]}))

    def test_bunker_url(self):
        url = bunker_url(self.pk, ["wss://a.example", "wss://b.example"], "abc")
        self.assertEqual(url, f"bunker://{self.pk}?relay=wss%3A%2F%2Fa.example&relay=wss%3A%2F%2Fb.example&secret=abc")

    def test_connect_ok_and_secret_single_use(self):
        self.assertEqual(self.connect()["result"], "ack")
        self.assertIn("error", self.connect(client="c2"))  # mismo secret, otro cliente
        self.assertEqual(self.connect()["result"], "ack")  # cliente ya autorizado

    def test_connect_wrong_secret(self):
        self.assertIn("error", self.connect(secret="otro"))

    def test_unauthorized_client_cannot_sign(self):
        r = run(self.signer.handle("c9", {"id": "2", "method": "get_public_key", "params": []}))
        self.assertIn("unauthorized", r["error"])

    def test_get_public_key_and_sign(self):
        self.connect()
        r = run(self.signer.handle("c1", {"id": "3", "method": "get_public_key", "params": []}))
        self.assertEqual(r["result"], self.pk)
        unsigned = {"kind": 22242, "content": "", "tags": [["challenge", "xyz"]], "created_at": 1700000000}
        r = run(self.signer.handle("c1", {"id": "4", "method": "sign_event", "params": [json.dumps(unsigned)]}))
        signed = json.loads(r["result"])
        self.assertEqual(signed["pubkey"], self.pk)
        self.assertTrue(crypto.verify_event(signed))

    def test_rejected_signature(self):
        signer = Nip46Signer(self.keys, "s3cr3t", no)
        run(signer.handle("c1", {"id": "1", "method": "connect", "params": [self.pk, "s3cr3t"]}))
        r = run(signer.handle("c1", {"id": "5", "method": "sign_event", "params": ['{"kind":1,"content":"","tags":[]}']}))
        self.assertIn("rejected", r["error"])

    def test_switch_relays_keeps_current(self):
        self.connect()
        r = run(self.signer.handle("c1", {"id": "7", "method": "switch_relays", "params": []}))
        self.assertEqual(r["result"], "null")

    def test_unknown_method(self):
        r = run(self.signer.handle("c1", {"id": "6", "method": "nip44_encrypt", "params": []}))
        self.assertIn("not supported", r["error"])

    def test_encryption_roundtrip_both_schemes(self):
        other, _ = crypto.load_keys(None)
        for scheme in (crypto.NIP44, crypto.NIP04):
            ct = crypto.encrypt(other, self.pk, "hola", scheme)
            self.assertEqual(crypto.decrypt(self.keys, crypto.pubkey_hex(other), ct), ("hola", scheme))


if __name__ == "__main__":
    unittest.main()
