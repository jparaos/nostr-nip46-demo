"use strict";

// Panel y vista de solicitudes: consulta periódica (polling) a la API.

const POLL_MS = 1500;
const page = document.body.dataset.page;
let bunkerUrl = "";
let timeoutSeconds = 120;
let failures = 0;
let revealSecret = false;
const busy = new Set(); // solicitudes con una decisión en curso

const $ = (id) => document.getElementById(id);

async function refresh() {
  try {
    const [status, requests] = await Promise.all([api("/api/status"), api("/api/requests")]);
    failures = 0;
    renderStatus(status);
    renderPending(requests.pending);
    if ($("history-body")) renderHistory(requests.history);
  } catch (error) {
    if (error.status === 401) return window.location.replace("/unlock");
    failures += 1;
    setConnection("bad", "Sin conexión con el firmante");
    if (failures === 1) showAlert(error.message);
  }
}

function setConnection(kind, text) {
  $("conn-pill").className = `pill ${kind}`;
  $("conn-text").textContent = text;
}

function renderStatus(status) {
  const signer = status.signer;
  const connected = signer.relays.filter((r) => r.connected).length;
  timeoutSeconds = status.approval_timeout;
  showAlert("");

  if (!signer.running) setConnection("bad", "Firmante detenido");
  else if (connected === 0) setConnection("warn", "Conectando a relays…");
  else setConnection("ok", `Conectado · ${connected}/${signer.relays.length} relays`);

  const badge = $("nav-pending");
  badge.hidden = status.pending === 0;
  badge.textContent = status.pending;
  document.title = (status.pending ? `(${status.pending}) ` : "") + document.title.replace(/^\(\d+\) /, "");

  if (page !== "dashboard") return;
  bunkerUrl = signer.bunker_url || "";
  // El secret se oculta en pantalla (compartir pantalla, capturas); "Copiar" copia la URL completa.
  const shown = revealSecret ? bunkerUrl : bunkerUrl.replace(/secret=[^&]+/, "secret=••••••••");
  $("bunker-url").textContent = shown || "—";
  $("secret-state").textContent = signer.secret_used
    ? "El secret de esta URL ya se usó. Para conectar un cliente nuevo, genera una nueva URL."
    : "Lista para usar: el secret es de un solo uso.";
  $("secret-state").className = signer.secret_used ? "small muted" : "small";
  $("st-vault").textContent = status.vault.unlocked ? "Desbloqueada" : "Bloqueada";
  $("st-signer").textContent = signer.running ? `Activo desde ${formatTime(signer.started_at)}` : "Detenido";
  $("st-npub").textContent = status.npub || "—";
  $("st-activity").replaceChildren(...activityLines(signer.activity));

  $("relay-list").replaceChildren(
    ...signer.relays.map((r) =>
      el("li", {}, el("span", { class: `dot ${r.connected ? "ok" : "bad"}` }), r.url,
        el("span", { class: "muted small",
          text: `${r.connected ? "conectado" : "reintentando"} · ${r.events} eventos` })))
  );
  $("client-list").replaceChildren(
    ...(signer.clients.length
      ? signer.clients.map((c) => el("li", {}, el("code", { text: c })))
      : [el("li", { class: "muted", text: "Ninguno todavía" })])
  );
}

// Diagnóstico: permite saber si las solicitudes del cliente llegan y por qué se descartan.
function activityLines(activity) {
  if (!activity) return ["—"];
  const last = activity.last_event_at
    ? `último evento hace ${Math.max(0, Math.round(Date.now() / 1000 - activity.last_event_at))}s`
    : "aún no llega ningún evento";
  const lines = [el("div", { text: `${activity.received} recibidos · ${activity.answered} respondidos · ${last}` })];
  for (const [reason, count] of Object.entries(activity.dropped || {})) {
    lines.push(el("div", { class: "st-rejected", text: `${count} descartados: ${reason}` }));
  }
  if (activity.unsent_replies) {
    lines.push(el("div", { class: "st-rejected", text: `${activity.unsent_replies} respuestas sin relay conectado` }));
  }
  return lines;
}

function renderPending(pending) {
  const list = $("pending-list");
  if (!pending.length) {
    list.replaceChildren(el("p", { class: "muted", text: "No hay solicitudes pendientes." }));
    return;
  }
  // Se reconstruye solo si cambió el conjunto, para no perder el foco de los botones.
  const ids = pending.map((r) => r.id).join(",");
  if (list.dataset.ids !== ids) {
    list.dataset.ids = ids;
    list.replaceChildren(...pending.map(requestCard));
  }
  for (const req of pending) {
    const left = Math.max(0, Math.round(timeoutSeconds - (Date.now() / 1000 - req.received_at)));
    const counter = document.querySelector(`[data-countdown="${req.id}"]`);
    if (counter) counter.textContent = `se rechaza sola en ${left}s`;
  }
}

function requestCard(req) {
  const approveBtn = el("button", { class: "btn approve", type: "button", text: "APROBAR",
    onclick: () => decide(req.id, "approve") });
  const rejectBtn = el("button", { class: "btn reject", type: "button", text: "RECHAZAR",
    onclick: () => decide(req.id, "reject") });
  return el("article", { class: "request", "data-id": req.id },
    el("div", { class: "request-head" },
      el("span", { class: "request-title", text: `Firma de evento kind ${req.kind}` }),
      el("span", { class: "countdown", "data-countdown": req.id })),
    el("dl", {},
      el("dt", { text: "Identificador" }), el("dd", {}, el("code", { text: req.id })),
      el("dt", { text: "Cliente" }), el("dd", {}, el("code", { text: req.client_short, title: req.client })),
      el("dt", { text: "Método" }), el("dd", { text: req.method }),
      el("dt", { text: "Sitio (tag u)" }), el("dd", { text: siteOf(req) }),
      el("dt", { text: "Kind" }), el("dd", { text: String(req.kind) }),
      el("dt", { text: "Contenido" }), el("dd", {}, el("pre", { text: req.content || "(vacío)" })),
      el("dt", { text: "Tags" }), el("dd", {}, el("pre", { text: JSON.stringify(req.tags, null, 1) })),
      el("dt", { text: "Creado" }), el("dd", { text: formatTime(req.created_at) })),
    el("div", { class: "row" }, approveBtn, rejectBtn));
}

function siteOf(req) {
  const u = (req.tags || []).find((t) => Array.isArray(t) && t[0] === "u");
  return u && typeof u[1] === "string" ? u[1] : "(no indicado)";
}

async function decide(id, action) {
  if (busy.has(id)) return;
  busy.add(id);
  document.querySelectorAll(`[data-id="${id}"] button`).forEach((b) => (b.disabled = true));
  try {
    await api(`/api/requests/${encodeURIComponent(id)}/${action}`, { method: "POST" });
    showAlert(action === "approve" ? "Firma aprobada y enviada." : "Solicitud rechazada: no se firmó nada.", "ok");
  } catch (error) {
    showAlert(error.status === 409 ? "La solicitud ya había expirado o fue resuelta." : error.message);
  } finally {
    busy.delete(id);
    $("pending-list").dataset.ids = "";
    refresh();
  }
}

const STATUS_TEXT = { approved: "Aprobada", rejected: "Rechazada", expired: "Expirada", cancelled: "Cancelada" };

function renderHistory(history) {
  $("history-body").replaceChildren(
    ...(history.length
      ? history.map((r) => el("tr", {},
          el("td", { text: formatTime(r.resolved_at) }),
          el("td", {}, el("code", { text: r.client_short })),
          el("td", { text: String(r.kind) }),
          el("td", { class: `st-${r.status}`, text: STATUS_TEXT[r.status] || r.status })))
      : [el("tr", {}, el("td", { colspan: "4", class: "muted", text: "Sin actividad." }))])
  );
}

$("lock-btn").addEventListener("click", async () => {
  try {
    await api("/api/vault/lock", { method: "POST" });
  } finally {
    window.location.replace("/unlock");
  }
});

if ($("copy-bunker")) $("copy-bunker").addEventListener("click", (e) => bunkerUrl && copyText(bunkerUrl, e.target));
if ($("reveal-secret")) {
  $("reveal-secret").addEventListener("click", (e) => {
    revealSecret = !revealSecret;
    e.target.textContent = revealSecret ? "Ocultar secret" : "Mostrar secret";
    refresh();
  });
}
if ($("restart-btn")) {
  $("restart-btn").addEventListener("click", async () => {
    if (!confirm("Se generará una URL nueva y se desconectarán los clientes autorizados. ¿Continuar?")) return;
    try {
      await api("/api/signer/restart", { method: "POST" });
      showAlert("URL nueva generada.", "ok");
      refresh();
    } catch (error) {
      showAlert(error.message);
    }
  });
}

refresh();
setInterval(refresh, POLL_MS);
