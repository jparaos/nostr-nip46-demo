"""Pruebas del SignerService con un pool de relays falso (sin red)."""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from signer import crypto
from signer.approvals import ApprovalManager, Status
from signer.audit import AuditLog
from signer.service import NIP46_KIND, SignerService

AUTH_EVENT = {"kind": 27235, "content": "Stacker News Authentication",
              "tags": [["challenge", "c0ffee"], ["u", "https://stacker.news"], ["method", "GET"]],
              "created_at": 1700000000}


class FakePool:
    def __init__(self):
        self.events = asyncio.Queue()
        self.published: list[dict] = []
        self.started = self.stopped = False

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True

    async def publish(self, event):
        self.published.append(event)
        return 1


class ServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.keys, _ = crypto.load_keys(None)
        self.client, _ = crypto.load_keys(None)
        self.client_pk = crypto.pubkey_hex(self.client)
        self.pool = FakePool()
        self.manager = ApprovalManager(timeout=5)
        self.tmp = tempfile.TemporaryDirectory()
        self.audit_path = Path(self.tmp.name) / "audit.jsonl"
        self.service = SignerService(self.keys, self.manager, relays=["wss://x"], audit=AuditLog(self.audit_path),
                                     pool=self.pool, secret="s3cr3t")
        await self.service.start()

    async def asyncTearDown(self):
        await self.service.stop()
        self.tmp.cleanup()

    def request_event(self, req_id, method, params, scheme=crypto.NIP04):
        body = json.dumps({"id": req_id, "method": method, "params": params})
        content = crypto.encrypt(self.client, self.service.pubkey, body, scheme)
        return crypto.build_event(self.client, NIP46_KIND, content, [["p", self.service.pubkey]])

    async def send(self, req_id, method, params):
        self.pool.events.put_nowait(self.request_event(req_id, method, params))

    async def response(self, req_id, timeout=2.0):
        """Espera y descifra la respuesta publicada para req_id."""
        for _ in range(int(timeout / 0.01)):
            for ev in self.pool.published:
                body = json.loads(crypto.decrypt(self.client, self.service.pubkey, ev["content"])[0])
                if body["id"] == req_id:
                    self.assertTrue(crypto.verify_event(ev))
                    self.assertEqual(ev["tags"], [["p", self.client_pk]])
                    return body
            await asyncio.sleep(0.01)
        raise AssertionError(f"sin respuesta para {req_id}")

    async def pending(self):
        for _ in range(200):
            if self.manager.get_pending_requests():
                return self.manager.get_pending_requests()[0]
            await asyncio.sleep(0.01)
        raise AssertionError("no hay solicitud pendiente")

    async def connect(self):
        await self.send("1", "connect", ["", "s3cr3t"])  # Stacker News envía el target vacío
        self.assertEqual((await self.response("1"))["result"], "ack")

    async def test_full_flow_with_approval(self):
        await self.connect()
        await self.send("2", "get_public_key", [])
        self.assertEqual((await self.response("2"))["result"], self.service.pubkey)
        await self.send("3", "sign_event", [json.dumps(AUTH_EVENT)])
        req = await self.pending()
        self.assertEqual((req.kind, req.client), (27235, self.client_pk))
        self.manager.approve_request(req.id)
        signed = json.loads((await self.response("3"))["result"])
        self.assertTrue(crypto.verify_event(signed))
        self.assertEqual(signed["pubkey"], self.service.pubkey)

    async def test_rejected_request_produces_no_signature(self):
        await self.connect()
        await self.send("3", "sign_event", [json.dumps(AUTH_EVENT)])
        self.manager.reject_request((await self.pending()).id)
        resp = await self.response("3")
        self.assertIn("rejected", resp["error"])
        self.assertEqual(resp["result"], "")
        self.assertNotIn('"sig"', json.dumps(resp))

    async def test_timeout_produces_error_response(self):
        self.manager.timeout = 0.05
        await self.connect()
        await self.send("3", "sign_event", [json.dumps(AUTH_EVENT)])
        resp = await self.response("3")
        self.assertIn("error", resp)
        self.assertEqual(self.manager.get_history()[0].status, Status.EXPIRED)

    async def test_pending_approval_does_not_block_other_requests(self):
        await self.connect()
        await self.send("3", "sign_event", [json.dumps(AUTH_EVENT)])
        req = await self.pending()
        await self.send("4", "ping", [])
        self.assertEqual((await self.response("4"))["result"], "pong")  # respondido mientras 3 espera
        self.manager.approve_request(req.id)
        self.assertIn("sig", (await self.response("3"))["result"])

    async def test_switch_relays_and_unauthorized(self):
        await self.send("5", "get_public_key", [])
        self.assertIn("unauthorized", (await self.response("5"))["error"])
        await self.connect()
        await self.send("6", "switch_relays", [])
        self.assertEqual((await self.response("6"))["result"], "null")

    async def test_stop_cancels_pending_without_signing(self):
        await self.connect()
        await self.send("3", "sign_event", [json.dumps(AUTH_EVENT)])
        await self.pending()
        await self.service.stop()
        self.assertTrue(self.pool.stopped)
        self.assertFalse(self.service.running)
        self.assertEqual(self.manager.get_pending_requests(), [])
        self.assertEqual(self.manager.get_history()[0].status, Status.CANCELLED)
        signed = [ev for ev in self.pool.published
                  if '"sig"' in crypto.decrypt(self.client, self.service.pubkey, ev["content"])[0]]
        self.assertEqual(signed, [])

    async def test_invalid_event_is_ignored(self):
        ev = self.request_event("7", "ping", [])
        ev["sig"] = "00" * 64
        self.pool.events.put_nowait(ev)
        await asyncio.sleep(0.05)
        self.assertEqual(self.pool.published, [])

    async def test_audit_does_not_store_secrets_or_signed_events(self):
        await self.connect()
        await self.send("3", "sign_event", [json.dumps(AUTH_EVENT)])
        self.manager.approve_request((await self.pending()).id)
        signed = json.loads((await self.response("3"))["result"])
        text = self.audit_path.read_text(encoding="utf-8")
        for secret in ("s3cr3t", signed["sig"], "c0ffee", "Stacker News Authentication", "nsec1"):
            self.assertNotIn(secret, text)
        self.assertIn(signed["id"], text)
        records = [json.loads(line) for line in text.splitlines()]
        sign_in = next(r for r in records if r.get("method") == "sign_event" and r["dir"] == "in")
        self.assertEqual(sign_in["params"]["kind"], 27235)
        self.assertEqual(sign_in["params"]["tag_names"], ["challenge", "u", "method"])


if __name__ == "__main__":
    unittest.main()
