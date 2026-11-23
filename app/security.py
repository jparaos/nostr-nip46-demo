"""Seguridad de la aplicación web local.

Amenaza principal: el servidor escucha en 127.0.0.1, pero cualquier página que
el usuario abra en su navegador puede intentar llamarlo. Escuchar en localhost
NO basta. Defensas:

1. Host permitido (anti DNS rebinding): solo 127.0.0.1:<puerto> y localhost:<puerto>.
2. Origin obligatorio e igual al propio en métodos que modifican estado.
3. CSRF de doble envío: cookie HttpOnly `signer_csrf` + el mismo token en el
   encabezado `X-CSRF-Token` (la página lo recibe en un <meta>). Un sitio ajeno
   no puede leer ninguno de los dos ni enviar el encabezado sin preflight CORS,
   y no se habilita CORS.
4. Sesión local: cookie HttpOnly + SameSite=Strict `signer_session`, emitida solo
   tras probar la contraseña (crear, importar o desbloquear). Se revocan todas
   al bloquear la bóveda o al detener el servidor.
5. Encabezados: CSP estricta sin scripts inline, no-store, sin framing, sin referrer.
"""

from __future__ import annotations

import secrets

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

SESSION_COOKIE = "signer_session"
CSRF_COOKIE = "signer_csrf"
CSRF_HEADER = "x-csrf-token"
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


class SessionStore:
    """Sesiones en memoria: el operador es uno solo y no sobreviven a un reinicio."""

    def __init__(self):
        self._tokens: set[str] = set()

    def create(self) -> str:
        token = secrets.token_urlsafe(32)
        self._tokens.add(token)
        return token

    def valid(self, token: str | None) -> bool:
        return bool(token) and any(secrets.compare_digest(token, t) for t in self._tokens)

    def revoke_all(self) -> None:
        self._tokens.clear()


def allowed_origins(port: int) -> set[str]:
    return {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}


class LocalOnlyMiddleware(BaseHTTPMiddleware):
    """Valida Host y Origin, y agrega encabezados de seguridad a toda respuesta."""

    def __init__(self, app, port: int):
        super().__init__(app)
        self.hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        self.origins = allowed_origins(port)

    async def dispatch(self, request: Request, call_next):
        client = request.client.host if request.client else ""
        if client not in ("127.0.0.1", "::1", "localhost", "testclient"):
            return _deny("conexión no local rechazada")
        if request.headers.get("host", "").lower() not in self.hosts:
            return _deny("host no permitido")
        if request.method in UNSAFE_METHODS and request.headers.get("origin") not in self.origins:
            return _deny("origen no permitido")

        response = await call_next(request)
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response


def _deny(reason: str) -> JSONResponse:
    return JSONResponse({"detail": reason}, status_code=403)


def ensure_csrf_cookie(request: Request) -> str:
    """Token CSRF existente o uno nuevo (se setea en la respuesta de la página)."""
    return request.cookies.get(CSRF_COOKIE) or secrets.token_urlsafe(32)


def set_cookie(response, name: str, value: str) -> None:
    # Secure no aplica: el servidor es http://127.0.0.1 (no sale de la máquina).
    response.set_cookie(name, value, httponly=True, samesite="strict", path="/")
