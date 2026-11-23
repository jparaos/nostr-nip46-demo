# Registros de trabajo con el agente de IA

Todo el desarrollo se hizo en **una sesión de Claude Code** (app de escritorio, modelo Claude Opus 5.5), entre el 19 y el 22 de noviembre de 2026.

| Archivo | Contenido |
|---|---|
| [`01-sesion-claude-code.md`](01-sesion-claude-code.md) | Transcripción saneada: 15 mensajes de la persona, todas las respuestas de texto de Claude y una lista colapsable de herramientas usadas en cada tramo |

## Cómo se generó y saneó

La transcripción se exportó desde el registro JSONL local de la sesión con [`tools/export_ai_log.py`](../tools/export_ai_log.py), que deja documentado qué se incluye y qué se redacta:

- **Incluye:** mensajes de la persona, texto de las respuestas, nombre y descripción de cada herramienta usada (comandos, archivos editados, acciones en el navegador).
- **Excluye:** salidas de herramientas (contenido de archivos, resultados de comandos), razonamiento interno del modelo, avisos del sistema y metadatos.
- **Redacta:** `nsec1…`/`ncryptsec1…`, el `secret` de las URL `bunker://`, las contraseñas de prueba usadas, correos personales, el enlace de videoconferencia de la invitación y el nombre de usuario de Windows en rutas.
- **Verificación:** después de exportar se buscó en el archivo cada patrón sensible (nsec, secret, contraseñas, correo, usuario). Resultado: 0 coincidencias reales (solo menciones del formato `ncryptsec1…` en explicaciones).

Nota: el nsec que aparece en la conversación (pegado desde la consola del prototipo el 20-11) pertenece a una clave de prueba que se considera comprometida y no se usa más; igualmente está redactado.

La exportación se regeneró tras el login real con la interfaz web; el mensaje final de Claude de esa última respuesta no alcanza a quedar incluido. Para regenerarla:

```bash
python -m tools.export_ai_log <ruta-al-jsonl-de-la-sesion> ai-logs/01-sesion-claude-code.md --redact <contraseñas-de-demo...>
```

Las contraseñas usadas en las demostraciones (bóvedas temporales ya eliminadas) se pasan con `--redact` para no escribirlas en el código publicado.

## Forma de trabajo (prompting)

La persona postulante condujo el trabajo en etapas cortas, pidiendo diagnóstico antes de cambios y aprobando cada incremento:

1. **Exploración** (19-11): entender el flujo `bunker://` de NIP-46 y los supuestos del enunciado; Claude leyó la especificación NIP-46 con un subagente de lectura web.
2. **README primero**: supuestos, alcance y tradeoffs antes de escribir código.
3. **Diagrama** de arquitectura para el README.
4. **Prototipo por consola** (20-11) y **login real en Stacker News**, ejecutado por la persona; su salida resolvió las preguntas abiertas (NIP-04, kind 27235, `connect` con destinatario vacío).
5. **"Prompt 0"** (22-11): reencuadre arquitectónico; Claude debía inspeccionar, diagnosticar y proponer un plan incremental **sin modificar código** hasta su aprobación.
6. **Incremento 1:** desacoplar el firmante de la consola (`ApprovalManager`, `SignerService`, auditoría saneada).
7. **Incremento 2:** bóveda cifrada (scrypt + AES-256-GCM).
8. **Incremento 3:** interfaz web FastAPI con seguridad local.
9. **Revisión final:** auditoría funcional y de seguridad, evidencias, documentación y estos registros.
10. **Diagnóstico con el sitio real** (22-11): con la web, las solicitudes de Stacker News no llegaban. Sin compartir la URL `bunker://`, la persona reportó el síntoma; Claude verificó conectividad (relay.nsec.app inalcanzable, relay.damus.io con 503), confirmó en el código de NDK que el cliente usa todos los relays de la URL, cambió los relays por defecto y agregó diagnóstico al panel. El segundo intento terminó con login real exitoso, documentado con las capturas de la persona.

## Decisiones relevantes y su origen

| Decisión | Quién / cuándo | Motivo |
|---|---|---|
| Python + `nostr-sdk` solo para criptografía, protocolo NIP-46 propio | Propuesta de Claude, elegida por la persona (20-11) | `pynostr` no implementa NIP-44; la consigna pide un firmante propio |
| Flujo `bunker://` (no `nostrconnect://`) | Claude (19-11), confirmado en el login real | Stacker News acepta `bunker://` |
| Responder con el mismo cifrado recibido (NIP-04/NIP-44) | Claude (prototipo) | Resultó indispensable: Stacker News usa NIP-04 |
| Aprobación manual de cada firma, sin autoaprobación en la web | Claude, exigido luego por la persona | Un cliente conectado no puede firmar en nombre del usuario sin revisión |
| `ApprovalManager` como única cola compartida | Persona (requisito del incremento 1) | La consola y la web resuelven la misma espera del firmante |
| No usar SQLite | Claude (Prompt 0), aceptado | El único estado persistible (clientes autorizados) no justifica una base de datos |
| AES-256-GCM en vez de NIP-49 | Persona (requisito explícito); Claude había recomendado NIP-49 | Documentado como tradeoff en el README |
| Validación de Host/Origin + CSRF + sesión `SameSite=Strict` | Claude (riesgo R1 del Prompt 0) | Escuchar en 127.0.0.1 no protege de páginas web abiertas en el mismo navegador |
| Auditoría sin evento firmado completo | Claude (incremento 1) | Un evento kind 27235 firmado funciona como credencial mientras su desafío esté vigente |
| Validar el evento antes de pedir aprobación; descartar eventos NIP-46 fuera de ventana; acotar colas | Claude (revisión final) | Hallazgos de la revisión de seguridad |
| Relays por defecto `nos.lol`, `relay.primal.net`, `relay.damus.io` y diagnóstico de eventos en el panel | Claude, a partir del fallo reportado por la persona (22-11) | `relay.nsec.app` no respondía desde la red de prueba; los descartes no dejaban rastro visible |

## Errores del agente detectados durante el trabajo

Se dejan visibles en la transcripción porque muestran cómo se verificó el trabajo:

- Un script de edición convirtió `\n` en saltos de línea reales y borró métodos de `ConsoleApprover` en `signer/main.py`; las pruebas unitarias no lo detectaron porque no importaban el módulo. Se reescribió el archivo y se agregó `tests/test_cli.py`.
- Varias pruebas fallaron por errores de la propia prueba (regex que tomaba el `<meta viewport>`, comprobación incorrecta de `__context__`), no del código; se corrigieron explicando la causa.
- El CSS anulaba el atributo `hidden` y mostraba el campo de importación en el modo "crear"; se detectó al revisar la interfaz en el navegador.
- `requirements.zip` (entorno virtual completo, 9,9 MB) quedó versionado en un commit; se detectó en la revisión final y se sacó del índice.
- La revisión final dio por buenos los relays por defecto sin probar su alcance desde la red de la persona; el login real con la web falló hasta corregirlo.
