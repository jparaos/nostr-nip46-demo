"use strict";

const form = document.getElementById("unlock-form");

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  showAlert("");
  const button = form.querySelector("button");
  button.disabled = true;
  button.textContent = "Verificando…";
  try {
    await api("/api/vault/unlock", { method: "POST", body: { password: form.password.value } });
    window.location.replace("/dashboard");
  } catch (error) {
    form.password.value = "";
    form.password.focus();
    showAlert(error.status === 401 ? "Contraseña incorrecta. La bóveda sigue bloqueada." : error.message);
    button.disabled = false;
    button.textContent = "Desbloquear";
  }
});
