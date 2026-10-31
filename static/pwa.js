// PWA: registra o service worker, mostra o botão "Instalar app" e avisa quando falta o certificado.
(function () {
  const local = ["localhost", "127.0.0.1"].includes(location.hostname);

  function hint(text, linkText) {
    if (document.getElementById("pwa-hint") || sessionStorageGet("pwa-hint-closed")) return;
    const bar = document.createElement("div");
    bar.id = "pwa-hint";
    bar.className = "pwa-hint";
    bar.append(document.createTextNode(text + " "));
    const a = document.createElement("a");
    a.href = "/certificado";
    a.textContent = linkText;
    bar.append(a);
    const close = document.createElement("button");
    close.type = "button";
    close.className = "ghost small";
    close.textContent = "×";
    close.setAttribute("aria-label", "Fechar aviso");
    close.style.marginLeft = "8px";
    close.addEventListener("click", () => { bar.remove(); sessionStorageSet("pwa-hint-closed", "1"); });
    bar.append(close);
    document.body.prepend(bar);
  }
  function sessionStorageGet(k) { try { return sessionStorage.getItem(k); } catch { return null; } }
  function sessionStorageSet(k, v) { try { sessionStorage.setItem(k, v); } catch { /* sem armazenamento */ } }

  if (!("serviceWorker" in navigator) || (!window.isSecureContext && !local)) {
    hint("Para instalar o painel como app, abra-o por HTTPS e instale o certificado do servidor:", "ver como");
  } else {
    navigator.serviceWorker.register("/sw.js").catch(() => {
      // Erro típico: o aparelho ainda não confia no certificado do servidor
      hint("Para instalar o painel como app neste aparelho, instale o certificado do servidor:", "ver como");
    });
  }

  // Botão "Instalar app" (Chrome/Edge/Android; no iPhone é pelo Compartilhar → Tela de Início)
  let deferred = null;
  const button = () => document.getElementById("install-app");
  window.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault();
    deferred = e;
    if (button()) button().hidden = false;
  });
  window.addEventListener("appinstalled", () => { if (button()) button().hidden = true; deferred = null; });
  document.addEventListener("click", async (e) => {
    if (e.target.id !== "install-app" || !deferred) return;
    deferred.prompt();
    await deferred.userChoice;
    deferred = null;
    button().hidden = true;
  });
})();
