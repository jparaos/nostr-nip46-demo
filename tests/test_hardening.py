"""Regresiones de la revisión final de seguridad."""

import asyncio
import json
import logging
import time
import unittest

from signer import crypto
from signer.approvals import ApprovalManager, Status
from signer.nip46 import Nip46Signer, validate_unsigned
from signer.relay import SEEN_LIMIT, RelayPool
from signer.service import MAX_EVENT_AGE, NIP46_KIND, SignerService
from tests.test_service import AUTH_EVENT, FakePool


class ValidateUnsignedTest(unittest.TestCase):
    def setUp(self):
        self.pk = "ab" * 32

    def test_stacker_news_event_is_valid(self):
        validate_unsigned(dict(AUTH_EVENT), self.pk)
        validate_unsigned({**AUTH_EVENT, "pubkey": self.pk, "id": "x"}, self.pk)  # SN envía pubkey e id

    def test_malformed_events_rejected(self):
        bad = {
            "no es objeto": ["kind", 1],
            "kind texto": {**AUTH_EVENT, "kind": "27235"},
            "kind booleano": {**AUTH_EVENT, "kind": True},
            "kind fuera de rango": {**AUTH_EVENT, "kind": 70000},
            "content no texto": {**AUTH_EVENT, "content": {"a": 1}},
            "tags no lista": {**AUTH_EVENT, "tags": "challenge"},
            "tag con número": {**AUTH_EVENT, "tags": [["challenge", 5]]},
            "tag vacío": {**AUTH_EVENT, "tags": [[]]},
            "created_at negativo": {**AUTH_EVENT, "created_at": -1},
            "pubkey ajena": {**AUTH_EVENT, "pubkey": "cd" * 32},
            "demasiado grande": {**AUTH_EVENT, "content": "x" * 70000},
        }
        for name, event in bad.items():
            with self.subTest(name):
                with self.assertRaises(ValueError):
                    validate_unsigned(event, self.pk)

    def test_invalid_event_never_reaches_approval(self):
        asked = []

        async def approve(unsigned, client):
            asked.append(unsigned)
            return True

        keys, _ = crypto.load_keys(None)
        signer = Nip46Signer(keys, "s", approve)
        pk = signer.pubkey

        async def scenario():
            await signer.handle("c", {"id": "1", "method": "connect", "params": [pk, "s"]})
            return await signer.handle("c", {"id": "2", "method": "sign_event",
                                             "params": [json.dumps({**AUTH_EVENT, "tags": "x"})]})

        response = asyncio.run(scenario())
        self.assertIn("invalid event", response["error"])
        self.assertEqual(asked, [])


class ApprovalLimitsTest(unittest.IsolatedAsyncioTestCase):
    async def test_pending_queue_is_bounded(self):
        m = ApprovalManager(timeout=5, max_pending=2)
        tasks = [asyncio.create_task(m.submit_request("c", {**AUTH_EVENT, "content": str(i)})) for i in range(3)]
        await asyncio.sleep(0.05)
        self.assertEqual(len(m.get_pending_requests()), 2)
        self.assertTrue(tasks[2].done() and tasks[2].result() is False)
        self.assertEqual(m.get_history()[0].status, Status.REJECTED)
        m.cancel_all()
        await asyncio.gather(*tasks)


class RelayDedupTest(unittest.TestCase):
    def test_seen_ids_are_bounded_and_duplicates_dropped(self):
        pool = RelayPool(["wss://x"], {})
        pool._on_message("wss://x", json.dumps(["EVENT", "s", {"id": "dup"}]))
        pool._on_message("wss://y", json.dumps(["EVENT", "s", {"id": "dup"}]))  # mismo evento, otro relay
        self.assertEqual(pool.events.qsize(), 1)
        for i in range(SEEN_LIMIT + 50):
            pool._on_message("wss://x", json.dumps(["EVENT", "s", {"id": f"e{i}"}]))
        self.assertEqual(len(pool._seen), SEEN_LIMIT)
        pool._on_message("wss://x", json.dumps(["EVENT", "s", "no-es-objeto"]))  # no rompe


class ServiceHardeningTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.keys, _ = crypto.load_keys(None)
        self.client, _ = crypto.load_keys(None)
        self.pool = FakePool()
        self.service = SignerService(self.keys, ApprovalManager(timeout=5), relays=["wss://x"],
                                     pool=self.pool, secret="s3cr3t")
        self.pk = self.service.pubkey

    def envelope(self, body, scheme=crypto.NIP04, created_at=None, p=None):
        content = crypto.encrypt(self.client, self.pk, json.dumps(body), scheme)
        return crypto.sign_event(self.client, {"kind": NIP46_KIND, "content": content, "tags": [["p", p or self.pk]],
                                               "created_at": created_at or int(time.time())})

    def scheme_of(self, event):
        return crypto.detect_scheme(event["content"])

    async def test_response_uses_same_scheme_as_request(self):
        for scheme in (crypto.NIP04, crypto.NIP44):
            with self.subTest(scheme):
                self.pool.published.clear()
                await self.service.process(self.envelope({"id": "p", "method": "ping", "params": []}, scheme))
                self.assertEqual(self.scheme_of(self.pool.published[0]), scheme)

    async def test_stale_or_future_events_are_dropped(self):
        for created_at in (int(time.time()) - MAX_EVENT_AGE - 60, int(time.time()) + MAX_EVENT_AGE + 60):
            event = self.envelope({"id": "1", "method": "connect", "params": ["", "s3cr3t"]}, created_at=created_at)
            self.assertIsNone(await self.service.process(event))
        self.assertEqual(self.pool.published, [])
        self.assertEqual(self.service.signer.authorized, set())  # el secret sigue sin usarse

    async def test_event_not_addressed_to_signer_is_dropped(self):
        event = self.envelope({"id": "1", "method": "ping", "params": []}, p="ef" * 32)
        self.assertIsNone(await self.service.process(event))

    async def test_tampered_event_is_dropped(self):
        event = self.envelope({"id": "1", "method": "connect", "params": ["", "s3cr3t"]})
        event["content"] = self.envelope({"id": "1", "method": "ping", "params": []})["content"]
        self.assertIsNone(await self.service.process(event))
        self.assertEqual(self.service.signer.authorized, set())

    async def test_dropped_events_are_counted_and_audited(self):
        import tempfile
        from pathlib import Path

        from signer.audit import AuditLog

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit.jsonl"
            self.service.audit = AuditLog(path)
            old = int(time.time()) - MAX_EVENT_AGE - 60
            await self.service.process(self.envelope({"id": "1", "method": "ping", "params": []}, created_at=old))
            await self.service.process(self.envelope({"id": "2", "method": "ping", "params": []}, p="ef" * 32))
            await self.service.process(self.envelope({"id": "3", "method": "ping", "params": []}))
            activity = self.service.activity
            self.assertEqual((activity["received"], activity["answered"]), (3, 1))
            self.assertEqual(activity["dropped"], {"fuera de la ventana de tiempo": 1, "no dirigido a este firmante": 1})
            drops = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                     if json.loads(line)["dir"] == "drop"]
            self.assertEqual(len(drops), 2)
            self.assertNotIn("content", json.dumps(drops))  # sin contenido cifrado ni descifrado

    async def test_reply_without_connected_relay_is_reported(self):
        async def publish_nowhere(event):
            return 0

        self.pool.publish = publish_nowhere
        await self.service.process(self.envelope({"id": "1", "method": "ping", "params": []}))
        self.assertEqual((self.service.activity["answered"], self.service.activity["unsent_replies"]), (0, 1))

    def test_default_relays_exclude_unreachable_nsec_app(self):
        from signer.service import DEFAULT_RELAYS

        self.assertNotIn("wss://relay.nsec.app", DEFAULT_RELAYS)
        self.assertIn("relay=wss%3A%2F%2Fnos.lol", SignerService(self.keys, ApprovalManager()).bunker_url)

    async def test_logs_never_contain_bunker_secret(self):
        with self.assertLogs("service", level="INFO") as captured:
            await self.service.process(self.envelope({"id": "1", "method": "connect", "params": ["", "s3cr3t"]}))
        self.assertNotIn("s3cr3t", "\n".join(captured.output))


if __name__ == "__main__":
    unittest.main()
