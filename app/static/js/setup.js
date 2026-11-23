"use strict";

let mode = "create";
const form = document.getElementById("setup-form");
const secretField = document.getElementById("secret-field");
const submit = document.getElementById("setup-submit");

document.querySelectorAll(".tab").forEach((tab) =>
  tab.addEventListener("click", () => {
    mode = tab.dataset.mode;
    document.querySelectorAll(".tab").forEach((t) => {
      t.classList.toggle("active", t === tab);
      t.setAttribute("aria-selected", String(t === tab));
    });
    secretField.hidden = mode !== "import";
    form.secret.required = mode === "import";
    submit.textContent = mode === "import" ? "Importar y crear bóveda" : "Crear bóveda";
    showAlert("");
  })
);

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  showAlert("");
  if (form.password.value !== form.confirm.value) return showAlert("Las contraseñas no coinciden.");
  submit.disabled = true;
  submit.textContent = "Cifrando…";
  try {
    const body = { password: form.password.value, confirm: form.confirm.value };
    if (mode === "import") body.secret = form.secret.value;
    const result = await api(`/api/vault/${mode === "import" ? "import" : "create"}`, { method: "POST", body });
    form.reset(); // no dejar contraseña ni clave en el DOM
    showResult(result);
  } catch (error) {
    showAlert(error.message);
    submit.disabled = false;
    submit.textContent = mode === "import" ? "Importar y crear bóveda" : "Crear bóveda";
  }
});

function showResult(result) {
  document.getElementById("setup-form-card").hidden = true;
  document.getElementById("result-card").hidden = false;
  document.getElementById("result-npub").textContent = result.npub;
  const goButton = document.getElementById("go-dashboard");

  if (result.nsec_backup) {
    document.getElementById("result-title").textContent = "Bóveda creada";
    document.getElementById("backup").hidden = false;
    const nsec = document.getElementById("result-nsec");
    nsec.textContent = result.nsec_backup;
    const check = document.getElementById("backup-done");
    goButton.disabled = true;
    check.addEventListener("change", () => (goButton.disabled = !check.checked));
    document.getElementById("reveal").addEventListener("click", (e) => {
      nsec.classList.toggle("blurred");
      e.target.textContent = nsec.classList.contains("blurred") ? "Mostrar" : "Ocultar";
    });
    document.getElementById("copy-nsec").addEventListener("click", (e) => copyText(result.nsec_backup, e.target));
  } else {
    document.getElementById("result-title").textContent = "Clave importada";
  }
  goButton.addEventListener("click", () => {
    document.getElementById("result-nsec").textContent = ""; // se descarta del DOM al salir
    window.location.replace("/dashboard"); // replace: el nsec no queda en el historial
  });
}
