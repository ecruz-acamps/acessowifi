// Mostra os dados do certificado (textContent: nada de innerHTML)
fetch("/certificado/info").then((r) => r.json()).then((c) => {
  if (!c.available) {
    document.getElementById("cert-missing").hidden = false;
    document.getElementById("cert-box").hidden = true;
    return;
  }
  document.getElementById("cert-name").textContent = c.name;
  document.getElementById("cert-until").textContent = new Date(c.not_after).toLocaleDateString("pt-BR");
  document.getElementById("cert-scope").textContent = c.scope.join(", ");
  document.getElementById("cert-fp").textContent = c.fingerprint;
}).catch(() => {});
