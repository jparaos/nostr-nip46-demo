"use strict";

// Utilidades compartidas. Nunca se usa innerHTML con datos del servidor:
// el contenido y los tags de las solicitudes vienen de clientes remotos (no confiables).

const CSRF = document.querySelector('meta[name="csrf-token"]').content;

async function api(path, { method = "GET", body } = {}) {
  const options = { method, headers: {}, credentials: "same-origin" };
  if (method !== "GET") {
    options.headers["X-CSRF-Token"] = CSRF;
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body || {});
  }
  let response;
  try {
    response = await fetch(path, options);
  } catch {
    throw new ApiError(0, "No se pudo contactar al firmante local. ¿Sigue en ejecución?");
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new ApiError(response.status, data.detail || `Error ${response.status}`);
  return data;
}

class ApiError extends Error {
  constructor(status, message) {
    super(typeof message === "string" ? message : "Datos inválidos");
    this.status = status;
  }
}

function showAlert(message, kind = "error") {
  const box = document.getElementById("alert");
  box.textContent = message;
  box.className = kind === "ok" ? "alert ok" : "alert";
  box.hidden = !message;
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value);
  }
  for (const child of children) {
    if (child == null) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

async function copyText(text, button) {
  try {
    await navigator.clipboard.writeText(text);
    const label = button.textContent;
    button.textContent = "Copiado ✓";
    setTimeout(() => (button.textContent = label), 1500);
  } catch {
    showAlert("No se pudo copiar automáticamente; selecciona el texto y cópialo.");
  }
}

function formatTime(seconds) {
  if (!seconds) return "—";
  return new Date(seconds * 1000).toLocaleString();
}
