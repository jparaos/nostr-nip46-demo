"""Exporta una sesión de Claude Code (JSONL) a Markdown saneado para ai-logs/.

Uso:
    python -m tools.export_ai_log <sesion.jsonl> <salida.md> [--redact TEXTO ...]

`--redact` agrega textos a eliminar (p. ej. contraseñas usadas en demos), para
no tener que escribirlos en este archivo público.

Qué incluye: mensajes de la persona, texto de las respuestas de Claude y una
línea por cada herramienta usada (nombre + descripción/archivo).
Qué excluye: resultados de herramientas (salidas de comandos, contenido de
archivos), razonamiento interno, avisos del sistema y metadatos de la sesión.

Saneamiento (se aplica a todo el texto exportado):
    nsec1… / ncryptsec1…            -> [nsec REDACTADO]
    secret=<hex> en URLs bunker://  -> secret=[REDACTADO]
    contraseñas de prueba y --redact -> [contraseña REDACTADA]
    correos (salvo el institucional de la convocatoria) -> [correo REDACTADO]
    enlaces de videoconferencia     -> [enlace REDACTADO]
    rutas con el usuario de Windows -> <usuario>
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# Contraseñas que ya son públicas en tests/; las de demos se pasan con --redact.
TEST_PASSWORDS = [
    "correcta-horse-battery", "contrasena-segura-1", "contrasena-larga", "incorrecta-999", "incorrecta-123",
]
PUBLIC_EMAILS = {"seleccion@hacienda.gov.cl", "noreply@anthropic.com"}

PATTERNS = [
    (re.compile(r"nsec1[02-9ac-hj-np-z]{20,}"), "[nsec REDACTADO]"),
    (re.compile(r"ncryptsec1[02-9ac-hj-np-z]{20,}"), "[nsec REDACTADO]"),
    (re.compile(r"(secret=)[0-9a-fA-F]{8,}"), r"\1[REDACTADO]"),
    (re.compile(r"https?://meet\.google\.com/\S+"), "[enlace REDACTADO]"),
    (re.compile(r"(?i)(C:[\\/]+Users[\\/]+|/c/Users/)[^\\/\s\"']+"), r"\1<usuario>"),
    (re.compile(r"(?i)(projects[\\/]+)D--[^\\/\s]+"), r"\1<proyecto>"),
]
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
SYSTEM_TAGS = re.compile(r"<(system-reminder|command-[\w-]+|local-command-[\w-]+)[^>]*>.*?</\1>", re.S)
PASTE_TAG = re.compile(r"</?pasted_content[^>]*>")


def sanitize(text: str, extra: list[str] | None = None) -> str:
    for password in sorted(TEST_PASSWORDS + (extra or []), key=len, reverse=True):
        text = text.replace(password, "[contraseña REDACTADA]")
    for pattern, replacement in PATTERNS:
        text = pattern.sub(replacement, text)
    return EMAIL.sub(lambda m: m.group(0) if m.group(0) in PUBLIC_EMAILS else "[correo REDACTADO]", text)


def tool_line(block: dict) -> str:
    data = block.get("input") or {}
    name = block.get("name", "?").replace("mcp__Claude_Browser__", "navegador:").replace("mcp__ccd_turn__", "app:")
    detail = data.get("description") or data.get("file_path") or data.get("url") or data.get("query") or ""
    if name.startswith("app:"):
        return ""  # lista de tareas / cierre de turno de la app: sin valor para el registro
    return f"- 🔧 `{name}` {('— ' + str(detail)) if detail else ''}".rstrip()


def user_text(content) -> str:
    if isinstance(content, str):
        parts = [content]
    else:
        parts = [b.get("text", "") for b in content if b.get("type") == "text"]
        parts += ["[imagen adjunta]" for b in content if b.get("type") == "image"]
    text = PASTE_TAG.sub("", SYSTEM_TAGS.sub("", "\n".join(parts))).strip()
    return text


def export(source: Path, target: Path, extra: list[str] | None = None) -> tuple[int, int]:
    out: list[str] = [
        "# Transcripción saneada — sesión de Claude Code",
        "",
        "Generada con `python -m tools.export_ai_log`. Incluye los mensajes de la persona, el texto de "
        "las respuestas y las herramientas usadas; excluye salidas de herramientas y razonamiento interno. "
        "Secretos y datos personales redactados (ver encabezado de `tools/export_ai_log.py`).",
        "",
    ]
    turns = tools = 0
    pending_tools: list[str] = []

    def flush_tools():
        nonlocal tools
        if pending_tools:
            out.extend(["<details><summary>Herramientas usadas (" + str(len(pending_tools)) + ")</summary>", ""])
            out.extend(pending_tools)
            out.extend(["", "</details>", ""])
            tools += len(pending_tools)
            pending_tools.clear()

    for line in source.read_text(encoding="utf-8").splitlines():
        entry = json.loads(line)
        if entry.get("type") not in ("user", "assistant") or entry.get("isMeta") or entry.get("isSidechain"):
            continue
        content = (entry.get("message") or {}).get("content")
        if entry["type"] == "user":
            if isinstance(content, list) and all(b.get("type") == "tool_result" for b in content):
                continue
            text = user_text(content)
            if not text:
                continue
            flush_tools()
            turns += 1
            stamp = entry.get("timestamp", "")[:16].replace("T", " ")
            out.extend([f"---", "", f"## 🧑 Persona · {stamp}", "", text, ""])
        else:
            for block in content if isinstance(content, list) else []:
                if block.get("type") == "text" and block.get("text", "").strip():
                    flush_tools()
                    out.extend(["### 🤖 Claude", "", block["text"].strip(), ""])
                elif block.get("type") == "tool_use":
                    line_ = tool_line(block)
                    if line_:
                        pending_tools.append(line_)
    flush_tools()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(sanitize("\n".join(out)) + "\n", encoding="utf-8")
    return turns, tools


if __name__ == "__main__":
    args = sys.argv[1:]
    extra = args[args.index("--redact") + 1:] if "--redact" in args else []
    turns, tools = export(Path(args[0]), Path(args[1]), extra)
    print(f"exportados {turns} mensajes de la persona y {tools} usos de herramientas")
