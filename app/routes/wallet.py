"""Endpoints de la bóveda: crear, importar, desbloquear, bloquear."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from signer import vault as v

from ..dependencies import AppState, get_state, require_csrf, require_session
from ..security import SESSION_COOKIE, set_cookie

router = APIRouter(prefix="/api/vault", tags=["bóveda"])
CSRF = [Depends(require_csrf)]


class NewPassword(BaseModel):
    password: str = Field(max_length=1024)
    confirm: str = Field(max_length=1024)

    def checked(self) -> str:
        if self.password != self.confirm:
            raise HTTPException(status_code=400, detail="las contraseñas no coinciden")
        return self.password

    def __repr__(self) -> str:  # nunca imprimir la contraseña
        return "NewPassword(<oculto>)"


class ImportRequest(NewPassword):
    secret: str = Field(max_length=200)

    def __repr__(self) -> str:
        return "ImportRequest(<oculto>)"


class UnlockRequest(BaseModel):
    password: str = Field(max_length=1024)

    def __repr__(self) -> str:
        return "UnlockRequest(<oculto>)"


ERRORS = {
    v.VaultExistsError: (409, "ya existe una bóveda; no se sobrescribe"),
    v.VaultNotFoundError: (404, "no hay bóveda creada"),
    v.WrongPasswordError: (401, "contraseña incorrecta"),
    v.CorruptVaultError: (500, "el archivo de la bóveda está dañado"),
    v.InvalidKeyError: (400, "la clave no es un nsec1… ni un hex de 64 caracteres válido"),
    v.WeakPasswordError: (400, f"la contraseña debe tener al menos {v.MIN_PASSWORD_LEN} caracteres"),
}


def vault_error(exc: v.VaultError) -> HTTPException:
    status, message = ERRORS.get(type(exc), (400, "operación no válida"))
    return HTTPException(status_code=status, detail=message)  # mensajes fijos: nunca eco de datos


async def open_session(state: AppState, payload: dict, status: int = 200) -> JSONResponse:
    """Inicia el firmante con las claves de la bóveda y emite la cookie de sesión."""
    await state.runtime.start(state.vault.signing_keys())
    response = JSONResponse(payload, status_code=status)
    set_cookie(response, SESSION_COOKIE, state.sessions.create())
    return response


@router.get("/state")
def vault_state(state: AppState = Depends(get_state)) -> dict:
    return {"exists": state.vault.exists, "unlocked": state.vault.is_unlocked}


@router.post("/create", dependencies=CSRF)
async def create(body: NewPassword, state: AppState = Depends(get_state)) -> JSONResponse:
    try:
        created = await state.vault.create(body.checked())
    except v.VaultError as exc:
        raise vault_error(exc) from None
    # Única respuesta que incluye el nsec: se muestra una vez para respaldo (Cache-Control: no-store).
    return await open_session(state, {"pubkey": created.pubkey, "npub": created.npub,
                                      "nsec_backup": created.nsec_backup}, status=201)


@router.post("/import", dependencies=CSRF)
async def import_key(body: ImportRequest, state: AppState = Depends(get_state)) -> JSONResponse:
    try:
        pubkey = await state.vault.import_key(body.secret, body.checked())
    except v.VaultError as exc:
        raise vault_error(exc) from None
    return await open_session(state, {"pubkey": pubkey, "npub": _npub(pubkey)}, status=201)


@router.post("/unlock", dependencies=CSRF)
async def unlock(body: UnlockRequest, state: AppState = Depends(get_state)) -> JSONResponse:
    try:
        pubkey = await state.vault.unlock(body.password)
    except v.VaultError as exc:
        raise vault_error(exc) from None
    return await open_session(state, {"pubkey": pubkey, "npub": _npub(pubkey)})


@router.post("/lock", dependencies=CSRF)
async def lock(request: Request, state: AppState = Depends(require_session)) -> JSONResponse:
    await state.runtime.stop()  # lo pendiente se rechaza; no se firma nada más
    state.vault.lock()
    state.sessions.revoke_all()
    response = JSONResponse({"locked": True})
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


def _npub(pubkey: str) -> str:
    import nostr_sdk as sdk

    return sdk.PublicKey.parse(pubkey).to_bech32()
