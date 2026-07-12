// Job Hunt Command Center — dashboard.
const CFG = window.JHCC_CONFIG || {};
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (s) => (s == null ? "" : String(s)).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const LS = { id: "jhcc_id", refresh: "jhcc_refresh", email: "jhcc_email" };

let APPS = [];
let editing = null; // appId being edited, or null for new
const STATUSES = ["applied", "screen", "interview", "offer", "rejected", "ghosted"];
let filter = "all";
let query = "";

// ---------- Cognito auth (USER_PASSWORD_AUTH, no SDK) -----------------------
async function cognito(target, body) {
  const r = await fetch(`https://cognito-idp.${CFG.region}.amazonaws.com/`, {
    method: "POST",
    headers: { "Content-Type": "application/x-amz-json-1.1", "X-Amz-Target": `AWSCognitoIdentityProviderService.${target}` },
    body: JSON.stringify(body),
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.message || j.__type || "auth error");
  return j;
}

async function login(email, password) {
  const j = await cognito("InitiateAuth", {
    AuthFlow: "USER_PASSWORD_AUTH", ClientId: CFG.clientId,
    AuthParameters: { USERNAME: email, PASSWORD: password },
  });
  const a = j.AuthenticationResult;
  if (!a) throw new Error("Login needs a step this simple client doesn't support.");
  localStorage.setItem(LS.id, a.IdToken);
  if (a.RefreshToken) localStorage.setItem(LS.refresh, a.RefreshToken);
  localStorage.setItem(LS.email, email);
}

async function refresh() {
  const rt = localStorage.getItem(LS.refresh);
  if (!rt) throw new Error("no refresh token");
  const j = await cognito("InitiateAuth", {
    AuthFlow: "REFRESH_TOKEN_AUTH", ClientId: CFG.clientId, AuthParameters: { REFRESH_TOKEN: rt },
  });
  localStorage.setItem(LS.id, j.AuthenticationResult.IdToken);
}

function logout() {
  [LS.id, LS.refresh, LS.email].forEach((k) => localStorage.removeItem(k));
  show(false);
}

// ---------- API ------------------------------------------------------------
async function api(method, path, body, _retried) {
  const r = await fetch(CFG.apiBase + path, {
    method,
    headers: { "Content-Type": "application/json", Authorization: "Bearer " + localStorage.getItem(LS.id) },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (r.status === 401 && !_retried) {
    try { await refresh(); return api(method, path, body, true); }
    catch (_e) { logout(); throw new Error("session expired"); }
  }
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || `request failed (${r.status})`);
  return j;
}

// ---------- load + render --------------------------------------------------
async function load() {
  const j = await api("GET", "/applications");
  APPS = j.applications || [];
  render();
}

function render() {
  renderStats();
  renderFilters();
  renderList();
}

function renderStats() {
  const n = APPS.length;
  const by = (s) => APPS.filter((a) => a.status === s).length;
  const responded = APPS.filter((a) => ["screen", "interview", "offer", "rejected"].includes(a.status)).length;
  const rate = n ? Math.round((responded / n) * 100) : 0;
  const active = APPS.filter((a) => ["applied", "screen", "interview"].includes(a.status)).length;
  const cards = [
    ["Total", n], ["Active", active], ["Interviews", by("interview")],
    ["Offers", by("offer")], ["Response rate", rate + "%"],
  ];
  $("#stats").innerHTML = cards.map(([k, v]) => `<div class="stat"><b>${v}</b><span>${k}</span></div>`).join("");
}

function renderFilters() {
  const counts = { all: APPS.length };
  STATUSES.forEach((s) => (counts[s] = APPS.filter((a) => a.status === s).length));
  const chip = (k) => `<button class="chip ${filter === k ? "on" : ""}" data-f="${k}">${k}<span>${counts[k] || 0}</span></button>`;
  $("#filters").innerHTML = ["all", ...STATUSES].map(chip).join("");
  $$("#filters .chip").forEach((b) => (b.onclick = () => { filter = b.dataset.f; renderList(); renderFilters(); }));
}

function renderList() {
  let rows = APPS.slice();
  if (filter !== "all") rows = rows.filter((a) => a.status === filter);
  if (query) {
    const q = query.toLowerCase();
    rows = rows.filter((a) => [a.company, a.title, a.location, (a.tags || "")].join(" ").toLowerCase().includes(q));
  }
  $("#empty").hidden = APPS.length !== 0;
  $("#list").innerHTML = rows.map(card).join("");
  $$("#list .card").forEach((c) => (c.onclick = () => openModal(c.dataset.id)));
}

function card(a) {
  const due = a.nextDue ? `<span class="due">⏰ ${esc(a.nextAction || "next")} · ${a.nextDue}</span>` : "";
  const spons = a.sponsors ? `<span class="tag sp">sponsors</span>` : "";
  const tags = (a.tags || "").split(",").map((t) => t.trim()).filter(Boolean).slice(0, 4)
    .map((t) => `<span class="tag">${esc(t)}</span>`).join("");
  return `<article class="card" data-id="${a.appId}">
    <div class="card-h"><b>${esc(a.company || "—")}</b><span class="pill ${a.status}">${a.status}</span></div>
    <div class="role">${esc(a.title || "")}</div>
    <div class="meta">${esc(a.location || "")}${a.workMode ? " · " + esc(a.workMode) : ""}${a.source ? " · " + esc(a.source) : ""}</div>
    <div class="tags">${spons}${tags}</div>${due}</article>`;
}

// ---------- modal / editor -------------------------------------------------
function openModal(id) {
  editing = id || null;
  const a = id ? APPS.find((x) => x.appId === id) : {};
  const f = $("#app-form");
  f.reset();
  $("#sheet-title").textContent = id ? "Edit application" : "Log application";
  $("#delete").hidden = !id;
  $("#form-err").textContent = "";
  $("#resume-status").textContent = "";
  $("#resume").value = "";
  ["company", "title", "location", "workMode", "salary", "source", "url", "status",
    "contactName", "contactEmail", "nextAction", "nextDue", "tags", "jd"].forEach((k) => {
    if (f[k] != null) f[k].value = a[k] || (k === "status" ? "applied" : "");
  });
  f.sponsors.checked = !!a.sponsors;
  renderDocs(a.documents || []);
  $("#modal").hidden = false;
}
function closeModal() { $("#modal").hidden = true; editing = null; }

function renderDocs(docs) {
  $("#docs").innerHTML = docs.length
    ? "<div class='doclist'>" + docs.map((d) =>
        `<a href="#" data-key="${esc(d.docKey)}">📄 ${esc(d.filename || d.kind || "document")}</a>`).join("") + "</div>"
    : "";
  $$("#docs a").forEach((el) => (el.onclick = async (e) => {
    e.preventDefault();
    const j = await api("GET", "/download?key=" + encodeURIComponent(el.dataset.key));
    window.open(j.downloadUrl, "_blank");
  }));
}

async function saveApp(e) {
  e.preventDefault();
  const f = $("#app-form");
  const rec = {};
  ["company", "title", "location", "workMode", "salary", "source", "url", "status",
    "contactName", "contactEmail", "nextAction", "nextDue", "tags", "jd"].forEach((k) => (rec[k] = f[k].value.trim()));
  rec.sponsors = f.sponsors.checked;
  $("#save").disabled = true; $("#form-err").textContent = "";
  try {
    let saved = editing
      ? await api("PUT", "/applications/" + editing, rec)
      : await api("POST", "/applications", rec);
    const file = $("#resume").files[0];
    if (file) {
      $("#resume-status").textContent = "Uploading résumé…";
      const doc = await uploadDoc(saved.appId, file);
      const docs = (saved.documents || []).concat([doc]);
      saved = await api("PUT", "/applications/" + saved.appId, { documents: docs });
    }
    closeModal();
    await load();
  } catch (err) {
    $("#form-err").textContent = err.message;
  } finally {
    $("#save").disabled = false;
  }
}

async function uploadDoc(appId, file) {
  const { uploadUrl, docKey } = await api("POST", `/applications/${appId}/documents`, { filename: file.name, kind: "resume" });
  const put = await fetch(uploadUrl, { method: "PUT", body: file });
  if (!put.ok) throw new Error("résumé upload failed");
  return { docKey, filename: file.name, kind: "resume", at: Math.floor(Date.now() / 1000) };
}

async function delApp() {
  if (!editing || !confirm("Delete this application?")) return;
  await api("DELETE", "/applications/" + editing);
  closeModal(); await load();
}

// ---------- CSV export -----------------------------------------------------
function exportCsv() {
  const cols = ["company", "title", "status", "location", "workMode", "salary", "source",
    "url", "contactName", "contactEmail", "sponsors", "nextAction", "nextDue", "tags"];
  const esc2 = (v) => `"${String(v == null ? "" : v).replace(/"/g, '""')}"`;
  const rows = [cols.join(",")].concat(APPS.map((a) => cols.map((c) => esc2(a[c])).join(",")));
  const blob = new Blob([rows.join("\n")], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url; link.download = "job-applications.csv"; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

// ---------- wiring ---------------------------------------------------------
function show(authed) {
  $("#login").hidden = authed;
  $("#app").hidden = !authed;
  if (authed) { $("#who").textContent = localStorage.getItem(LS.email) || ""; load().catch((e) => console.error(e)); }
}

$("#login-form").onsubmit = async (e) => {
  e.preventDefault();
  $("#login-err").textContent = ""; $("#login-btn").disabled = true;
  try { await login($("#email").value.trim(), $("#password").value); show(true); }
  catch (err) { $("#login-err").textContent = err.message; }
  finally { $("#login-btn").disabled = false; }
};
$("#logout").onclick = logout;
$("#add").onclick = () => openModal(null);
$("#cancel").onclick = closeModal;
$("#sheet-close").onclick = closeModal;
$("#delete").onclick = delApp;
$("#app-form").onsubmit = saveApp;
$("#export").onclick = exportCsv;
$("#search").oninput = (e) => { query = e.target.value; renderList(); };
document.onkeydown = (e) => { if (e.key === "/" && $("#app").hidden === false && document.activeElement.tagName !== "INPUT" && document.activeElement.tagName !== "TEXTAREA") { e.preventDefault(); $("#search").focus(); } };

// boot
if (!CFG.apiBase) { $("#login").hidden = false; $("#login-err").textContent = "config.js not set."; }
else show(!!localStorage.getItem(LS.id));
