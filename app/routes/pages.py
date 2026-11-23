"""Páginas HTML. Cada página recibe el token CSRF en un <meta> y su cookie."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from ..dependencies import AppState, get_state, has_session
from ..security import CSRF_COOKIE, ensure_csrf_cookie, set_cookie

templates = Jinja2Templates(directory=Path(__file__).resolve().parents[1] / "templates")
router = APIRouter(include_in_schema=False)


def render(request: Request, name: str, **context):
    token = ensure_csrf_cookie(request)
    response = templates.TemplateResponse(request, name, {"csrf_token": token, **context})
    set_cookie(response, CSRF_COOKIE, token)
    return response


@router.get("/")
def home(request: Request, state: AppState = Depends(get_state)):
    if not state.vault.exists:
        return RedirectResponse("/setup", status_code=303)
    if not has_session(request, state):
        return RedirectResponse("/unlock", status_code=303)
    return RedirectResponse("/dashboard", status_code=303)


@router.get("/setup")
def setup(request: Request, state: AppState = Depends(get_state)):
    if state.vault.exists:
        return RedirectResponse("/", status_code=303)
    return render(request, "setup.html", page="setup")


@router.get("/unlock")
def unlock(request: Request, state: AppState = Depends(get_state)):
    if not state.vault.exists:
        return RedirectResponse("/setup", status_code=303)
    return render(request, "unlock.html", page="unlock", pubkey=state.vault.public_key())


@router.get("/dashboard")
def dashboard(request: Request, state: AppState = Depends(get_state)):
    if not has_session(request, state):
        return RedirectResponse("/", status_code=303)
    return render(request, "dashboard.html", page="dashboard")


@router.get("/requests")
def requests_page(request: Request, state: AppState = Depends(get_state)):
    if not has_session(request, state):
        return RedirectResponse("/", status_code=303)
    return render(request, "requests.html", page="requests")
