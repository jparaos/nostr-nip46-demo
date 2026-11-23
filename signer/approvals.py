"""Aprobación asíncrona de solicitudes de firma.

El firmante (Nip46Signer) recibe una función `approve(unsigned, client) -> bool`.
Este módulo la implementa con una cola de solicitudes pendientes: cada solicitud
queda esperando en un asyncio.Future que se resuelve desde cualquier interfaz
(consola, API web) sin bloquear el event loop.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import asdict, dataclass, field
from enum import Enum


class Status(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class ApprovalError(Exception):
    """Error base de la gestión de aprobaciones."""


class RequestNotFound(ApprovalError):
    pass


class RequestAlreadyResolved(ApprovalError):
    pass


@dataclass
class SignRequest:
    """Solicitud de firma tal como se muestra a la persona que aprueba."""

    id: str
    client: str
    kind: int
    content: str
    tags: list
    created_at: int | None
    method: str = "sign_event"  # único método NIP-46 que requiere aprobación
    received_at: float = field(default_factory=time.time)
    status: Status = Status.PENDING
    reason: str | None = None
    resolved_at: float | None = None

    def to_dict(self) -> dict:
        data = asdict(self)
        data["status"] = self.status.value
        return data


class ApprovalHandler(ABC):
    """Contrato que cualquier mecanismo de aprobación debe cumplir."""

    @abstractmethod
    async def submit_request(self, client: str, unsigned: dict) -> bool:
        """Registra la solicitud y espera la decisión. True = aprobada."""

    @abstractmethod
    def approve_request(self, request_id: str) -> SignRequest: ...

    @abstractmethod
    def reject_request(self, request_id: str, reason: str | None = None) -> SignRequest: ...

    @abstractmethod
    def get_pending_requests(self) -> list[SignRequest]: ...

    async def __call__(self, unsigned: dict, client: str) -> bool:
        """Adaptador a la firma `ApproveFn` que espera Nip46Signer."""
        return await self.submit_request(client, unsigned)


class ApprovalManager(ApprovalHandler):
    """Implementación en memoria con tiempo máximo de espera.

    - Cada solicitud recibe un id interno (uuid4), independiente del id NIP-46.
    - Una solicitud idéntica del mismo cliente mientras la primera está pendiente
      (p. ej. el cliente reenvía) comparte la misma decisión en vez de duplicarse.
    - Al vencer el tiempo, la solicitud se rechaza automáticamente (EXPIRED).
    - cancel_all() rechaza todo lo pendiente (CANCELLED); se usa al detener el servicio.
    """

    def __init__(self, timeout: float = 120.0, history_size: int = 50, max_pending: int = 20):
        self.timeout = timeout
        self.max_pending = max_pending
        self._pending: dict[str, tuple[SignRequest, asyncio.Future[bool]]] = {}
        self._by_key: dict[tuple, str] = {}
        self._history: deque[SignRequest] = deque(maxlen=history_size)
        self._new: asyncio.Queue[SignRequest] = asyncio.Queue()

    # ---- lado del firmante -------------------------------------------------

    async def submit_request(self, client: str, unsigned: dict) -> bool:
        key = _dedupe_key(client, unsigned)
        if key in self._by_key:  # duplicado: espera la misma decisión
            _, future = self._pending[self._by_key[key]]
            return await asyncio.shield(future)

        request = SignRequest(
            id=uuid.uuid4().hex,
            client=client,
            kind=int(unsigned.get("kind", -1)),
            content=str(unsigned.get("content", "")),
            tags=list(unsigned.get("tags") or []),
            created_at=unsigned.get("created_at"),
        )
        if len(self._pending) >= self.max_pending:
            # un cliente autorizado no puede inundar la cola: se rechaza sin mostrarla
            request.status, request.reason, request.resolved_at = (
                Status.REJECTED, "demasiadas solicitudes pendientes", time.time())
            self._history.appendleft(request)
            return False
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self._pending[request.id] = (request, future)
        self._by_key[key] = request.id
        self._new.put_nowait(request)
        try:
            return await asyncio.wait_for(asyncio.shield(future), self.timeout)
        except asyncio.TimeoutError:
            if request.id in self._pending:
                self._resolve(request.id, False, Status.EXPIRED, f"sin respuesta en {self.timeout:g}s")
            return future.result() if future.done() else False
        except asyncio.CancelledError:
            if request.id in self._pending:
                self._resolve(request.id, False, Status.CANCELLED, "solicitud cancelada")
            raise

    # ---- lado de la interfaz ----------------------------------------------

    def approve_request(self, request_id: str) -> SignRequest:
        return self._resolve(request_id, True, Status.APPROVED, None)

    def reject_request(self, request_id: str, reason: str | None = None) -> SignRequest:
        return self._resolve(request_id, False, Status.REJECTED, reason or "rechazada por el usuario")

    def get_pending_requests(self) -> list[SignRequest]:
        return [req for req, _ in self._pending.values()]

    def get_history(self) -> list[SignRequest]:
        return list(self._history)

    async def next_request(self) -> SignRequest:
        """Espera la próxima solicitud nueva (para interfaces tipo consola)."""
        return await self._new.get()

    def cancel_all(self, reason: str = "servicio detenido") -> int:
        ids = list(self._pending)
        for request_id in ids:
            self._resolve(request_id, False, Status.CANCELLED, reason)
        return len(ids)

    # ---- interno -------------------------------------------------------------

    def _resolve(self, request_id: str, approved: bool, status: Status, reason: str | None) -> SignRequest:
        entry = self._pending.pop(request_id, None)
        if entry is None:
            if any(r.id == request_id for r in self._history):
                raise RequestAlreadyResolved(request_id)
            raise RequestNotFound(request_id)
        request, future = entry
        self._by_key = {k: v for k, v in self._by_key.items() if v != request_id}
        request.status, request.reason, request.resolved_at = status, reason, time.time()
        if not future.done():
            future.set_result(approved)
        self._history.appendleft(request)
        return request


def _dedupe_key(client: str, unsigned: dict) -> tuple:
    canonical = json.dumps(
        [unsigned.get("kind"), unsigned.get("content"), unsigned.get("tags"), unsigned.get("created_at")],
        sort_keys=True,
        separators=(",", ":"),
    )
    return client, canonical
