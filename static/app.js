const REFRESH_MS = 30_000;
const state = { tab: "clients", clients: [], users: [], aps: [] };

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

function renderClients(q) {
  const body = $("#clients-body");
  body.replaceChildren();
  const rows = state.clients.filter((c) => matches(c, q));
  if (!rows.length) return emptyRow(body, 8, "Nenhum cliente conectado.");

  for (const c of rows) {
    const tr = document.createElement("tr");
    const user = c.username
      ? el("div", c.username, "strong")
      : badge("sem usuário", "warn");
    const userInfo = c.username && !c.registered ? badge("não cadastrado", "bad") : el("small", c.auth_method, "muted");
    tr.append(
      cell(user, userInfo),
      cell(c.hostname || "–", el("small", [c.os, c.manufacturer].filter(Boolean).join(" · "), "muted")),
      cell(el("div", c.ip, "mono"), el("small", c.mac, "mono muted")),
      cell(c.ssid, c.vlan != null ? el("small", `VLAN ${c.vlan}`, "muted") : null),
      cell(c.ap, el("small", [c.radio, c.channel && `canal ${c.channel}`].filter(Boolean).join(" · "), "muted")),
      cell(signalBadge(c.signal), c.snr != null ? el("small", `SNR ${c.snr}`, "muted") : null),
      cell(c.rate_mbps ? `${c.rate_mbps} Mbps` : "–"),
      cell(duration(c.connected_for)),
    );
    body.append(tr);
  }
}

function renderUsers(q) {
  const body = $("#users-body");
  body.replaceChildren();
  const rows = state.users.filter((u) => matches(u, q))
    .sort((a, b) => b.online - a.online || a.username.localeCompare(b.username));
  if (!rows.length) return emptyRow(body, 6, "Nenhum usuário encontrado.");

  const authLabel = { password: "Local", radius: "RADIUS", ldap: "LDAP", tacacs: "TACACS+", guest: "Visitante" };
  for (const u of rows) {
    const tr = document.createElement("tr");
    const status = !u.enabled ? badge("desativado", "bad") : u.online ? badge("online", "ok") : badge("offline");
    tr.append(
      cell(el("div", u.username, "strong"), u.name ? el("small", u.name, "muted") : null),
      cell(authLabel[u.auth] || u.auth, u.two_factor !== "disable" ? el("small", `2FA: ${u.two_factor}`, "muted") : null),
      cell(u.groups.join(", ") || "–"),
      cell(u.email || "–", u.phone ? el("small", u.phone, "muted") : null, u.company ? el("small", u.company, "muted") : null),
      cell(u.expiration || "–"),
      cell(status),
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

function render() {
  const q = $("#search").value.trim().toLowerCase();
  ({ clients: renderClients, users: renderUsers, aps: renderAps })[state.tab](q);

  $("#stat-online").textContent = state.clients.length;
  $("#stat-users").textContent = state.users.length;
  $("#stat-unknown").textContent = state.clients.filter((c) => !c.username).length;
  $("#stat-aps").textContent = state.aps.filter((a) => a.status === "connected").length;
}

async function getJSON(url) {
  const r = await fetch(url, { headers: { Accept: "application/json" } });
  if (r.status === 401) { location.href = "/login"; throw new Error("sessão expirada"); }
  const data = await r.json();
  if (!r.ok) throw new Error(data.error || `Erro ${r.status}`);
  return data;
}

async function load() {
  const btn = $("#refresh");
  btn.disabled = true;
  try {
    [state.clients, state.users, state.aps] = await Promise.all(
      ["/api/clients", "/api/users", "/api/aps"].map(getJSON),
    );
    $("#error").hidden = true;
    $("#updated").textContent = `Atualizado às ${new Date().toLocaleTimeString("pt-BR")}`;
    render();
  } catch (e) {
    $("#error").textContent = e.message;
    $("#error").hidden = false;
  } finally {
    btn.disabled = false;
  }
}

document.querySelectorAll("[role=tab]").forEach((b) =>
  b.addEventListener("click", () => {
    state.tab = b.dataset.tab;
    document.querySelectorAll("[role=tab]").forEach((x) => x.setAttribute("aria-selected", x === b));
    document.querySelectorAll("[data-panel]").forEach((p) => (p.hidden = p.dataset.panel !== state.tab));
    render();
  }),
);
$("#search").addEventListener("input", render);
$("#refresh").addEventListener("click", load);

load();
setInterval(() => { if (!document.hidden) load(); }, REFRESH_MS);
