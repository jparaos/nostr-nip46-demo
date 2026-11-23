"""Pruebas de la aplicación web (endpoints + integración con relays simulados)."""

import json
import re
import tempfile
import time
import unittest
from pathlib import Path

import nostr_sdk as sdk
from fastapi.testclient import TestClient

from app.main import create_app
from signer import crypto
from signer.audit import AuditLog
from signer.service import NIP46_KIND
from tests.test_service import AUTH_EVENT, FakePool

PORT = 8046
BASE = f"http://127.0.0.1:{PORT}"
ORIGIN = {"Origin": BASE}
PASSWORD = "contrasena-segura-1"


class WebTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault_path = Path(self.tmp.name) / "vault.json"
        self.pools: list[FakePool] = []

        def pool_factory(relays, filters):
            pool = FakePool()
            pool.connected = relays[:1]  # simula 1 relay conectado
            self.pools.append(pool)
            return pool

        self.app = create_app(vault_path=self.vault_path, port=PORT, relays=["wss://a.test", "wss://b.test"],
                              approval_timeout=5, scrypt_n=2**14, pool_factory=pool_factory, audit=AuditLog(None))
        self.client = TestClient(self.app, base_url=BASE)
        self.client.__enter__()  # ejecuta el lifespan
        self.responses = []  # todas las respuestas, para buscar secretos filtrados

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.tmp.cleanup()

    # ---- helpers -----------------------------------------------------------------

    def csrf(self, page="/setup") -> str:
        html = self.client.get(page).text
        return re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)

    def post(self, path, body=None, token=None, headers=None):
        hdrs = {**ORIGIN, "X-CSRF-Token": token if token is not None else self._token}
        hdrs.update(headers or {})
        response = self.client.post(path, json=body or {}, headers=hdrs)
        self.responses.append(response)
        return response

    def get(self, path):
        response = self.client.get(path)
        self.responses.append(response)
        return response

    def create_vault(self):
        self._token = self.csrf("/setup")
        r = self.post("/api/vault/create", {"password": PASSWORD, "confirm": PASSWORD})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    def import_vault(self, secret):
        self._token = self.csrf("/setup")
        return self.post("/api/vault/import", {"secret": secret, "password": PASSWORD, "confirm": PASSWORD})

    def all_response_text(self) -> str:
        return "\n".join(r.text + json.dumps(dict(r.headers)) for r in self.responses)

    # ---- integración NIP-46 -----------------------------------------------------

    def nip46(self, client_keys, signer_pk, req_id, method, params):
        body = json.dumps({"id": req_id, "method": method, "params": params})
        content = crypto.encrypt(client_keys, signer_pk, body, crypto.NIP04)  # como Stacker News
        event = crypto.build_event(client_keys, NIP46_KIND, content, [["p", signer_pk]])
        self.client.portal.call(self.pools[-1].events.put, event)

    def wait_response(self, client_keys, signer_pk, req_id, timeout=3.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            for ev in list(self.pools[-1].published):
                body = json.loads(crypto.decrypt(client_keys, signer_pk, ev["content"])[0])
                if body["id"] == req_id:
                    return body
            time.sleep(0.02)
        raise AssertionError(f"sin respuesta NIP-46 para {req_id}")

    def wait_pending(self, n=1, timeout=3.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            pending = self.get("/api/requests").json()["pending"]
            if len(pending) >= n:
                return pending
            time.sleep(0.02)
        raise AssertionError("no apareció la solicitud pendiente")

    def connected_client(self, signer_pk):
        client = sdk.Keys.generate()
        secret = self.get("/api/status").json()["signer"]["bunker_url"].split("secret=")[1]
        self.nip46(client, signer_pk, "c1", "connect", ["", secret])
        self.assertEqual(self.wait_response(client, signer_pk, "c1")["result"], "ack")
        return client


class VaultFlowTest(WebTestCase):
    def test_home_redirects_to_setup_without_vault(self):
        r = self.client.get("/", follow_redirects=False)
        self.assertEqual((r.status_code, r.headers["location"]), (303, "/setup"))

    def test_create_vault_starts_signer_and_shows_nsec_once(self):
        created = self.create_vault()
        self.assertTrue(created["nsec_backup"].startswith("nsec1"))
        self.assertTrue(created["npub"].startswith("npub1"))
        self.assertIn("signer_session", self.client.cookies)
        self.assertNotIn(created["nsec_backup"], self.vault_path.read_text())

        status = self.get("/api/status").json()
        self.assertTrue(status["signer"]["running"])
        self.assertEqual(status["pubkey"], created["pubkey"])
        url = status["signer"]["bunker_url"]
        self.assertRegex(url, rf"^bunker://{created['pubkey']}\?relay=wss%3A%2F%2Fa\.test&relay=wss%3A%2F%2Fb\.test&secret=[0-9a-f]{{32}}$")
        self.assertEqual(status["signer"]["relays"], [{"url": "wss://a.test", "connected": True, "events": 0},
                                                     {"url": "wss://b.test", "connected": False, "events": 0}])
        self.assertEqual(status["signer"]["activity"]["received"], 0)
        # el nsec solo apareció en la respuesta de creación
        later = "\n".join(r.text for r in self.responses[1:])
        self.assertNotIn(created["nsec_backup"], later)
        self.assertNotIn(created["nsec_backup"], self.client.get("/dashboard").text)
        self.assertEqual(self.responses[0].headers["cache-control"], "no-store")

    def test_setup_unavailable_once_vault_exists(self):
        self.create_vault()
        self.assertEqual(self.client.get("/setup", follow_redirects=False).headers["location"], "/")
        r = self.post("/api/vault/create", {"password": PASSWORD, "confirm": PASSWORD})
        self.assertEqual(r.status_code, 409)

    def test_password_mismatch_and_weak(self):
        self._token = self.csrf()
        r = self.post("/api/vault/create", {"password": PASSWORD, "confirm": PASSWORD + "x"})
        self.assertEqual(r.status_code, 400)
        r = self.post("/api/vault/create", {"password": "corta", "confirm": "corta"})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(self.vault_path.exists())

    def test_import_nsec_and_hex(self):
        keys = sdk.Keys.generate()
        r = self.import_vault(keys.secret_key().to_bech32())
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()["pubkey"], keys.public_key().to_hex())
        self.assertNotIn("nsec_backup", r.json())  # una clave importada nunca se devuelve

        self.tearDown(); self.setUp()  # bóveda nueva para probar hex
        keys2 = sdk.Keys.generate()
        r = self.import_vault(keys2.secret_key().to_hex())
        self.assertEqual(r.json()["pubkey"], keys2.public_key().to_hex())

    def test_import_invalid_key_is_rejected_without_echo(self):
        r = self.import_vault("nsec1esto-no-es-una-clave")
        self.assertEqual(r.status_code, 400)
        self.assertNotIn("esto-no-es", r.text)
        self.assertFalse(self.vault_path.exists())

    def test_lock_and_unlock(self):
        created = self.create_vault()
        self.assertEqual(self.post("/api/vault/lock").status_code, 200)
        self.assertEqual(self.get("/api/status").status_code, 401)  # sesión revocada
        self.assertFalse(self.get("/api/vault/state").json()["unlocked"])
        self.assertEqual(self.client.get("/", follow_redirects=False).headers["location"], "/unlock")

        self._token = self.csrf("/unlock")
        r = self.post("/api/vault/unlock", {"password": "incorrecta-999"})
        self.assertEqual((r.status_code, r.json()["detail"]), (401, "contraseña incorrecta"))
        self.assertFalse(self.get("/api/vault/state").json()["unlocked"])  # sigue bloqueada

        r = self.post("/api/vault/unlock", {"password": PASSWORD})
        self.assertEqual(r.status_code, 200)
        status = self.get("/api/status").json()
        self.assertTrue(status["signer"]["running"])
        self.assertEqual(status["pubkey"], created["pubkey"])

    def test_lock_stops_signer_and_cancels_pending(self):
        created = self.create_vault()
        client = self.connected_client(created["pubkey"])
        self.nip46(client, created["pubkey"], "s1", "sign_event", [json.dumps(AUTH_EVENT)])
        self.wait_pending()
        pool = self.pools[-1]
        self.post("/api/vault/lock")
        self.assertTrue(pool.stopped)
        signed = [e for e in pool.published if '"sig"' in crypto.decrypt(client, created["pubkey"], e["content"])[0]]
        self.assertEqual(signed, [])


class ApprovalFlowTest(WebTestCase):
    def setUp(self):
        super().setUp()
        self.created = self.create_vault()
        self.pk = self.created["pubkey"]
        self.client_keys = self.connected_client(self.pk)

    def test_authorized_client_listed_abbreviated(self):
        clients = self.get("/api/status").json()["signer"]["clients"]
        full = self.client_keys.public_key().to_hex()
        self.assertEqual(clients, [f"{full[:8]}…{full[-4:]}"])
        self.assertTrue(self.get("/api/status").json()["signer"]["secret_used"])

    def test_pending_request_is_visible_with_all_fields(self):
        self.nip46(self.client_keys, self.pk, "s1", "sign_event", [json.dumps(AUTH_EVENT)])
        [req] = self.wait_pending()
        self.assertEqual(req["method"], "sign_event")
        self.assertEqual(req["kind"], 27235)
        self.assertEqual(req["content"], AUTH_EVENT["content"])
        self.assertEqual(req["tags"], AUTH_EVENT["tags"])
        self.assertEqual(req["created_at"], AUTH_EVENT["created_at"])
        self.assertEqual(req["client"], self.client_keys.public_key().to_hex())
        self.assertEqual(self.get("/api/status").json()["pending"], 1)

    def test_approve_signs_and_responds_to_remote_client(self):
        self.nip46(self.client_keys, self.pk, "s1", "sign_event", [json.dumps(AUTH_EVENT)])
        [req] = self.wait_pending()
        r = self.post(f"/api/requests/{req['id']}/approve")
        self.assertEqual((r.status_code, r.json()["status"]), (200, "approved"))
        signed = json.loads(self.wait_response(self.client_keys, self.pk, "s1")["result"])
        self.assertTrue(crypto.verify_event(signed))
        self.assertEqual(signed["pubkey"], self.pk)
        self.assertEqual(signed["tags"], AUTH_EVENT["tags"])
        self.assertEqual(self.post(f"/api/requests/{req['id']}/approve").status_code, 409)  # ya resuelta

    def test_reject_returns_error_without_signing(self):
        self.nip46(self.client_keys, self.pk, "s1", "sign_event", [json.dumps(AUTH_EVENT)])
        [req] = self.wait_pending()
        r = self.post(f"/api/requests/{req['id']}/reject", {"reason": "no fui yo"})
        self.assertEqual(r.json()["status"], "rejected")
        resp = self.wait_response(self.client_keys, self.pk, "s1")
        self.assertIn("rejected", resp["error"])
        self.assertEqual(resp["result"], "")
        history = self.get("/api/requests").json()["history"]
        self.assertEqual((history[0]["status"], history[0]["reason"]), ("rejected", "no fui yo"))

    def test_unknown_request_404(self):
        self.assertEqual(self.post("/api/requests/no-existe/approve").status_code, 404)

    def test_restart_generates_new_url_and_forgets_clients(self):
        old = self.get("/api/status").json()["signer"]["bunker_url"]
        r = self.post("/api/signer/restart")
        self.assertNotEqual(r.json()["bunker_url"], old)
        self.assertEqual(r.json()["clients"], [])


class SecurityTest(WebTestCase):
    def setUp(self):
        super().setUp()
        self.created = self.create_vault()

    def test_endpoints_require_session(self):
        anonymous = TestClient(self.app, base_url=BASE)  # sin cookies
        token = re.search(r'name="csrf-token" content="([^"]+)"', anonymous.get("/unlock").text).group(1)
        hdrs = {**ORIGIN, "X-CSRF-Token": token}
        self.assertEqual(anonymous.get("/api/status").status_code, 401)
        self.assertEqual(anonymous.get("/api/requests").status_code, 401)
        self.assertEqual(anonymous.post("/api/requests/x/approve", headers=hdrs).status_code, 401)
        self.assertEqual(anonymous.post("/api/vault/lock", headers=hdrs).status_code, 401)
        self.assertEqual(anonymous.post("/api/signer/restart", headers=hdrs).status_code, 401)
        self.assertEqual(anonymous.get("/dashboard", follow_redirects=False).status_code, 303)
        self.assertTrue(self.get("/api/vault/state").json()["unlocked"])  # nada cambió

    def test_csrf_required_on_mutations(self):
        self.assertEqual(self.post("/api/vault/lock", token="").status_code, 403)
        self.assertEqual(self.post("/api/vault/lock", token="token-falso").status_code, 403)
        self.assertTrue(self.get("/api/vault/state").json()["unlocked"])

    def test_foreign_or_missing_origin_rejected(self):
        for origin in ("https://evil.example", "http://127.0.0.1:9999", "null"):
            r = self.post("/api/vault/lock", headers={"Origin": origin})
            self.assertEqual(r.status_code, 403, origin)
        r = self.client.post("/api/vault/lock", headers={"X-CSRF-Token": self._token})  # sin Origin
        self.assertEqual(r.status_code, 403)
        self.assertTrue(self.get("/api/vault/state").json()["unlocked"])

    def test_foreign_host_rejected_dns_rebinding(self):
        for host in ("evil.example", "evil.example:8046", "127.0.0.1:9999", "0.0.0.0:8046"):
            r = self.client.get("/api/vault/state", headers={"Host": host})
            self.assertEqual(r.status_code, 403, host)

    def test_no_cors_headers(self):
        r = self.client.options("/api/vault/lock", headers={"Origin": "https://evil.example",
                                                            "Access-Control-Request-Method": "POST"})
        self.assertNotIn("access-control-allow-origin", {k.lower() for k in r.headers})

    def test_security_headers_and_cookie_flags(self):
        r = self.client.get("/dashboard")
        self.assertIn("script-src 'self'", r.headers["content-security-policy"])
        self.assertEqual(r.headers["x-frame-options"], "DENY")
        self._token = self.csrf("/unlock")
        r = self.post("/api/vault/unlock", {"password": PASSWORD})
        cookie = r.headers["set-cookie"].lower()
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=strict", cookie)

    def test_secrets_never_leak(self):
        nsec = self.created["nsec_backup"]
        hex_secret = sdk.Keys.parse(nsec).secret_key().to_hex()
        self._token = self.csrf("/unlock")
        self.post("/api/vault/unlock", {"password": "incorrecta-999"})
        self.post("/api/vault/unlock", {"password": PASSWORD})
        for path in ("/api/status", "/api/requests", "/api/vault/state", "/dashboard", "/requests", "/unlock"):
            self.get(path)
        leaked = "\n".join(r.text for r in self.responses[1:])  # todo salvo la respuesta de creación
        for secret in (nsec, hex_secret, PASSWORD, "incorrecta-999"):
            self.assertNotIn(secret, leaked)
        self.assertNotIn(PASSWORD, self.responses[0].text)

    def test_no_auto_approve(self):
        client = self.connected_client(self.created["pubkey"])
        self.nip46(client, self.created["pubkey"], "s1", "sign_event", [json.dumps(AUTH_EVENT)])
        self.wait_pending()
        time.sleep(0.3)
        self.assertEqual(len(self.get("/api/requests").json()["pending"]), 1)  # sigue esperando a la persona


if __name__ == "__main__":
    unittest.main()
