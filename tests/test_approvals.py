"""Pruebas del ApprovalManager. Ejecutar: python -m unittest -v"""

import asyncio
import unittest

from signer.approvals import ApprovalManager, RequestAlreadyResolved, RequestNotFound, Status

EVENT = {"kind": 27235, "content": "Stacker News Authentication",
         "tags": [["challenge", "abc"], ["u", "https://stacker.news"]], "created_at": 1700000000}


async def wait_pending(manager, n=1):
    for _ in range(100):
        if len(manager.get_pending_requests()) >= n:
            return manager.get_pending_requests()
        await asyncio.sleep(0.01)
    raise AssertionError("la solicitud no quedó pendiente")


class ApprovalManagerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.m = ApprovalManager(timeout=5)

    async def test_pending_request_exposes_event_fields(self):
        task = asyncio.create_task(self.m.submit_request("client1", EVENT))
        [req] = await wait_pending(self.m)
        self.assertEqual(req.status, Status.PENDING)
        self.assertEqual((req.client, req.kind, req.content, req.tags, req.created_at),
                         ("client1", 27235, EVENT["content"], EVENT["tags"], EVENT["created_at"]))
        self.assertEqual(len(req.id), 32)
        self.assertFalse(task.done())  # sigue esperando: no bloquea, no decide solo
        self.m.reject_request(req.id)
        await task

    async def test_approve(self):
        task = asyncio.create_task(self.m.submit_request("c", EVENT))
        [req] = await wait_pending(self.m)
        resolved = self.m.approve_request(req.id)
        self.assertTrue(await task)
        self.assertEqual(resolved.status, Status.APPROVED)
        self.assertEqual(self.m.get_pending_requests(), [])
        self.assertEqual(self.m.get_history()[0].id, req.id)

    async def test_reject(self):
        task = asyncio.create_task(self.m.submit_request("c", EVENT))
        [req] = await wait_pending(self.m)
        self.m.reject_request(req.id, "no reconozco este sitio")
        self.assertFalse(await task)
        self.assertEqual(self.m.get_history()[0].status, Status.REJECTED)
        self.assertEqual(self.m.get_history()[0].reason, "no reconozco este sitio")

    async def test_timeout_rejects(self):
        m = ApprovalManager(timeout=0.05)
        self.assertFalse(await m.submit_request("c", EVENT))
        self.assertEqual(m.get_history()[0].status, Status.EXPIRED)
        self.assertEqual(m.get_pending_requests(), [])

    async def test_resolve_after_timeout_is_invalid(self):
        m = ApprovalManager(timeout=0.05)
        await m.submit_request("c", EVENT)
        with self.assertRaises(RequestAlreadyResolved):
            m.approve_request(m.get_history()[0].id)

    async def test_concurrent_requests_are_independent(self):
        events = [dict(EVENT, content=f"evento {i}") for i in range(3)]
        tasks = [asyncio.create_task(self.m.submit_request(f"c{i}", ev)) for i, ev in enumerate(events)]
        pending = await wait_pending(self.m, 3)
        self.assertEqual(len({r.id for r in pending}), 3)
        by_content = {r.content: r.id for r in pending}
        self.m.approve_request(by_content["evento 1"])
        self.m.reject_request(by_content["evento 0"])
        self.m.approve_request(by_content["evento 2"])
        self.assertEqual(await asyncio.gather(*tasks), [False, True, True])

    async def test_duplicate_submission_shares_decision(self):
        t1 = asyncio.create_task(self.m.submit_request("c", EVENT))
        await wait_pending(self.m)
        t2 = asyncio.create_task(self.m.submit_request("c", dict(EVENT)))
        await asyncio.sleep(0.02)
        pending = self.m.get_pending_requests()
        self.assertEqual(len(pending), 1)  # el reenvío no crea otra solicitud
        self.m.approve_request(pending[0].id)
        self.assertEqual(await asyncio.gather(t1, t2), [True, True])

    async def test_same_event_from_other_client_is_not_duplicate(self):
        t1 = asyncio.create_task(self.m.submit_request("c1", EVENT))
        t2 = asyncio.create_task(self.m.submit_request("c2", EVENT))
        self.assertEqual(len(await wait_pending(self.m, 2)), 2)
        self.m.cancel_all()
        await asyncio.gather(t1, t2)

    async def test_unknown_request(self):
        with self.assertRaises(RequestNotFound):
            self.m.approve_request("no-existe")
        with self.assertRaises(RequestNotFound):
            self.m.reject_request("no-existe")

    async def test_double_resolution_is_invalid(self):
        task = asyncio.create_task(self.m.submit_request("c", EVENT))
        [req] = await wait_pending(self.m)
        self.m.approve_request(req.id)
        await task
        with self.assertRaises(RequestAlreadyResolved):
            self.m.reject_request(req.id)

    async def test_cancel_all_on_shutdown(self):
        tasks = [asyncio.create_task(self.m.submit_request(f"c{i}", EVENT)) for i in range(2)]
        await wait_pending(self.m, 2)
        self.assertEqual(self.m.cancel_all(), 2)
        self.assertEqual(await asyncio.gather(*tasks), [False, False])
        self.assertTrue(all(r.status == Status.CANCELLED for r in self.m.get_history()))

    async def test_cancelled_waiter_marks_request_cancelled(self):
        task = asyncio.create_task(self.m.submit_request("c", EVENT))
        [req] = await wait_pending(self.m)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.m.get_history()[0].status, Status.CANCELLED)


if __name__ == "__main__":
    unittest.main()
