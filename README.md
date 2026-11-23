# Firmante NIP-46 local para iniciar sesión en Stacker News

> Prueba técnica — Desarrollador Full-Stack Semi Senior, Ministerio de Hacienda (Secretaría de Gobierno Digital).

## 1. Descripción del proyecto

Aplicación local en **Python** que guarda una clave secreta de Nostr en una **bóveda cifrada** y la usa a través de un **firmante remoto NIP-46** implementado en este repositorio para iniciar sesión en [Stacker News](https://stacker.news). La clave nunca sale del equipo: Stacker News pide firmas a través de relays Nostr y la persona aprueba o rechaza cada una desde una **interfaz web local** (o desde la consola).

## 2. Objetivo de la solución

Demostrar la firma remota NIP-46 de punta a punta con el menor alcance que cumple la consigna, documentando supuestos y tradeoffs.

| Requisito de la consigna | Cumplimiento |
|---|---|
| Ingresar o crear una clave secreta de Nostr | ✅ Web (pantalla de configuración) y consola (`init`, `init --import`); nsec o hex |
| Esa clave es usada por un firmante NIP-46 implementado por la persona postulante | ✅ Protocolo propio (`signer/nip46.py`, `signer/service.py`, `signer/relay.py`); `nostr-sdk` solo para criptografía |
| Demostración del login en Stacker News de punta a punta | ✅ Login real verificado con la interfaz web el 22-11-2026 (y antes con la consola, el 20-11-2026); capturas en la sección 14.4 |
| Python, Rust, Elixir o Go; HTML/CSS/JS simples sin frameworks | ✅ Python 3.13; Jinja2 + CSS + JavaScript nativo |
| README con instalación, supuestos y tradeoffs | ✅ Este documento |
| Registros de conversación con el agente de IA | ✅ [`ai-logs/`](ai-logs/) (saneados) |

## 3. Arquitectura

Un solo proceso local con tres capas: **interfaz** (web o consola), **servicio del firmante** y **protocolo/criptografía**. La interfaz y el firmante comparten **una única cola de aprobaciones** (`ApprovalManager`): cuando llega un `sign_event`, el firmante espera en un `asyncio.Future` que la persona resuelve desde la web.

| Archivo | Responsabilidad |
|---|---|
| [`signer/crypto.py`](signer/crypto.py) | Serialización e id de eventos (NIP-01), firma, cifrado NIP-44/NIP-04 (envoltorio de `nostr-sdk`) |
| [`signer/nip46.py`](signer/nip46.py) | Protocolo NIP-46: URL `bunker://`, `secret` de un solo uso, validación del evento, despacho de métodos |
| [`signer/relay.py`](signer/relay.py) | WebSocket a relays: suscripción, publicación, reconexión, eventos duplicados |
| [`signer/service.py`](signer/service.py) | `SignerService`: valida, descifra, despacha y responde; una tarea por evento |
| [`signer/approvals.py`](signer/approvals.py) | `ApprovalHandler` (contrato) y `ApprovalManager` (cola con tiempo límite, duplicados, límite de pendientes) |
| [`signer/vault.py`](signer/vault.py) | Bóveda: generar/importar/validar clave, scrypt + AES-256-GCM, persistencia atómica, bloqueo |
| [`signer/audit.py`](signer/audit.py) | Auditoría `logs/session.jsonl` con minimización de datos |
| [`signer/main.py`](signer/main.py) | Interfaz de consola (alternativa) |
| [`app/main.py`](app/main.py) | Aplicación FastAPI: composición, `lifespan`, middleware |
| [`app/security.py`](app/security.py), [`app/dependencies.py`](app/dependencies.py) | Host/Origin, sesión local, CSRF, encabezados; estado compartido |
| [`app/services/`](app/services/) | `VaultService` (scrypt en un hilo) y `SignerRuntime` (inicia/detiene `SignerService`) |
| [`app/routes/`](app/routes/) | `wallet.py` (bóveda), `signer.py` (estado, nueva URL), `approvals.py` (solicitudes), `pages.py` (HTML) |
| [`app/templates/`](app/templates/), [`app/static/`](app/static/) | Jinja2, CSS y JavaScript nativo |
| [`tools/demo_client.py`](tools/demo_client.py) | Cliente NIP-46 que replica la solicitud de Stacker News (kind 27235, opción `--nip04`) |
| [`tools/export_ai_log.py`](tools/export_ai_log.py) | Exporta y sanea la transcripción de Claude Code para `ai-logs/` |
| [`tests/`](tests/) | 86 pruebas automatizadas (sección 14) |

**Flujo NIP-46** (métodos soportados: `connect`, `get_public_key`, `sign_event`, `ping`, `switch_relays`; cualquier otro responde `error`):

1. Al desbloquear la bóveda, el firmante se suscribe a eventos kind 24133 dirigidos a su pubkey y genera `bunker://<pubkey>?relay=…&secret=<aleatorio>`.
2. Stacker News envía `connect` con el `secret`; el firmante lo valida, lo invalida (un solo uso), autoriza al cliente y responde `ack`.
3. `get_public_key` → pubkey del usuario.
4. `sign_event` → el evento se valida y queda **pendiente** en la web; al aprobar se firma con la clave de la bóveda; al rechazar (o a los 120 s) se responde `error` sin firmar.
5. Cada respuesta se cifra con el **mismo esquema** que usó el cliente (NIP-04 o NIP-44).

## 4. Diagrama

![Diagrama de arquitectura: usuario, aplicación local (interfaz web, bóveda cifrada, generador bunker://, SignerService + Nip46Signer + ApprovalManager), relays Nostr y Stacker News](img/arquitectura.png)

_Fuente editable: [`img/arquitectura.svg`](img/arquitectura.svg)._

## 5. Tecnologías utilizadas

| Tecnología | Uso |
|---|---|
| Python 3.13 (compatible 3.11+), `asyncio` | Lenguaje y concurrencia |
| [`nostr-sdk`](https://pypi.org/project/nostr-sdk/) 0.45.2 | Solo primitivas: claves secp256k1, firma Schnorr, cifrado NIP-44/NIP-04 |
| `websockets` 17.4 | Conexión a relays |
| `cryptography` 50.0.2 + `hashlib.scrypt` | AES-256-GCM y derivación de la contraseña |
| FastAPI 0.165 + Uvicorn 0.54 + Jinja2 3.1 | Servidor web local y plantillas |
| HTML + CSS + JavaScript nativo | Interfaz, sin frameworks de frontend |
| `unittest` + `httpx` (TestClient) | Pruebas |

## 6. Instalación

```bash
git clone https://github.com/jparaos/nostr-nip46-demo.git
cd nostr-nip46-demo
python -m venv .venv
.venv\Scripts\activate          # Windows  (Linux/macOS: source .venv/bin/activate)
pip install -r requirements.txt
pip install -r requirements-dev.txt   # solo para ejecutar las pruebas
```

## 7. Configuración

No hay archivo de configuración: todo es por línea de comandos.

| Opción | Por defecto | Descripción |
|---|---|---|
| `--port` | `8046` | Puerto web. El host es siempre `127.0.0.1` (no configurable) |
| `--vault RUTA` | `~/.nip46-signer/vault.json` | Ubicación de la bóveda (fuera del repositorio). También `SIGNER_VAULT_PATH` |
| `--relay wss://…` (repetible) | `nos.lol`, `relay.primal.net`, `relay.damus.io` | Relays donde escucha el firmante (y que van en la URL `bunker://`) |
| `--approval-timeout` | `120` | Segundos antes de rechazar automáticamente una firma pendiente |

## 8. Ejecución

```bash
python -m app            # interfaz web → abrir http://127.0.0.1:8046
```

Alternativa por consola (misma bóveda): `python -m signer.main` (ver `python -m signer.main --help`).

## 9. Creación e importación de claves

En la primera ejecución, `http://127.0.0.1:8046` abre la **configuración**:

- **Crear clave nueva:** contraseña + confirmación → se genera la clave con `nostr-sdk`, se guarda cifrada y se muestra la clave pública. El `nsec` se muestra **una sola vez** (borroso hasta pulsar "Mostrar") con una advertencia; hay que marcar "Ya guardé mi clave secreta" para continuar. No se puede volver a recuperar desde la interfaz.
- **Importar nsec:** se pega un `nsec1…` o un hex de 64 caracteres; se valida, se cifra y se muestra la pubkey. Una clave importada nunca se devuelve.

Consola: `python -m signer.main init` o `init --import` (la clave se escribe oculta). Nunca se sobrescribe una bóveda existente.

| Configuración inicial | Clave creada (nsec oculto) |
|---|---|
| ![Pantalla de configuración](img/evidencias/01-configuracion.png) | ![Bóveda creada con advertencia de respaldo](img/evidencias/02-creacion-clave.png) |

## 10. Uso de la bóveda

- **Desbloquear:** en ejecuciones siguientes, `/unlock` pide la contraseña. Si es incorrecta se muestra "Contraseña incorrecta. La bóveda sigue bloqueada."; la bóveda no se desbloquea hasta ingresar la correcta.
- **Bloquear:** botón **Bloquear** (arriba a la derecha). Detiene el firmante, rechaza lo pendiente sin firmar, suelta la clave de memoria y revoca la sesión.
- **Formato:** JSON con pubkey pública, parámetros de scrypt, salt, nonce y texto cifrado (sección 18).

## 11. Inicio del firmante

El firmante arranca **al desbloquear** (o al crear/importar). El panel muestra:

- Estado de la bóveda, del firmante y de cada relay (conectado / reintentando) y un indicador de conexión.
- La URL `bunker://` con el `secret` **oculto en pantalla** (botón "Mostrar secret"); **Copiar** copia la URL completa.
- Clientes autorizados (solo pubkeys abreviadas) y solicitudes pendientes.
- **Generar nueva URL:** nuevo `secret` (el anterior es de un solo uso); olvida los clientes autorizados.

![Panel con estado, relays y URL bunker](img/evidencias/03-dashboard.png)

## 12. Conexión con Stacker News

1. En el panel, **Copiar** la URL `bunker://…`.
2. Abrir <https://stacker.news/signup?callbackUrl=https%3A%2F%2Fstacker.news%2F&type=nostr>, elegir el login con Nostr remoto (NIP-46 / bunker) y pegar la URL.
3. Aprobar la solicitud que aparece en el panel (sección 13).

![URL bunker con el secret oculto](img/evidencias/04-url-bunker.png)

**Comportamiento observado de Stacker News** (sesión real del 20-11-2026; el firmante registra cada solicitud):

| Pregunta | Observado |
|---|---|
| Cifrado | **NIP-04**, no NIP-44. Responder con el mismo esquema (S7) resultó indispensable |
| Métodos y orden | `connect` → `get_public_key` → `sign_event`. No envía `switch_relays` ni `ping` |
| Parámetros de `connect` | El primero (pubkey del firmante) llega **vacío**; solo trae el `secret` |
| Evento a firmar | **kind 27235** (NIP-98): `content` `"Stacker News Authentication"`, tags `challenge`, `u` = `https://stacker.news`, `method` = `GET`. Incluye `pubkey` e `id`, que el firmante verifica/recalcula |

## 13. Aprobación de solicitudes

Cada `sign_event` aparece en el panel y en **Solicitudes** con identificador, cliente, método, sitio (tag `u`), kind, contenido, tags y fecha de creación, más una cuenta regresiva.

- **APROBAR:** la decisión llega al `ApprovalManager`, el `Nip46Signer` firma con la clave de la bóveda y responde al cliente remoto.
- **RECHAZAR:** el firmante responde `error` (`user rejected the signature`) sin firmar.
- **Sin respuesta en 120 s:** se rechaza automáticamente. No existe autoaprobación en la web.

| Solicitud pendiente | Aprobada | Rechazada |
|---|---|---|
| ![Solicitud kind 27235 pendiente](img/evidencias/05-solicitud-pendiente.png) | ![Historial con firma aprobada](img/evidencias/06-aprobacion.png) | ![Historial con firma rechazada](img/evidencias/07-rechazo.png) |

## 14. Pruebas

### 14.1 Automatizadas — 86 pruebas, todas pasan (1 omitida en Windows)

```bash
python -m unittest -v
```

| Archivo | Pruebas | Cubre |
|---|---|---|
| `test_nip46.py` | 9 | URL bunker, secret de un solo uso, cliente no autorizado, métodos, cifrado NIP-04/NIP-44 |
| `test_approvals.py` | 12 | Pendiente, aprobación, rechazo, timeout, concurrentes, duplicados, inexistente, doble resolución, cancelación |
| `test_service.py` | 8 | Flujo completo, rechazo sin firma, timeout, `ping` mientras una firma espera, detener sin firmar, evento inválido, auditoría sin secretos |
| `test_vault.py` | 19 (+1 POSIX omitida) | Generación, importación nsec/hex, claves inválidas sin eco, cifrado, contraseña incorrecta, 8 tipos de archivo corrupto, alteración detectada, bloqueo, reinicio, sin sobrescritura, archivo sin nsec |
| `test_cli.py` | 3 | La consola compila, sin bóveda explica cómo crearla, `init` no sobrescribe |
| `test_web.py` | 22 | Creación, importación, desbloqueo, bloqueo, inicio del firmante, bunker URL, solicitudes visibles, aprobación y rechazo con eventos NIP-04 reales, acceso sin sesión, CSRF, Origin/Host ajenos, CORS, cookies, secretos en respuestas, sin autoaprobación |
| `test_hardening.py` | 13 | Regresiones de la revisión final: validación del evento antes de aprobar, respuesta con el mismo esquema, eventos fuera de ventana, no dirigidos o alterados, cola acotada, duplicados entre relays, `secret` ausente de los logs, descartes contados y auditados, respuesta sin relay informada, relays por defecto |

### 14.2 Manuales (navegador)

Recorridas en el navegador integrado sobre la app real (`python -m app`): configuración, respaldo del nsec, panel, copiar URL, solicitud visible, aprobar, rechazar, bloquear, contraseña incorrecta y desbloqueo. Sin errores en la consola del navegador. Capturas en [`img/evidencias/`](img/evidencias/).

### 14.3 Con relays reales (22-11-2026)

`tools/demo_client.py` replica a Stacker News (kind 27235 con `challenge`/`u`/`method`, cifrado NIP-04) contra la app web sobre `relay.damus.io` y `nos.lol` (`relay.nsec.app` no conectó desde la red de prueba):

- Aprobación desde la web → `RESULTADO: OK - firma válida del usuario`.
- Con los relays por defecto actuales (`nos.lol`, `relay.primal.net`, `relay.damus.io`): 3/3 conectados, las solicitudes llegaron por `nos.lol` y `relay.primal.net`, y el panel mostró 3 recibidas · 3 respondidas · 0 descartadas.
- Rechazo desde la web → el cliente recibió `user rejected the signature`, sin firma.

```bash
python -m tools.demo_client "bunker://…" --nip04
```

### 14.4 Prueba real con Stacker News

Ejecutadas por la persona postulante en su navegador, con claves de prueba.

**22-11-2026 — interfaz web: verificada de punta a punta.**

1. Primer intento fallido: con los relays por defecto de entonces (`relay.nsec.app`, `relay.damus.io`, `nos.lol`) las solicitudes de Stacker News no llegaban. Diagnóstico: desde la red de prueba `relay.nsec.app` no respondía y `relay.damus.io` devolvía HTTP 503. Se cambiaron los relays por defecto y se agregó al panel el diagnóstico de eventos por relay y motivos de descarte.
2. Segundo intento, con `nos.lol`, `relay.primal.net` y `relay.damus.io`: se pegó la URL `bunker://` en "Connect with token or NIP-05"; llegaron `connect` → `get_public_key` → `sign_event` (kind 27235, `u` = `https://stacker.news`) por `nos.lol` y `relay.primal.net`; se aprobó desde la web y Stacker News creó la cuenta `@60d7a9523c` e inició sesión con la misma npub (`npub1vrt6j…6a4s7jk76k`) que muestra el panel.

| Panel antes de conectar | URL pegada en Stacker News | Solicitud kind 27235 en el panel |
|---|---|---|
| ![Panel sin eventos](img/evidencias/08-sn-panel-antes.png) | ![Login con token en Stacker News](img/evidencias/09-sn-pegar-bunker.png) | ![Solicitud de Stacker News pendiente](img/evidencias/10-sn-solicitud-27235.png) |

| Firma aprobada (3 recibidos · 3 respondidos) | Sesión iniciada | Perfil con la misma npub |
|---|---|---|
| ![Panel tras aprobar](img/evidencias/11-sn-firma-aprobada.png) | ![Onboarding de Stacker News con sesión iniciada](img/evidencias/12-sn-sesion-iniciada.png) | ![Perfil @60d7a9523c en Stacker News](img/evidencias/13-sn-perfil.png) |

El `secret` aparece oculto en las capturas; el `challenge` visible es un desafío de un solo uso ya consumido.

**20-11-2026 — prototipo de consola: verificado.** Stacker News creó la cuenta `@949190fb6b` con la npub que mostró el firmante (esa clave quedó expuesta en la conversación y se descartó).

![Perfil en Stacker News con sesión iniciada mediante el prototipo de consola (20-11-2026)](img/login-stacker-news.png)

## 15. Supuestos

| # | Tema | Supuesto | Justificación |
|---|------|----------|---------------|
| S1 | Clave del firmante vs. del usuario | La **misma** clave para ambos roles | NIP-46 lo permite; reduce complejidad |
| S2 | Usuarios | Una persona, una bóveda, una clave | La consigna habla de "un usuario" |
| S3 | Almacenamiento | Archivo local cifrado: scrypt + AES-256-GCM | Protección en reposo sin servicios externos |
| S4 | Relays | Tres relays públicos, configurables | Stacker News y el firmante deben compartir relays |
| S5 | `secret` | Obligatorio y de un solo uso; luego se recuerdan los clientes autorizados (en memoria) | Evita que cualquiera que vea la pubkey pueda conectarse |
| S6 | Permisos (`perms`) | Se ignoran; la autorización real es la aprobación manual | Más fuerte que permisos declarados |
| S7 | Cifrado | Se responde con el mismo esquema recibido (NIP-04 o NIP-44) | Confirmado: Stacker News usa NIP-04 |
| S8 | Interfaz | Web local + consola como alternativa | Permitido por la consigna |
| S9 | Ejecución | El firmante corre en la máquina del usuario mientras dura el login | No se busca un servicio 24/7 |
| S10 | Sesiones | En memoria; no sobreviven a un reinicio | Reiniciar exige volver a desbloquear |

**Fuera de alcance:** flujo `nostrconnect://`, multiusuario, billetera de producción, extensión de navegador, métodos `nip04_*`/`nip44_*` como servicio, `logout`, descubrimiento NIP-05/NIP-89.

## 16. Tradeoffs

- **`nostr-sdk` solo para criptografía.** La serialización de eventos, los relays y toda la lógica NIP-46 están en este repositorio, porque la consigna pide un firmante propio. Se descartó `pynostr` porque no implementa NIP-44.
- **Misma clave para firmante y usuario (S1).** Más simple; a cambio la pubkey del usuario queda en la URL `bunker://`.
- **Aprobación manual de cada firma.** Una interacción más, pero ningún cliente conectado firma sin revisión.
- **Relays públicos.** Sin infraestructura propia, pero dependientes de su disponibilidad. `relay.nsec.app` (el relay NIP-46 más usado) nunca respondió desde la red de prueba y se sacó de los valores por defecto; `relay.damus.io` respondió 503 de forma intermitente. Por eso son tres, se reintentan con espera exponencial y el panel muestra cuántos eventos entrega cada uno.
- **AES-256-GCM propio vs. NIP-49 (`ncryptsec`).** NIP-49 no requería dependencias y es interoperable; se eligió AES-256-GCM por requisito explícito, a costa de `cryptography` y un formato propio versionado.
- **Archivo cifrado vs. llavero del sistema operativo.** Portable y revisable; el llavero sería más seguro pero dependiente de la plataforma.
- **Polling (1,5 s) vs. SSE/WebSocket.** Menos piezas y fácil de probar; la demora no se nota en una aprobación manual.
- **Una sola cola de aprobaciones.** Web y consola resuelven la misma espera del firmante; no hay nada que sincronizar.
- **Nueva URL = reiniciar el firmante.** Un `secret` nuevo sin tocar el protocolo; a cambio se olvidan los clientes autorizados.
- **Sin SQLite.** El único estado persistible (clientes autorizados) no lo justifica; volver a pegar la URL tras un reinicio es aceptable y más seguro.

## 17. Limitaciones conocidas

- Python no garantiza borrar la memoria: el objeto `Keys` y los `str` intermedios (hex, contraseña) viven hasta que el recolector los libera.
- La fortaleza de la bóveda depende de la contraseña (mínimo 8 caracteres, sin medidor). Quien copie el archivo puede probar contraseñas offline; solo lo frena el costo de scrypt. La web no limita intentos de desbloqueo (es local y exige CSRF).
- GCM no distingue "contraseña incorrecta" de "archivo alterado".
- En Windows no se aplican permisos al archivo; lo protege la ACL del perfil del usuario.
- Los clientes autorizados y las sesiones viven en memoria: tras reiniciar hay que desbloquear y volver a pegar la URL.
- Depende de relays públicos: la URL `bunker://` incluye los relays configurados aunque alguno esté caído; si ninguno es compartido y alcanzable por ambos lados, las solicitudes no llegan. El panel lo hace visible (eventos por relay, recibidos/respondidos/descartados y motivo).
- `TestClient` de Starlette muestra un aviso de deprecación (recomienda `httpx2`); no afecta las pruebas.

## 18. Seguridad

### Bóveda

| Elemento | Elección | Motivo |
|---|---|---|
| Derivación | `scrypt`, N=2¹⁷, r=8, p=1 → 32 bytes | ~128 MiB y ~0,3 s por intento: encarece ataques de diccionario; nunca un hash rápido |
| Cifrado | AES-256-GCM | Autenticado: detecta contraseña incorrecta y archivos alterados |
| Aleatoriedad | `secrets.token_bytes`: salt 16 B y nonce 12 B nuevos en cada guardado | Sin valores constantes |
| Datos asociados | `version`, `pubkey` y parámetros de scrypt | Cambiar la pubkey o bajar el costo en el archivo hace fallar el descifrado |
| Límites al leer | N entre 2¹⁴ y 2²⁰, r=8, p=1 | Un archivo manipulado no degrada el costo ni agota la memoria |
| Persistencia | Escritura atómica, sin sobrescritura, `600/700` en POSIX, fuera del repo | Sin estados intermedios ni bóvedas pisadas |

En disco solo hay datos cifrados; nunca la contraseña ni el nsec. Los errores se re-lanzan con mensajes fijos (sin eco de la clave o contraseña) y `repr()` oculta los secretos.

### Interfaz web

Escuchar en `127.0.0.1` no basta: cualquier página abierta en el navegador puede intentar llamar al servidor (CSRF) o apuntar su dominio a `127.0.0.1` (DNS rebinding).

| Control | Implementación |
|---|---|
| Solo local | Uvicorn en `127.0.0.1` fijo; el middleware rechaza clientes no locales |
| Host | Solo `127.0.0.1:<puerto>` y `localhost:<puerto>` (anti DNS rebinding) |
| Origin | Obligatorio en POST e igual al propio |
| CSRF | Cookie HttpOnly + mismo token en `X-CSRF-Token` |
| Sesión | Cookie HttpOnly + `SameSite=Strict`, solo tras probar la contraseña; se revoca al bloquear o detener |
| CORS | No habilitado |
| Encabezados | CSP `script-src 'self'`, `X-Frame-Options: DENY`, `Cache-Control: no-store`, `Referrer-Policy: no-referrer` |
| Datos remotos | Contenido y tags se pintan con `textContent`, nunca `innerHTML` |

**Secretos en respuestas:** ningún endpoint devuelve la contraseña ni la clave, salvo la respuesta de creación, que entrega el nsec **una vez** para respaldo (`no-store`). El `secret` de la URL se muestra oculto en el panel.

### Protocolo y servicio

| Riesgo | Control |
|---|---|
| Eventos manipulados | Se verifican id y firma Schnorr; se descartan los no dirigidos al firmante (tag `p`) |
| Repetición / eventos viejos | Se descartan solicitudes NIP-46 con `created_at` fuera de ±10 min |
| Evento malformado a firmar | `validate_unsigned`: kind entero 0–65535, `content` texto, tags listas de textos, `pubkey` igual al firmante, ≤ 64 KB; se valida **antes** de pedir aprobación |
| Cliente no autorizado | Solo `connect` con el `secret` autoriza; el resto responde `unauthorized` |
| Solicitudes que nunca se resuelven | Rechazo automático a los 120 s; al detener o bloquear, todo lo pendiente se rechaza |
| Duplicados | Ids vistos entre relays (acotado a 4096); reenvíos idénticos comparten la misma decisión |
| Saturación | Máximo 20 solicitudes pendientes; el resto se rechaza |
| Concurrencia | Una tarea por evento: una firma pendiente no bloquea `ping` ni a otros clientes |
| Diagnóstico | Cada evento descartado queda en la auditoría con su motivo (sin contenido) y se cuenta en el panel junto a los eventos por relay y las respuestas sin relay conectado |
| Logs | La auditoría no guarda el `secret`, firmas, eventos firmados ni contenido (solo kind, nombres de tags, largo y hash); el log de servicio no incluye el `secret` (probado) |

## 19. Uso del agente de IA

El desarrollo completo se hizo con **Claude Code** en una sesión conducida por etapas: exploración del protocolo, README y diagrama primero, prototipo por consola y login real, y luego incrementos aprobados uno a uno (desacople del firmante, bóveda, web, revisión final). En [`ai-logs/`](ai-logs/) están:

- [`ai-logs/README.md`](ai-logs/README.md): forma de trabajo, decisiones relevantes con su origen y errores del agente detectados durante el trabajo.
- [`ai-logs/01-sesion-claude-code.md`](ai-logs/01-sesion-claude-code.md): transcripción saneada (sin nsec, contraseñas, `secret` ni datos personales), generada con `tools/export_ai_log.py`.

## Referencias

- [Nostr](https://nostr.com)
- [NIP-46 — Nostr Remote Signing](https://github.com/nostr-protocol/nips/blob/master/46.md)
- [NIP-44 — Encrypted Payloads](https://github.com/nostr-protocol/nips/blob/master/44.md)
- [NIP-98 — HTTP Auth](https://github.com/nostr-protocol/nips/blob/master/98.md)
- [Login con Nostr en Stacker News](https://stacker.news/signup?callbackUrl=https%3A%2F%2Fstacker.news%2F&type=nostr)
