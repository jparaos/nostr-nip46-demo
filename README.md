# Firmante NIP-46 local para iniciar sesión en Stacker News

> Prueba técnica — Desarrollador Full-Stack Semi Senior, Ministerio de Hacienda (Secretaría de Gobierno Digital).

Aplicación local en **Python** que guarda una clave secreta de Nostr en una **bóveda cifrada** y la usa a través de un **firmante remoto NIP-46** implementado en este repositorio para iniciar sesión en [Stacker News](https://stacker.news). La clave nunca sale del equipo: Stacker News pide firmas a través de relays Nostr y la persona aprueba o rechaza cada una desde una **interfaz web local** (o desde la consola).

![Arquitectura](img/arquitectura.png)

**Resultado:** login real en Stacker News verificado con la interfaz web el 22-11-2026 ([evidencias](docs/DOCUMENTACION_TECNICA.md#prueba-real-con-stacker-news)).

## Instalación

Requisitos: Python 3.11+ (probado con 3.13 en Windows 11) y git.

```bash
git clone https://github.com/jparaos/nostr-nip46-demo.git
cd nostr-nip46-demo
python -m venv .venv
.venv\Scripts\activate          # Windows  (Linux/macOS: source .venv/bin/activate)
pip install -r requirements.txt
```

## Uso rápido

```bash
python -m app            # abrir http://127.0.0.1:8046
```

1. **Configura la bóveda:** crea una clave nueva (respalda el `nsec` que se muestra una sola vez) o importa un `nsec`, y define una contraseña.
2. **Copia la URL `bunker://`** del panel.
3. En [Stacker News](https://stacker.news/signup?callbackUrl=https%3A%2F%2Fstacker.news%2F&type=nostr), elige *Continue with Nostr* → **Connect with token or NIP-05**, pega la URL y continúa.
4. **Aprueba** en el panel la solicitud de firma (kind 27235). Stacker News inicia sesión con tu npub.

Pruebas: `pip install -r requirements-dev.txt` y `python -m unittest` (86 pruebas).

Detalle de configuración, consola alternativa y capturas: **[Manual de instalación y uso](docs/MANUAL_INSTALACION.md)**.

## Supuestos

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

## Tradeoffs

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

## Más documentación

- **[Manual de instalación y uso](docs/MANUAL_INSTALACION.md):** configuración, ejecución, claves, bóveda, conexión con Stacker News y aprobación de solicitudes.
- **[Documentación técnica](docs/DOCUMENTACION_TECNICA.md):** cumplimiento de la consigna, arquitectura, tecnologías, pruebas y evidencias, limitaciones, seguridad.
- **[Registros del agente de IA](ai-logs/):** transcripción saneada de Claude Code y decisiones relevantes.
