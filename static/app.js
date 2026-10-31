const REFRESH_MS = 30_000;
const state = { tab: "clients", clients: [], users: [], devices: [], aps: [], groups: [], editing: null, device: null,
  ssid: { ready: false, reason: "", aps: [], ssids: [] }, ssidError: "", editingSsid: null,
  updates: { state: "unavailable", pending: [] } };

const $ = (sel) => document.querySelector(sel);

// Cria elemento com texto seguro (hostname/usuário vêm dos dispositivos — nunca usar innerHTML)
function el(tag, text, cls) {
  const e = document.createElement(tag);
  if (text != null && text !== "") e.textContent = text;
  if (cls) e.className = cls;
  return e;
}

function cell(...children) {
  const td = document.createElement("td");
  for (const c of children) if (c) td.append(typeof c === "string" ? el("div", c) : c);
  return td;
}

function badge(text, kind) { return el("span", text, `badge ${kind || ""}`); }

function duration(sec) {
  if (sec == null) return "–";
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60);
  return h ? `${h}h ${m}min` : `${m}min`;
}

function signalBadge(dbm) {
  if (dbm == null) return badge("–");
  const kind = dbm >= -60 ? "ok" : dbm >= -70 ? "warn" : "bad";
  return badge(`${dbm} dBm`, kind);
}

function fmtDate(iso) {
  if (!iso) return "–";
  return new Date(iso).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
}

// "YYYY-MM-DDTHH:MM" no horário local, para <input type=datetime-local>
function localInput(d) {
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

function endOfDayPlus(days) {
  const d = new Date();
  d.setDate(d.getDate() + days);
  d.setHours(23, 59, 0, 0);
  return localInput(d);
}

function validityBadge(u) {
  if (!u.expiration) return null;
  if (u.expired) return badge("expirado", "bad");
  const hours = (new Date(u.expiration) - Date.now()) / 3.6e6;
  if (hours < 24) return badge("expira hoje", "warn");
  const days = Math.floor(hours / 24);
  return el("small", days === 1 ? "1 dia restante" : `${days} dias restantes`, "muted");
}

function matches(obj, q) {
  if (!q) return true;
  return JSON.stringify(Object.values(obj)).toLowerCase().includes(q);
}

function emptyRow(tbody, cols, msg) {
  const tr = document.createElement("tr");
  const td = el("td", msg, "muted center");
  td.colSpan = cols;
  tr.append(td);
  tbody.append(tr);
}

const SOURCE = {
  registered: ["cadastrado", "ok"],
  google: ["Google Workspace", "info"],
  unknown: ["fora do painel", "warn"],
  pending: ["aguardando login", "neutral"],
  mac: ["liberado por MAC", "ok"],
};

function renderClients(q) {
  const body = $("#clients-body");
  body.replaceChildren();
  const rows = state.clients.filter((c) => matches(c, q));
  if (!rows.length) return emptyRow(body, 8, "Nenhum cliente conectado.");

  for (const c of rows) {
    const tr = document.createElement("tr");
    const [label, kind] = SOURCE[c.source] || [c.source, ""];
    tr.append(
      cell(c.username ? el("div", c.username, "strong") : null, badge(label, kind)),
      cell(c.hostname || "–", el("small", [c.os, c.manufacturer].filter(Boolean).join(" · "), "muted")),
      cell(el("div", c.ip, "mono"), el("small", c.mac, "mono muted"), c.random_mac ? el("small", "MAC aleatório", "muted") : null),
      cell(c.ap, el("small", [c.ssid, c.radio, c.channel && `canal ${c.channel}`].filter(Boolean).join(" · "), "muted")),
      cell(signalBadge(c.signal), c.snr != null ? el("small", `SNR ${c.snr}`, "muted") : null),
      cell(c.rate_mbps ? `${c.rate_mbps} Mbps` : "–"),
      cell(duration(c.connected_for)),
      clientActions(c),
    );
    body.append(tr);
  }
}

function clientActions(c) {
  const td = document.createElement("td");
  td.className = "actions-cell";
  if (c.allowed) td.append(badge("sempre permitido", "ok"));
  else if (c.username) td.append(actionButton("Permitir sempre", "", () => openDeviceDialog(c)));
  return td;
}

function renderDevices(q) {
  const body = $("#devices-body");
  body.replaceChildren();
  const rows = state.devices.filter((d) => matches(d, q))
    .sort((a, b) => b.online - a.online || (a.username || "").localeCompare(b.username || ""));
  if (!rows.length) return emptyRow(body, 6, "Nenhum aparelho liberado. Use \"Permitir sempre\" na aba Conectados agora.");

  for (const d of rows) {
    const tr = document.createElement("tr");
    if (d.expired) tr.className = "dim";
    const actions = document.createElement("td");
    actions.className = "actions-cell";
    actions.append(actionButton("Remover", "danger", () => removeDevice(d)));
    tr.append(
      cell(el("div", d.hostname || "–", "strong")),
      cell(el("div", d.mac, "mono"), d.random_mac ? el("small", "MAC aleatório", "muted") : null),
      cell(d.username),
      cell(fmtDate(d.expiration), el("small", d.follows_account ? "segue a validade da conta" : "validade própria", "muted")),
      cell(d.expired ? badge("vencido", "bad") : d.online ? badge("online", "ok") : badge("offline")),
      actions,
    );
    body.append(tr);
  }
}

function actionButton(text, cls, onClick) {
  const b = el("button", text, `ghost small ${cls || ""}`);
  b.type = "button";
  b.addEventListener("click", onClick);
  return b;
}

function renderUsers(q) {
  const body = $("#users-body");
  body.replaceChildren();
  const showExpired = $("#show-expired").checked;
  const rows = state.users
    .filter((u) => (showExpired || !u.expired) && matches(u, q))
    .sort((a, b) => b.online - a.online || a.expired - b.expired || (a.username || "").localeCompare(b.username || ""));
  if (!rows.length) return emptyRow(body, 6, "Nenhum usuário encontrado.");

  for (const u of rows) {
    const tr = document.createElement("tr");
    if (u.expired) tr.className = "dim";
    const status = !u.enabled ? badge("desativado", "bad")
      : u.expired ? badge("expirado", "bad")
      : u.online ? badge("online", "ok") : badge("offline");

    const actions = document.createElement("td");
    actions.className = "actions-cell";
    if (u.editable) {
      actions.append(
        actionButton("Editar", "", () => openUserDialog(u)),
        actionButton("Remover", "danger", () => removeUser(u)),
      );
    }

    tr.append(
      cell(el("div", u.username, "strong"), u.name ? el("small", u.name, "muted") : null),
      cell(u.company || "–", el("small", u.groups.join(", "), "muted")),
      cell(u.email || "–", u.phone ? el("small", u.phone, "muted") : null),
      cell(fmtDate(u.expiration), validityBadge(u)),
      cell(status, u.devices ? el("small", u.devices === 1 ? "1 aparelho liberado" : `${u.devices} aparelhos liberados`, "muted") : null),
      actions,
    );
    body.append(tr);
  }
}

function renderAps(q) {
  const body = $("#aps-body");
  body.replaceChildren();
  const rows = state.aps.filter((a) => matches(a, q));
  if (!rows.length) return emptyRow(body, 6, "Nenhum AP gerenciado.");

  for (const a of rows) {
    const tr = document.createElement("tr");
    tr.append(
      cell(el("div", a.name, "strong")),
      cell(el("span", a.serial, "mono")),
      cell(el("span", a.ip || "–", "mono")),
      cell(a.version || "–"),
      cell(String(a.clients)),
      cell(badge(a.status, a.status === "connected" ? "ok" : "bad")),
    );
    body.append(tr);
  }
}

const SSID_STATUS = { "no ar": "ok", agendado: "info", encerrado: "neutral", desligado: "neutral" };
const SSID_ACCESS = { portal: "Captive portal", open: "Sem senha", wpa2: "WPA2 com senha", enterprise: "WPA2 Enterprise" };

function renderSsids(q) {
  const body = $("#ssids-body");
  body.replaceChildren();
  const notice = state.ssidError || (!state.ssid.ready && state.ssid.reason);
  $("#ssid-notice-text").textContent = notice || "";
  $("#ssid-notice").hidden = !notice;

  // Escolha do SSID principal na própria aba (quando a instalação não definiu)
  const info = state.ssid.main_info || {};
  const choose = !state.ssidError && state.ssid.can_choose_main && !info.mode;
  $("#ssid-main-choose").hidden = !choose;
  if (choose && !$("#ssid-main-select").options.length) {
    $("#ssid-main-select").replaceChildren(...state.ssid.ssids.filter((x) => !x.managed)
      .map((x) => new Option(x.ssid === x.name ? x.ssid : `${x.ssid} (${x.name})`, x.name)));
  }
  const MODE = {
    bridge: "modo bridge: os SSIDs novos usam a mesma VLAN dele",
    switch: `dentro do software switch "${info.switch}": os SSIDs novos entram nele`,
    tunnel: "modo túnel, fora de software switch: um SSID novo ficaria sem rede",
  };
  $("#ssid-main-info").textContent = info.mode ? `SSID principal: ${info.name}, ${MODE[info.mode]}.` : "";
  const apName = Object.fromEntries(state.ssid.aps.map((a) => [a.id, a.name]));
  const rows = state.ssid.ssids.filter((x) => matches(x, q));
  if (!rows.length) return emptyRow(body, 6, "Nenhum SSID.");

  for (const x of rows) {
    const tr = document.createElement("tr");
    const when = x.mode === "window" ? `${fmtDate(x.start)} até ${fmtDate(x.end)}`
      : x.mode === "on" ? "sempre" : x.mode === "off" ? "–" : x.status;
    const aps = x.aps.length === state.ssid.aps.length && x.aps.length ? "todos"
      : x.aps.map((id) => apName[id] || id).join(", ") || "nenhum";
    const actions = document.createElement("td");
    actions.className = "actions-cell";
    if (x.managed) {
      actions.append(
        x.status === "no ar" ? actionButton("Desligar", "", () => setSsidMode(x, "off"))
          : actionButton("Ligar agora", "", () => setSsidMode(x, "on")),
        actionButton("Editar", "", () => openSsidDialog(x)),
        actionButton("Remover", "danger", () => removeSsid(x)),
      );
    }
    tr.append(
      cell(el("div", x.ssid, "strong"), x.main ? el("small", "SSID principal", "muted") : !x.managed ? el("small", "criado fora do painel", "muted") : null),
      cell(SSID_ACCESS[x.access] || x.access),
      cell(when),
      cell(aps),
      cell(badge(x.status, SSID_STATUS[x.status] || "neutral")),
      actions,
    );
    body.append(tr);
  }
}

function render() {
  const q = $("#search").value.trim().toLowerCase();
  ({ clients: renderClients, users: renderUsers, devices: renderDevices, ssids: renderSsids, aps: renderAps })[state.tab](q);

  const isUsers = state.tab === "users";
  $("#new-user").hidden = !isUsers || !state.groups.length;
  $("#new-ssid").hidden = state.tab !== "ssids";
  $("#new-ssid").disabled = !state.ssid.ready || !!state.ssidError;
  $("#new-ssid").title = $("#new-ssid").disabled ? "Veja o aviso amarelo: o que falta para criar SSIDs" : "";
  $("#show-expired-wrap").hidden = !isUsers;

  $("#stat-online").textContent = state.clients.filter((c) => c.username).length;
  $("#stat-google").textContent = state.clients.filter((c) => c.source === "google").length;
  $("#stat-pending").textContent = state.clients.filter((c) => c.source === "pending").length;
  $("#stat-active").textContent = state.users.filter((u) => u.kind === "guest" && !u.expired).length;
}

async function api(url, options = {}) {
  const r = await fetch(url, {
    ...options,
    headers: { Accept: "application/json", "Content-Type": "application/json", "X-Requested-With": "acessowifi" },
  });
  if (r.status === 401) { location.href = "/login"; throw new Error("sessão expirada"); }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `Erro ${r.status}`);
  return data;
}

function showError(msg) {
  $("#error").textContent = msg;
  $("#error").hidden = !msg;
}

async function load() {
  const btn = $("#refresh");
  btn.disabled = true;
  try {
    [state.clients, state.users, state.devices, state.aps, state.groups] = await Promise.all(
      ["/api/clients", "/api/users", "/api/devices", "/api/aps", "/api/groups"].map((u) => api(u)),
    );
    showError("");
    loadUpdates();
    // SSIDs à parte: sem a permissão nova no token, só esta aba mostra o aviso
    await api("/api/ssids").then((d) => { state.ssid = d; state.ssidError = ""; })
      .catch((e) => { state.ssidError = e.message; });
    $("#updated").textContent = `Atualizado às ${new Date().toLocaleTimeString("pt-BR")}`;
    render();
  } catch (e) {
    showError(e.message);
  } finally {
    btn.disabled = false;
  }
}

// --- Cadastro / edição ---

const dialog = $("#user-dialog");
const form = $("#user-form");
const fe = form.elements;

function openUserDialog(user = null) {
  state.editing = user;
  form.reset();
  $("#form-error").hidden = true;
  const editing = !!user;
  $("#user-dialog-title").textContent = editing ? `Editar ${user.username}` : "Novo usuário";
  $("#user-submit").textContent = editing ? "Salvar" : "Cadastrar";
  fe.username.readOnly = editing;
  dialog.querySelectorAll("[data-create-only]").forEach((e) => (e.hidden = editing));
  dialog.querySelectorAll("[data-edit-only]").forEach((e) => (e.hidden = !editing));
  fe.group.required = !editing;

  fe.group.replaceChildren(...state.groups.map((g) => new Option(g, g)));

  if (editing) {
    for (const f of ["username", "name", "email", "phone", "company"]) fe[f].value = user[f] || "";
    // Expirado: sugere renovar por 7 dias
    fe.expiration.value = user.expired ? endOfDayPlus(7) : user.expiration;
  } else {
    fe.expiration.value = endOfDayPlus(7);
  }
  fe.expiration.min = localInput(new Date());
  dialog.showModal();
  fe.name.focus();
}

// Sugere o login a partir do nome: "João da Silva" -> "joao.silva"
fe.name.addEventListener("input", () => {
  if (state.editing || fe.username.dataset.touched) return;
  const words = fe.name.value.normalize("NFD").replace(/\p{Diacritic}/gu, "")
    .toLowerCase().replace(/[^a-z0-9 ]/g, "").split(/\s+/)
    .filter((w) => w && !["da", "de", "do", "das", "dos", "e"].includes(w));
  fe.username.value = words.length > 1 ? `${words[0]}.${words[words.length - 1]}` : words[0] || "";
});
fe.username.addEventListener("input", () => (fe.username.dataset.touched = "1"));
dialog.addEventListener("close", () => delete fe.username.dataset.touched);

document.querySelectorAll("[data-days]").forEach((b) =>
  b.addEventListener("click", () => (b.form.elements.expiration.value = endOfDayPlus(Number(b.dataset.days)))),
);

form.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const submit = $("#user-submit");
  submit.disabled = true;
  const fields = Object.fromEntries(new FormData(form));
  try {
    let result;
    if (state.editing) {
      const u = state.editing;
      const payload = {
        name: fields.name, email: fields.email, phone: fields.phone, company: fields.company,
        reset_password: !!fields.reset_password,
      };
      if (fields.expiration !== u.expiration) payload.expiration = fields.expiration;
      result = await api(`/api/users/${encodeURIComponent(u.group)}/${u.id}`, { method: "PUT", body: JSON.stringify(payload) });
      result.expiration = fields.expiration;
    } else {
      result = await api("/api/users", { method: "POST", body: JSON.stringify(fields) });
      result.expiration = fields.expiration;
    }
    dialog.close();
    if (result.password) showCredentials(result);
    await load();
  } catch (e) {
    $("#form-error").textContent = e.message;
    $("#form-error").hidden = false;
  } finally {
    submit.disabled = false;
  }
});

async function removeUser(u) {
  const extra = u.devices ? ` Os ${u.devices} aparelho(s) liberado(s) também perdem o acesso.` : "";
  if (!confirm(`Remover o acesso de ${u.name || u.username}?${extra} Isso não pode ser desfeito.`)) return;
  try {
    await api(`/api/users/${encodeURIComponent(u.group)}/${u.id}`, { method: "DELETE" });
    await load();
  } catch (e) {
    showError(e.message);
  }
}

// --- Permitir sempre (liberação por MAC) ---

const deviceDialog = $("#device-dialog");
const deviceForm = $("#device-form");

function accountOf(username) {
  return state.users.find((u) => u.kind === "guest" && u.editable && u.username.toLowerCase() === username.toLowerCase());
}

function openDeviceDialog(c) {
  state.device = c;
  deviceForm.reset();
  $("#device-error").hidden = true;
  $("#device-host").textContent = [c.hostname, c.os].filter(Boolean).join(" · ") || "–";
  $("#device-mac").textContent = c.mac;
  $("#device-user").textContent = c.username;
  $("#device-random").hidden = !c.random_mac;

  const account = accountOf(c.username);
  const exp = deviceForm.elements.expiration;
  $("#device-exp-wrap").hidden = !!account;
  exp.required = !account;
  $("#device-follows").textContent = account
    ? `Segue a validade da conta de ${account.username} (até ${fmtDate(account.expiration)}). ` +
      "Se a conta for renovada, o aparelho continua liberado; se vencer ou for removida, a liberação sai junto."
    : "Este usuário não tem conta cadastrada no painel (ex.: Google Workspace). Escolha até quando liberar.";
  exp.value = endOfDayPlus(30);
  exp.min = localInput(new Date());
  deviceDialog.showModal();
}

deviceForm.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const c = state.device;
  const submit = $("#device-submit");
  submit.disabled = true;
  try {
    const payload = { mac: c.mac, username: c.username, hostname: c.hostname };
    if (!accountOf(c.username)) payload.expiration = deviceForm.elements.expiration.value;
    await api("/api/devices", { method: "POST", body: JSON.stringify(payload) });
    deviceDialog.close();
    await load();
  } catch (e) {
    $("#device-error").textContent = e.message;
    $("#device-error").hidden = false;
  } finally {
    submit.disabled = false;
  }
});

async function removeDevice(d) {
  if (!confirm(`Remover a liberação de ${d.hostname || d.mac}? O aparelho volta a passar pelo captive portal.`)) return;
  try {
    await api(`/api/devices/${encodeURIComponent(d.mac)}`, { method: "DELETE" });
    await load();
  } catch (e) {
    showError(e.message);
  }
}

// --- SSIDs ---

const ssidDialog = $("#ssid-dialog");
const ssidForm = $("#ssid-form");
const sf = ssidForm.elements;

function syncWindowFields() {
  const isWindow = sf.mode.value === "window";
  ssidForm.querySelector("[data-window]").hidden = !isWindow;
  sf.start.required = sf.end.required = isWindow;
  const sec = sf.security.value;
  ssidForm.querySelector("[data-wpa2]").hidden = sec !== "wpa2";
  // Na edição de um SSID que já é WPA2, a senha em branco mantém a atual
  const keeps = state.editingSsid?.access === "wpa2";
  sf.passphrase.required = sec === "wpa2" && !keeps;
  sf.passphrase.placeholder = keeps ? "deixe em branco para manter a senha atual" : "";
  $("#ssid-open-warning").hidden = sec !== "open";
}
ssidForm.querySelectorAll("input[name=mode], input[name=security]").forEach((r) => r.addEventListener("change", syncWindowFields));

function openSsidDialog(x = null) {
  state.editingSsid = x;
  ssidForm.reset();
  $("#ssid-error").hidden = true;
  $("#ssid-dialog-title").textContent = x ? `Editar ${x.ssid}` : "Novo SSID";
  $("#ssid-submit").textContent = x ? "Salvar" : "Criar SSID";
  const box = $("#ssid-aps");
  box.replaceChildren(...state.ssid.aps.map((a) => {
    const label = el("label", null, "check");
    const input = document.createElement("input");
    input.type = "checkbox"; input.name = "aps"; input.value = a.id;
    input.checked = x ? x.aps.includes(a.id) : true;
    label.append(input, document.createTextNode(` ${a.name}`));
    return label;
  }));
  sf.ssid.value = x ? x.ssid : "";
  const mode = x && ["on", "off", "window"].includes(x.mode) ? x.mode : "window";
  ssidForm.querySelector(`input[name=mode][value=${mode}]`).checked = true;
  const access = !x || x.access === "portal" ? "same" : x.access === "open" ? "open" : "wpa2";
  ssidForm.querySelector(`input[name=security][value=${access}]`).checked = true;
  const start = new Date(); start.setMinutes(0, 0, 0); start.setHours(start.getHours() + 1);
  sf.start.value = x?.start || localInput(start);
  sf.end.value = x?.end || endOfDayPlus(2);
  syncWindowFields();
  ssidDialog.showModal();
  sf.ssid.focus();
}

ssidForm.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const x = state.editingSsid;
  const payload = {
    ssid: sf.ssid.value.trim(),
    mode: sf.mode.value,
    aps: [...ssidForm.querySelectorAll("input[name=aps]:checked")].map((i) => i.value),
    security: sf.security.value,
  };
  if (payload.security === "wpa2" && sf.passphrase.value) payload.passphrase = sf.passphrase.value;
  if (payload.mode === "window") Object.assign(payload, { start: sf.start.value, end: sf.end.value });
  const submit = $("#ssid-submit");
  submit.disabled = true;
  try {
    if (x) await api(`/api/ssids/${encodeURIComponent(x.name)}`, { method: "PUT", body: JSON.stringify(payload) });
    else await api("/api/ssids", { method: "POST", body: JSON.stringify(payload) });
    ssidDialog.close();
    await load();
  } catch (e) {
    $("#ssid-error").textContent = e.message;
    $("#ssid-error").hidden = false;
  } finally {
    submit.disabled = false;
  }
});

async function setSsidMode(x, mode) {
  try {
    await api(`/api/ssids/${encodeURIComponent(x.name)}`, { method: "PUT", body: JSON.stringify({ mode }) });
    await load();
  } catch (e) {
    showError(e.message);
  }
}

async function removeSsid(x) {
  if (!confirm(`Remover o SSID "${x.ssid}"? Ele sai do ar em todos os FortiAPs.`)) return;
  try {
    await api(`/api/ssids/${encodeURIComponent(x.name)}`, { method: "DELETE" });
    await load();
  } catch (e) {
    showError(e.message);
  }
}

$("#new-ssid").addEventListener("click", () => openSsidDialog());
$("#ssid-main-save").addEventListener("click", async () => {
  try {
    await api("/api/ssids-main", { method: "PUT", body: JSON.stringify({ name: $("#ssid-main-select").value }) });
    $("#ssid-main-select").replaceChildren();
    await load();
  } catch (e) {
    $("#ssid-notice-text").textContent = e.message;
  }
});

// --- Atualizações do painel ---

const updatesDialog = $("#updates-dialog");
let updatesPoll = null;

async function loadUpdates() {
  try {
    state.updates = await api("/api/updates");
  } catch {
    return;   // painel reiniciando durante a instalação: tenta no próximo ciclo
  }
  renderUpdates();
}

function renderUpdates() {
  const u = state.updates;
  if (u.installed_version) $("#app-version").textContent = `v${u.installed_version}`;
  // Conta versões (1.1, 1.2...); sem número de versão nos commits, conta os commits
  const versions = u.pending_versions || [];
  const n = versions.length || (u.pending || []).length;
  $("#updates-count").textContent = n > 9 ? "9+" : String(n);
  $("#updates-count").hidden = n === 0;
  $("#updates-btn").title = n ? `${n} versão(ões) nova(s) disponível(is)` : "Atualizações do painel";
  if (!updatesDialog.open) return;

  const busy = u.state === "installing" || u.state === "checking";
  $("#updates-message").textContent = u.message || "";
  $("#updates-current").textContent = u.installed_version ? `v${u.installed_version}` : (u.current?.hash || "–");
  const list = $("#updates-list");
  if (versions.length) {
    list.replaceChildren(...versions.map((v) => {
      const li = el("li", null);
      li.append(el("strong", `Versão ${v.version}`), el("small", v.date || ""));
      const ul = document.createElement("ul");
      ul.append(...v.changes.map((c) => el("li", c)));
      li.append(ul);
      return li;
    }));
  } else {
    list.replaceChildren(...(u.pending || []).map((c) => {
      const li = el("li", c.subject);
      li.append(el("small", `${c.hash} · ${c.date ? new Date(c.date).toLocaleString("pt-BR") : ""}`));
      return li;
    }));
  }
  const showLog = (u.state === "installing" || u.state === "failed" || u.state === "ok") && (u.log || []).length;
  $("#updates-log").textContent = showLog ? u.log.join("\n") : "";
  $("#updates-log").hidden = !showLog;
  $("#updates-install").hidden = !n;
  $("#updates-install").disabled = busy || u.state === "unavailable";
  $("#updates-install").textContent = busy && u.state === "installing" ? "Instalando…"
    : u.target_version && versions.length ? `Instalar a versão ${u.target_version}`
    : n === 1 ? "Instalar 1 atualização" : `Instalar ${n} atualizações`;
  $("#updates-check").disabled = busy || u.state === "unavailable";
}

function pollUpdates(until) {
  clearInterval(updatesPoll);
  const started = Date.now();
  updatesPoll = setInterval(async () => {
    await loadUpdates();
    if (until(state.updates) || Date.now() - started > 180_000) {
      clearInterval(updatesPoll);
      if (state.updates.state === "ok") setTimeout(() => location.reload(), 1500);   // carrega a versão nova
    }
  }, 3000);
}

$("#updates-btn").addEventListener("click", async () => {
  updatesDialog.showModal();
  renderUpdates();
  await loadUpdates();
});

$("#updates-check").addEventListener("click", async () => {
  const before = state.updates.checked_at;
  try {
    await api("/api/updates/check", { method: "POST" });
    state.updates = { ...state.updates, state: "checking", message: "Consultando o GitHub…" };
    renderUpdates();
    pollUpdates((u) => u.checked_at !== before && u.state !== "checking");
  } catch (e) {
    $("#updates-message").textContent = e.message;
  }
});

$("#updates-install").addEventListener("click", async () => {
  const target = state.updates.target_version;
  if (!confirm(`Instalar ${target ? `a versão ${target}` : "a atualização"}? O painel fica fora do ar por alguns segundos.`)) return;
  const before = state.updates.checked_at;
  try {
    await api("/api/updates/install", { method: "POST" });
    state.updates = { ...state.updates, state: "installing", message: "Instalando a atualização…", log: [] };
    renderUpdates();
    // espera um status mais novo que o do clique (o anterior pode ser um "ok" antigo)
    pollUpdates((u) => u.checked_at !== before && (u.state === "ok" || u.state === "failed"));
  } catch (e) {
    $("#updates-message").textContent = e.message;
  }
});

const credDialog = $("#cred-dialog");

function showCredentials({ username, password, expiration }) {
  $("#cred-user").textContent = username;
  $("#cred-pass").textContent = password;
  $("#cred-exp").textContent = fmtDate(expiration);
  credDialog.showModal();
}

$("#cred-copy").addEventListener("click", async () => {
  const text = `Wi-Fi\nUsuário: ${$("#cred-user").textContent}\nSenha: ${$("#cred-pass").textContent}\n` +
    `Válido até: ${$("#cred-exp").textContent}`;
  try {
    await navigator.clipboard.writeText(text);
    $("#cred-copy").textContent = "Copiado!";
  } catch {
    $("#cred-copy").textContent = "Copie manualmente";
  }
  setTimeout(() => ($("#cred-copy").textContent = "Copiar"), 2000);
});

document.querySelectorAll("dialog [data-close]").forEach((b) =>
  b.addEventListener("click", () => b.closest("dialog").close()),
);

// --- Navegação ---

document.querySelectorAll("[role=tab]").forEach((b) =>
  b.addEventListener("click", () => {
    state.tab = b.dataset.tab;
    document.querySelectorAll("[role=tab]").forEach((x) => x.setAttribute("aria-selected", x === b));
    document.querySelectorAll("[data-panel]").forEach((p) => (p.hidden = p.dataset.panel !== state.tab));
    render();
  }),
);
$("#search").addEventListener("input", render);
$("#show-expired").addEventListener("change", render);
$("#refresh").addEventListener("click", load);
$("#new-user").addEventListener("click", () => openUserDialog());

load();
setInterval(() => { if (!document.hidden && !dialog.open && !credDialog.open && !deviceDialog.open && !ssidDialog.open) load(); }, REFRESH_MS);
