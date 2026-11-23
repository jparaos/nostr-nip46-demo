"""Estado del firmante y regeneración de la URL bunker://."""

from __future__ import annotations

import nostr_sdk as sdk
from fastapi import APIRouter, Depends

from ..dependencies import AppState, require_csrf, require_session

router = APIRouter(prefix="/api", tags=["firmante"])


@router.get("/status")
def status(state: AppState = Depends(require_session)) -> dict:
    pubkey = state.vault.public_key()
    return {
        "vault": {"exists": state.vault.exists, "unlocked": state.vault.is_unlocked},
        "pubkey": pubkey,
        "npub": sdk.PublicKey.parse(pubkey).to_bech32() if pubkey else None,
        "signer": state.runtime.status(),
        "pending": len(state.approvals.get_pending_requests()),
        "approval_timeout": state.approvals.timeout,
    }


@router.post("/signer/restart", dependencies=[Depends(require_csrf)])
async def restart(state: AppState = Depends(require_session)) -> dict:
    await state.runtime.restart(state.vault.signing_keys())
    return state.runtime.status()
