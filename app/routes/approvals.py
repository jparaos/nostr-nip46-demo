"""Solicitudes de firma: listar, aprobar, rechazar.

Opera sobre el MISMO ApprovalManager que usa el firmante: aprobar resuelve el
future que Nip46Signer está esperando y el flujo NIP-46 continúa; rechazar hace
que el firmante responda error al cliente remoto sin firmar.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from signer.approvals import RequestAlreadyResolved, RequestNotFound, SignRequest

from ..dependencies import AppState, require_csrf, require_session

router = APIRouter(prefix="/api/requests", tags=["aprobaciones"])


class RejectBody(BaseModel):
    reason: str | None = Field(default=None, max_length=200)


def serialize(req: SignRequest) -> dict:
    data = req.to_dict()
    data["client_short"] = f"{req.client[:8]}…{req.client[-4:]}"
    return data


@router.get("")
def list_requests(state: AppState = Depends(require_session)) -> dict:
    return {
        "pending": [serialize(r) for r in state.approvals.get_pending_requests()],
        "history": [serialize(r) for r in state.approvals.get_history()[:20]],
    }


@router.post("/{request_id}/approve", dependencies=[Depends(require_csrf)])
def approve(request_id: str, state: AppState = Depends(require_session)) -> dict:
    return _resolve(lambda: state.approvals.approve_request(request_id))


@router.post("/{request_id}/reject", dependencies=[Depends(require_csrf)])
def reject(request_id: str, body: RejectBody | None = None, state: AppState = Depends(require_session)) -> dict:
    return _resolve(lambda: state.approvals.reject_request(request_id, body.reason if body else None))


def _resolve(action) -> dict:
    try:
        return serialize(action())
    except RequestNotFound:
        raise HTTPException(status_code=404, detail="la solicitud no existe") from None
    except RequestAlreadyResolved:
        raise HTTPException(status_code=409, detail="la solicitud ya fue resuelta o expiró") from None
