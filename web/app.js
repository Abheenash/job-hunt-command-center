// Job Tracker — dashboard.
const CFG = window.JHCC_CONFIG || {};
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (s) => (s == null ? "" : String(s)).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const LS = { id: "jhcc_id", access: "jhcc_access", refresh: "jhcc_refresh", email: "jhcc_email" };

const STATUSES = ["applied", "screen", "interview", "offer", "rejected", "ghosted"];
const US_STATES = ["Remote", "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC"];
const FORM_FIELDS = ["company", "title", "dateApplied", "status", "priority", "location", "state", "workMode",
  "seniority", "salary", "source", "url", "contactName", "contactEmail", "nextAction", "nextDue",
  "tags", "requiredSkills", "niceToHave"];
const fmtDate = (epoch) => { try { return new Date(epoch * 1000).toISOString().slice(0, 10); } catch (_e) { return ""; } };

let APPS = [];
let PROSPECTS = [];
let editing = null, currentDetail = null;
let filterStatus = "all", filterState = "", filterDate = "", filterOpt = false, query = "", sortBy = "updated";

function parseSalary(s) {
  if (!s) return 0;
  let max = 0, m;
  const re = /(\d[\d,]*\.?\d*)\s*([kK])?/g;
  while ((m = re.exec(s))) {
    let n = parseFloat(m[1].replace(/,/g, ""));
    if (m[2]) n *= 1000;
    if (n > max) max = n; // top of the range as the representative figure
  }
  return max;
}
function sortApps(rows) {
  const pay = (dir) => (a, b) => {
    const pa = parseSalary(a.salary), pb = parseSalary(b.salary);
    if (!pa && !pb) return 0;
    if (!pa) return 1; if (!pb) return -1; // apps with no pay sort to the end
    return dir === "high" ? pb - pa : pa - pb;
  };
  const PRIO = { High: 3, Medium: 2, Low: 1 };
  const mp = (a) => (a.matchPercent != null ? a.matchPercent : -1);
  const cmp = {
    updated: (a, b) => (b.updatedAt || 0) - (a.updatedAt || 0),
    added: (a, b) => (b.createdAt || 0) - (a.createdAt || 0),
    company: (a, b) => (a.company || "").localeCompare(b.company || ""),
    company_desc: (a, b) => (b.company || "").localeCompare(a.company || ""),
    pay_high: pay("high"),
    pay_low: pay("low"),
    match: (a, b) => mp(b) - mp(a),
    applied: (a, b) => (b.dateApplied || "").localeCompare(a.dateApplied || ""),
    priority: (a, b) => (PRIO[b.priority] || 0) - (PRIO[a.priority] || 0),
  };
  return rows.slice().sort(cmp[sortBy] || cmp.updated);
}

// ---------- Cognito (no SDK) -----------------------------------------------
async function cognito(target, body) {
  const r = await fetch(`https://cognito-idp.${CFG.region}.amazonaws.com/`, {
    method: "POST",
    headers: { "Content-Type": "application/x-amz-json-1.1", "X-Amz-Target": `AWSCognitoIdentityProviderService.${target}` },
    body: JSON.stringify(body),
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(prettyErr(j.message || j.__type || "auth error"));
  return j;
}
const prettyErr = (m) => String(m).replace(/^.*#/, "").replace(/Exception$/, "");

function storeTokens(a) {
  if (a.IdToken) localStorage.setItem(LS.id, a.IdToken);
  if (a.AccessToken) localStorage.setItem(LS.access, a.AccessToken);
  if (a.RefreshToken) localStorage.setItem(LS.refresh, a.RefreshToken);
}

async function login(email, password) {
  const j = await cognito("InitiateAuth", {
    AuthFlow: "USER_PASSWORD_AUTH", ClientId: CFG.clientId,
    AuthParameters: { USERNAME: email, PASSWORD: password },
  });
  storeTokens(j.AuthenticationResult);
  localStorage.setItem(LS.email, email);
  show(true);
}

async function refresh() {
  const rt = localStorage.getItem(LS.refresh);
  if (!rt) throw new Error("no refresh token");
  const j = await cognito("InitiateAuth", { AuthFlow: "REFRESH_TOKEN_AUTH", ClientId: CFG.clientId, AuthParameters: { REFRESH_TOKEN: rt } });
  storeTokens(j.AuthenticationResult);
}

function logout() { Object.values(LS).forEach((k) => localStorage.removeItem(k)); location.reload(); }

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
  APPS = (await api("GET", "/applications")).applications || [];
  render();
}
function render() {
  renderStats(); renderStateFilter(); renderFilters(); renderList(); renderActivity();
  const el = $("#side-count"); if (el) el.textContent = `${APPS.length} application${APPS.length === 1 ? "" : "s"} tracked`;
  const tc = $("#todo-count"); if (tc) tc.textContent = todoItems().length;
  if (currentView === "todo" && !$("#todo-view").hidden) renderTodo();
}

function renderStats() {
  const n = APPS.length;
  const by = (s) => APPS.filter((a) => a.status === s).length;
  const responded = APPS.filter((a) => ["screen", "interview", "offer", "rejected"].includes(a.status)).length;
  const rate = n ? Math.round((responded / n) * 100) : 0;
  const active = APPS.filter((a) => ["applied", "screen", "interview"].includes(a.status)).length;
  const cards = [["Total", n], ["Active", active], ["Interviews", by("interview")], ["Offers", by("offer")], ["Response rate", rate + "%"]];
  $("#stats").innerHTML = cards.map(([k, v]) => `<div class="stat"><b>${v}</b><span>${k}</span></div>`).join("");
}

function renderStateFilter() {
  const present = [...new Set(APPS.map((a) => a.state).filter(Boolean))].sort();
  const sel = $("#f-state"), cur = sel.value;
  sel.innerHTML = `<option value="">All states</option>` + present.map((s) => `<option ${s === cur ? "selected" : ""}>${esc(s)}</option>`).join("");
}

function renderFilters() {
  const counts = { all: APPS.length };
  STATUSES.forEach((s) => (counts[s] = APPS.filter((a) => a.status === s).length));
  $("#filters").innerHTML = ["all", ...STATUSES].map((k) =>
    `<button class="chip ${filterStatus === k ? "on" : ""}" data-f="${k}">${k}<span>${counts[k] || 0}</span></button>`).join("");
  $$("#filters .chip").forEach((b) => (b.onclick = () => { filterStatus = b.dataset.f; renderFilters(); renderList(); currentDetail = null; showOnly("#list-view"); }));
}

function visible() {
  let rows = APPS.slice();
  if (filterStatus !== "all") rows = rows.filter((a) => a.status === filterStatus);
  if (filterState) rows = rows.filter((a) => a.state === filterState);
  if (filterDate) rows = rows.filter((a) => a.dateApplied === filterDate);
  if (filterOpt) rows = rows.filter((a) => a.sponsors);
  if (query) {
    const q = query.toLowerCase();
    rows = rows.filter((a) => [a.company, a.title, a.location, a.state, a.tags, a.requiredSkills].join(" ").toLowerCase().includes(q));
  }
  return rows;
}

function renderList() {
  const rows = sortApps(visible());
  $("#f-clear").hidden = !(filterState || filterDate || filterOpt);
  $("#empty").hidden = APPS.length !== 0;
  $("#list").innerHTML = rows.map(card).join("");
  $$("#list .card").forEach((c) => (c.onclick = () => openDetail(c.dataset.id)));
}

function ini(a) { return esc((a.company || "?").trim().charAt(0).toUpperCase() || "?"); }

function card(a) {
  const due = a.nextDue ? `<span class="due">⏰ ${esc(a.nextAction || "next")} · ${a.nextDue}</span>` : "";
  const spons = a.sponsors ? `<span class="tag sp">sponsors</span>` : "";
  const st = a.state ? `<span class="tag st">${esc(a.state)}</span>` : "";
  const mt = a.matchPercent != null ? `<span class="tag mt ${a.matchPercent >= 75 ? "good" : a.matchPercent >= 50 ? "ok" : "low"}">${a.matchPercent}% match</span>` : "";
  const pr = a.priority ? `<span class="tag pr ${a.priority.toLowerCase()}">${esc(a.priority)}</span>` : "";
  const tags = (a.tags || "").split(",").map((t) => t.trim()).filter(Boolean).slice(0, 3).map((t) => `<span class="tag">${esc(t)}</span>`).join("");
  return `<article class="card" data-id="${a.appId}">
    <div class="card-h"><span class="card-ico">${ini(a)}</span><b>${esc(a.company || "—")}</b><span class="pill ${a.status}">${a.status}</span></div>
    <div class="role">${esc(a.title || "")}</div>
    <div class="meta">${esc(a.dateApplied || "")}${a.location ? " · " + esc(a.location) : ""}${a.workMode ? " · " + esc(a.workMode) : ""}</div>
    <div class="tags">${pr}${mt}${st}${spons}${tags}</div>${due}</article>`;
}

function renderActivity() {
  const byDate = {};
  APPS.forEach((a) => { if (a.dateApplied) byDate[a.dateApplied] = (byDate[a.dateApplied] || 0) + 1; });
  const dates = Object.keys(byDate).sort().reverse();
  const max = Math.max(1, ...Object.values(byDate));
  $("#activity").innerHTML = `<div class="act-head">📅 Applications by date <span class="filenote">click a date to filter</span></div>` +
    (dates.length ? dates.map((d) =>
      `<div class="act-row ${filterDate === d ? "on" : ""}" data-d="${d}"><span class="act-date">${esc(d)}</span><span class="act-bar"><i style="width:${(byDate[d] / max) * 100}%"></i></span><b class="act-n">${byDate[d]}</b></div>`).join("")
      : `<p class="filenote">No dates yet.</p>`);
  $$("#activity .act-row").forEach((r) => (r.onclick = () => { filterDate = filterDate === r.dataset.d ? "" : r.dataset.d; $("#f-date").value = filterDate; renderActivity(); renderList(); }));
}

// ---------- detail view (portfolio-style) ----------------------------------
function showOnly(sel) { ["#list-view", "#detail-view", "#edit-view", "#todo-view", "#prospects-view"].forEach((s) => ($(s).hidden = s !== sel)); }
function openDetail(id) { const a = APPS.find((x) => x.appId === id); if (!a) return; currentDetail = id; renderDetail(a); showOnly("#detail-view"); window.scrollTo(0, 0); }
function closeDetail() { currentDetail = null; showOnly(currentView === "todo" ? "#todo-view" : "#list-view"); }

let currentView = "all";
function setView(v) {
  currentView = v; currentDetail = null;
  $$("#views .view").forEach((b) => b.classList.toggle("on", b.dataset.v === v));
  if (v === "todo") { renderTodo(); showOnly("#todo-view"); }
  else if (v === "prospects") { renderProspects(); showOnly("#prospects-view"); }
  else { showOnly("#list-view"); }
}

// ---------- follow-ups (next action + due + importance) --------------------
function todoItems() {
  return APPS.filter((a) => a.nextAction || a.nextDue).sort((x, y) => (x.nextDue || "9999").localeCompare(y.nextDue || "9999"));
}
function renderTodo() {
  const rows = todoItems();
  const t = today();
  const body = rows.length ? rows.map((a) => {
    const overdue = a.nextDue && a.nextDue < t;
    const soon = a.nextDue && a.nextDue === t;
    const when = a.nextDue ? (overdue ? `overdue · ${a.nextDue}` : soon ? `due today` : `due ${a.nextDue}`) : "no date";
    const pr = a.priority ? `<span class="tag pr ${a.priority.toLowerCase()}">${esc(a.priority)}</span>` : "";
    return `<div class="todo-row ${overdue ? "over" : soon ? "soon" : ""}" data-id="${a.appId}">
      <span class="todo-when">${esc(when)}</span>
      <div class="todo-main"><b>${esc(a.company || "—")}</b> — ${esc(a.title || "")}<div class="todo-act">${esc(a.nextAction || "Follow up")}</div></div>
      <span class="pill ${a.status}">${a.status}</span>${pr}</div>`;
  }).join("") : `<p class="empty">No follow-ups. Add a <b>Next action + due date</b> to an application to see it here.</p>`;
  $("#todo-view").innerHTML = `<div class="page-head"><div><h1>⏰ Follow-ups</h1><p class="sub">Applications with a next action, soonest first. Overdue in red.</p></div></div>
    <div class="container"><div class="todo-list">${body}</div></div>`;
  $$("#todo-view .todo-row").forEach((r) => (r.onclick = () => openDetail(r.dataset.id)));
}

function kvRow(label, val) { return val ? `<div><span>${label}</span><b>${esc(val)}</b></div>` : ""; }

function renderDetail(a) {
  const now = Math.floor(Date.now() / 1000);
  const days = a.dateApplied ? Math.max(0, Math.round((Date.now() - new Date(a.dateApplied).getTime()) / 86400000)) : null;
  const tagList = (a.tags || "").split(",").map((t) => t.trim()).filter(Boolean);
  const skillsBody = [
    a.requiredSkills ? `<p><strong>Required:</strong> ${esc(a.requiredSkills)}</p>` : "",
    a.niceToHave ? `<p><strong>Nice to have:</strong> ${esc(a.niceToHave)}</p>` : "",
    tagList.length ? `<div class="tags">${tagList.map((t) => `<span class="tag">${esc(t)}</span>`).join("")}</div>` : "",
  ].join("");
  const docs = (a.documents || []);
  const timeline = (a.timeline || []).slice().reverse();

  $("#detail-view").innerHTML = `
    <button class="backlink" id="d-back"><svg class="i"><use href="#ic-back"/></svg> All applications</button>
    <div class="detail-head">
      <span class="card-ico big">${ini(a)}</span>
      <div><div class="lede">${esc(a.title || "")}</div><h1>${esc(a.company || "—")}</h1>
        <span class="pill ${a.status}">${esc(a.status)}</span>${a.state ? ` <span class="tag st">${esc(a.state)}</span>` : ""}${a.sponsors ? ` <span class="tag sp">sponsors OPT</span>` : ""}</div>
      <div class="detail-actions">
        ${a.url ? `<a class="btn" href="${esc(a.url)}" target="_blank" rel="noopener">↗ Posting</a>` : ""}
        <button class="btn" id="d-edit">✎ Edit</button>
        <button class="btn danger" id="d-del">🗑 Delete</button>
      </div>
    </div>
    <div class="detail-grid">
      <div class="detail-main">
        <div class="container"><div class="container-head">🎯 JD ↔ résumé match</div><div class="container-body" id="d-match">${matchInner(a)}</div></div>
        ${skillsBody ? `<div class="container"><div class="container-head">🧩 Skills &amp; tags</div><div class="container-body">${skillsBody}</div></div>` : ""}
        ${a.jd ? `<div class="container"><div class="container-head">📄 Job description</div><div class="container-body"><pre class="jd-text">${esc(a.jd)}</pre></div></div>` : ""}
        <div class="container"><div class="container-head">📎 Documents</div><div class="container-body">
          ${docs.length ? `<div class="doclist">${docs.map((d) => `<a href="#" data-key="${esc(d.docKey)}">📄 ${esc(d.filename || "document")}</a>`).join("")}</div>` : `<p class="muted">No résumé attached. Use Edit to add the one you applied with.</p>`}
        </div></div>
        ${timeline.length ? `<div class="container"><div class="container-head">🕘 Activity</div><div class="container-body"><ul class="timeline">${timeline.map((t) => `<li><span class="tl-date">${t.at ? esc(fmtDate(t.at)) : ""}</span>${esc(t.event || "")}</li>`).join("")}</ul></div></div>` : ""}
      </div>
      <div class="detail-side">
        <div class="container"><div class="container-head">Overview</div><div class="container-body kv">
          ${kvRow("Status", a.status)}
          ${kvRow("Importance", a.priority)}
          ${kvRow("Applied", a.dateApplied + (days != null ? ` (${days}d ago)` : ""))}
          ${kvRow("Location", a.location)}
          ${kvRow("State", a.state)}
          ${kvRow("Work mode", a.workMode)}
          ${kvRow("Seniority", a.seniority)}
          ${kvRow("Salary", a.salary)}
          ${kvRow("Source", a.source)}
          ${kvRow("Sponsors OPT", a.sponsors ? "yes" : "")}
          ${kvRow("Next action", a.nextAction)}
          ${kvRow("Due", a.nextDue)}
        </div></div>
        ${(a.attributes && a.attributes.length) ? `<div class="container"><div class="container-head">Custom fields</div><div class="container-body kv">
          ${a.attributes.map((x) => `<div><span>${esc(x.key)}</span><b>${esc(x.value)}</b></div>`).join("")}
        </div></div>` : ""}
        ${(a.contactName || a.contactEmail) ? `<div class="container"><div class="container-head">Contact</div><div class="container-body kv">
          ${kvRow("Recruiter", a.contactName)}
          ${a.contactEmail ? `<div><span>Email</span><b><a href="mailto:${esc(a.contactEmail)}">${esc(a.contactEmail)}</a></b></div>` : ""}
        </div></div>` : ""}
      </div>
    </div>`;

  $("#d-back").onclick = closeDetail;
  $("#d-edit").onclick = () => openEdit(a.appId);
  $("#d-del").onclick = () => delApp(a.appId);
  const mb = $("#d-match-btn"); if (mb) mb.onclick = () => runMatch(a.appId);
  $$("#detail-view .doclist a").forEach((el) => (el.onclick = async (e) => {
    e.preventDefault();
    const j = await api("GET", "/download?key=" + encodeURIComponent(el.dataset.key));
    window.open(j.downloadUrl, "_blank");
  }));
}

// ---------- JD ↔ résumé match ----------------------------------------------
function matchInner(a) {
  const hasResume = (a.documents || []).length > 0;
  if (a.matchPercent != null && a.matchedAt) return matchResult(a);
  if (!a.jd) return `<p class="muted">Add the job description (via <b>Edit</b>) to run a match check.</p>`;
  if (!hasResume) return `<p class="muted">Attach the résumé you applied with (via <b>Edit</b>) to run a match check.</p>`;
  return `<p class="muted">Compare this JD against your uploaded résumé — AI scores the fit and shows your gaps.</p>
    <button class="btn primary" id="d-match-btn">🎯 Run match check</button> <span id="d-match-msg" class="filenote"></span>`;
}
function matchResult(a) {
  const p = a.matchPercent || 0;
  const cls = p >= 75 ? "good" : p >= 50 ? "ok" : "low";
  const list = (arr, sym) => (arr || []).map((m) => `<li>${sym} ${esc(m)}</li>`).join("");
  const atsOk = (a.atsScore != null && a.atsScore >= 75);
  const ats = a.atsScore != null ? `<div class="filenote" style="margin-top:.4rem">ATS keyword match · <b class="${atsOk ? "ats-ok" : "ats-lo"}">${a.atsScore}%</b> ${atsOk ? "✓" : "(aim ≥75%)"}</div>` : "";
  return `<div class="match ${cls}">
      <div class="match-score"><b>${p}%</b><span>weighted fit</span></div>
      <div class="match-main"><div class="match-bar"><i style="width:${p}%"></i></div><p>${esc(a.matchSummary || "")}</p>${breakdownHtml(a.scoreBreakdown)}${ats}</div>
    </div>
    ${(a.matchMatched || []).length || (a.matchMissing || []).length ? `<div class="match-lists">
      <div class="match-good"><h4>✓ Strengths</h4><ul>${list(a.matchMatched, "")}</ul></div>
      <div class="match-gap"><h4>△ Missing keywords</h4><ul>${list(a.matchMissing, "")}</ul></div>
    </div>` : ""}
    <button class="btn sm" id="d-match-btn">↻ Re-run</button> <span id="d-match-msg" class="filenote"></span>`;
}
async function runMatch(id) {
  const btn = $("#d-match-btn"), msg = $("#d-match-msg");
  if (btn) { btn.disabled = true; btn.textContent = "Analyzing résumé vs JD…"; }
  try {
    await api("POST", `/applications/${id}/match`, {});
    await load();
    const a = APPS.find((x) => x.appId === id); if (a) renderDetail(a);
  } catch (e) { if (msg) msg.textContent = e.message; if (btn) { btn.disabled = false; btn.textContent = "Run match check"; } }
}

// ---------- edit view (inline, page-style) ---------------------------------
function openEdit(id) {
  editing = id || null;
  const a = id ? APPS.find((x) => x.appId === id) : {};
  const f = $("#app-form");
  f.reset();
  $("#edit-title").textContent = id ? "Edit application" : "Log application";
  $("#e-back-label").textContent = id ? "Back to application" : "Back to list";
  $("#form-err").textContent = ""; $("#resume-status").textContent = ""; $("#resume").value = ""; $("#autofill-status").textContent = "";
  $("#jd").value = a.jd || "";
  populateCf(a.attributes);
  lastGen = null; $("#gen-out").hidden = true; $("#gen-out").innerHTML = ""; $("#gen-cover").checked = false;
  FORM_FIELDS.forEach((k) => { if (f[k] != null) f[k].value = a[k] || ""; });
  if (!f.status.value) f.status.value = "applied";
  if (!f.dateApplied.value) f.dateApplied.value = today();
  f.sponsors.checked = !!a.sponsors;
  renderDocs(a.documents || []);
  showOnly("#edit-view"); window.scrollTo(0, 0);
}
function cancelEdit() { const id = editing; editing = null; if (id) openDetail(id); else showOnly("#list-view"); }
const today = () => new Date().toISOString().slice(0, 10);

function renderDocs(docs) {
  $("#docs").innerHTML = docs.length
    ? "<div class='doclist'>" + docs.map((d) => `<a href="#" data-key="${esc(d.docKey)}">📄 ${esc(d.filename || "document")}</a>`).join("") + "</div>" : "";
  $$("#docs a").forEach((el) => (el.onclick = async (e) => {
    e.preventDefault();
    const j = await api("GET", "/download?key=" + encodeURIComponent(el.dataset.key));
    window.open(j.downloadUrl, "_blank");
  }));
}

async function autofill() {
  const jd = $("#jd").value.trim();
  if (jd.length < 20) { $("#autofill-status").textContent = "Paste a longer JD first."; return; }
  $("#autofill").disabled = true; $("#autofill-status").textContent = "✨ Reading the JD…";
  try {
    const { fields, attributes } = await api("POST", "/parse-jd", { jd });
    const f = $("#app-form");
    Object.entries(fields || {}).forEach(([k, v]) => { if (f[k] != null && v) f[k].value = v; });
    mergeCustomFields(attributes);
    $("#autofill-status").textContent = "Filled ✓ — review and save.";
  } catch (e) { $("#autofill-status").textContent = e.message; }
  finally { $("#autofill").disabled = false; }
}

// ---------- résumé generator (JD -> tailored 2-page / 4-project résumé) ------
let lastGen = null;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function generateResume() {
  const jd = $("#jd").value.trim();
  const out = $("#gen-out");
  out.hidden = false;
  if (jd.length < 40) { out.innerHTML = `<p class="err">Paste a fuller job description first (a few lines).</p>`; return; }
  const f = $("#app-form");
  const btn = $("#gen-resume");
  btn.disabled = true;
  const mName = { sonnet: "Sonnet", haiku: "Haiku", opus: "Opus" }[$("#gen-model").value] || "Sonnet";
  out.innerHTML = `<p class="gen-loading">📄 ${mName} is rewriting your résumé for this JD — picking projects, tailoring bullets & skills… <span class="filenote">(~20–45s)</span></p>`;
  try {
    // Opus can exceed the API's 30s cap, so this is async: start a job, then poll.
    const { jobId } = await api("POST", "/generate-resume", {
      coverLetter: $("#gen-cover").checked, jd, template: $("#gen-template").value,
      model: $("#gen-model").value,
      company: f.company.value.trim(), role: f.title.value.trim(),
    });
    if (!jobId) throw new Error("could not start generation");
    let r = null;
    for (let i = 0; i < 40; i++) { // ~40 * 3s = 120s ceiling
      await sleep(3000);
      const s = await api("GET", "/generate-resume?job=" + encodeURIComponent(jobId));
      if (s.status === "ready") { r = s; break; }
      if (s.status === "error") throw new Error(s.error || "generation failed");
    }
    if (!r) throw new Error("generation timed out — try again");
    lastGen = r;
    renderGenOut(r);
    mergeCustomFields(r.customFields);
    if (r.pdfStatus === "compiling") pollPdf(jobId); // reveal the PDF button when ready
  } catch (e) { out.innerHTML = `<p class="err">${esc(e.message)}</p>`; }
  finally { btn.disabled = false; }
}

// Merge AI-suggested custom fields into the editor without clobbering the user's.
function mergeCustomFields(suggested) {
  if (!suggested || !suggested.length) return;
  const have = new Set($$("#cf-rows .cf-row .cf-k").map((i) => i.value.trim().toLowerCase()));
  suggested.forEach((c) => {
    const k = (c.key || "").trim();
    if (k && !have.has(k.toLowerCase())) { $("#cf-rows").appendChild(cfRow(k, c.value || "")); have.add(k.toLowerCase()); }
  });
}

const gchips = (list, cls) => (list && list.length ? list.map((x) => `<span class="gchip ${cls}">${esc(x)}</span>`).join("") : `<span class="filenote">none</span>`);
const atsChips = (list) => (list && list.length ? list.map((x) => `<span class="gchip miss">${esc(x)} <button type="button" class="gchip-add" data-kw="${esc(x)}" title="I have this — add to my skills for future résumés">+</button></span>`).join("") : `<span class="filenote">none</span>`);

function pdfHtml(r) {
  if (r.pdfUrl) return `<a class="btn primary" href="${esc(r.pdfUrl)}" target="_blank" rel="noopener">⬇ Download PDF${r.pages ? ` · ${r.pages}p` : ""}</a>`
    + (r.coverPdfUrl ? ` <a class="btn" href="${esc(r.coverPdfUrl)}" target="_blank" rel="noopener">⬇ Cover PDF</a>` : "");
  if (r.pdfStatus === "compiling") return `<span class="filenote">📄 Compiling PDF…</span>`;
  return `<span class="filenote">Server PDF unavailable — download the .tex and compile locally.</span>`;
}
function updatePdfUi(r) { const el = $("#gen-pdf"); if (el) el.innerHTML = pdfHtml(r); }

function breakdownHtml(bd) {
  if (!bd || !bd.length) return "";
  return `<div class="gen-rubric">` + bd.map((d) =>
    `<div class="rub-row" title="${esc(d.note || "")}"><span class="rub-dim">${esc(d.dimension)} <small>· ${d.weight}%</small></span><span class="rub-bar"><i style="width:${Math.max(3, d.score)}%"></i></span><span class="rub-sc">${d.score}</span></div>`).join("") + `</div>`;
}

function renderGenOut(r) {
  const out = $("#gen-out");
  const projs = (r.selectedProjects || []).map((p) => esc((p.name || "").replace(/\\&/g, "&"))).join(" · ");
  const atsOk = (r.atsScore != null && r.atsScore >= 75);
  const atsLabel = r.atsScore != null ? ` · <b class="${atsOk ? "ats-ok" : "ats-lo"}">${r.atsScore}%</b> ${atsOk ? "✓" : "(aim ≥75%)"}` : "";
  out.innerHTML = `
    <div class="gen-top">
      <div class="gen-score"><b>${r.matchPercent != null ? r.matchPercent + "%" : "—"}</b><span>weighted fit</span></div>
      <div class="gen-meta"><b>Projects picked:</b> ${projs || "—"} ${r.model ? `<span class="gen-badge">${esc(r.model)}</span>` : ""}<div class="filenote">${esc(r.rationale || "")}</div>${breakdownHtml(r.scoreBreakdown)}</div>
    </div>
    <div class="gen-cols">
      <div><div class="gen-h">✓ Matched</div>${gchips(r.matched, "ok")}</div>
      <div><div class="gen-h">⚠ Gaps</div>${gchips(r.gaps, "gap")}</div>
      <div><div class="gen-h">ATS keywords${atsLabel} · “+” if you have it</div>${atsChips(r.atsMissing)}</div>
    </div>
    <div class="gen-actions">
      <span id="gen-pdf">${pdfHtml(r)}</span>
      <button type="button" class="btn" id="gen-dl">⬇ .tex</button>
      <button type="button" class="btn" id="gen-copy">⧉ Copy LaTeX</button>
      ${r.coverLetterLatex ? `<button type="button" class="btn" id="gen-dl-cover">⬇ Cover .tex</button>` : ""}
      <button type="button" class="btn" id="gen-regen" title="Re-run — includes any skills you just confirmed">↻ Regenerate</button>
    </div>
    <details class="gen-src"><summary>Preview LaTeX</summary><pre>${esc(r.resumeLatex || "")}</pre></details>`;
  $("#gen-dl").onclick = () => downloadText("resume-tailored.tex", lastGen.resumeLatex || r.resumeLatex);
  $("#gen-copy").onclick = () => navigator.clipboard.writeText(lastGen.resumeLatex || r.resumeLatex || "").then(() => ($("#gen-copy").textContent = "Copied ✓"));
  if (r.coverLetterLatex) $("#gen-dl-cover").onclick = () => downloadText("cover-letter.tex", lastGen.coverLetterLatex || r.coverLetterLatex);
  $("#gen-regen").onclick = generateResume;
  $$("#gen-out .gchip-add").forEach((b) => (b.onclick = () => addSkill(b.dataset.kw, b)));
}

// Keep polling after results land, to reveal the server-side PDF when it's compiled.
async function pollPdf(jobId) {
  for (let i = 0; i < 70; i++) {
    await sleep(3000);
    let s; try { s = await api("GET", "/generate-resume?job=" + encodeURIComponent(jobId)); } catch (_e) { continue; }
    if (s.pdfStatus === "ready" || s.pdfStatus === "error") { lastGen = s; updatePdfUi(s); break; }
  }
}

// ATS "I have this" -> persist to my confirmed skills so future résumés can use it.
async function addSkill(kw, btn) {
  btn.disabled = true; const old = btn.textContent; btn.textContent = "…";
  try { await api("POST", "/profile-skills", { skill: kw }); btn.textContent = "✓"; btn.title = "Added — future résumés can use it"; }
  catch (_e) { btn.textContent = old; btn.disabled = false; }
}

function downloadText(name, text) {
  const url = URL.createObjectURL(new Blob([text || ""], { type: "text/plain" }));
  const link = document.createElement("a"); link.href = url; link.download = name; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

// ---------- custom fields (dynamic attributes — no code change per new field) -
function cfRow(key = "", value = "") {
  const row = document.createElement("div");
  row.className = "cf-row";
  row.innerHTML = `<input class="cf-k" placeholder="Field (e.g. Clearance)" value="${esc(key)}" />
    <input class="cf-v" placeholder="Value (e.g. TS/SCI)" value="${esc(value)}" />
    <button type="button" class="cf-del" title="Remove field">✕</button>`;
  row.querySelector(".cf-del").onclick = () => row.remove();
  return row;
}
function populateCf(attrs) {
  const box = $("#cf-rows"); box.innerHTML = "";
  (attrs || []).forEach((a) => box.appendChild(cfRow(a.key, a.value)));
}
function collectCf() {
  return $$("#cf-rows .cf-row").map((r) => ({ key: r.querySelector(".cf-k").value.trim(), value: r.querySelector(".cf-v").value.trim() })).filter((a) => a.key);
}

async function saveApp(e) {
  e.preventDefault();
  const f = $("#app-form");
  if (!f.company.value.trim() || !f.title.value.trim() || !f.dateApplied.value) {
    $("#form-err").textContent = "Company, title, and date applied are required."; return;
  }
  const rec = { jd: $("#jd").value.trim() };
  FORM_FIELDS.forEach((k) => (rec[k] = f[k].value.trim ? f[k].value.trim() : f[k].value));
  rec.sponsors = f.sponsors.checked;
  rec.attributes = collectCf();
  if (lastGen) {
    if (lastGen.matchPercent != null) { rec.matchPercent = lastGen.matchPercent; rec.matchedAt = Math.floor(Date.now() / 1000); }
    if (lastGen.snapshotKey) rec.generatedResumeKey = lastGen.snapshotKey;
  }
  $("#save").disabled = true; $("#form-err").textContent = "";
  try {
    let saved = editing ? await api("PUT", "/applications/" + editing, rec) : await api("POST", "/applications", rec);
    const file = $("#resume").files[0];
    if (file) {
      $("#resume-status").textContent = "Uploading résumé…";
      const doc = await uploadDoc(saved.appId, file);
      saved = await api("PUT", "/applications/" + saved.appId, { documents: (saved.documents || []).concat([doc]) });
    } else if (lastGen && lastGen.jobId) {
      // no manual upload but a résumé was generated — attach the compiled PDF directly
      try {
        const { doc } = await api("POST", `/applications/${saved.appId}/attach-generated`, { job: lastGen.jobId });
        if (doc) saved = await api("PUT", "/applications/" + saved.appId, { documents: (saved.documents || []).concat([doc]) });
      } catch (_e) { /* PDF not ready / compile failed — the .tex is still downloadable */ }
    }
    const savedId = saved.appId;
    const autoMatch = !!file && !!rec.jd && rec.jd.length >= 20; // new résumé + a JD -> auto-match
    await load();
    editing = null; openDetail(savedId);
    if (autoMatch) runMatch(savedId); // fire-and-forget; updates the detail when done
  } catch (err) { $("#form-err").textContent = err.message; }
  finally { $("#save").disabled = false; }
}

async function uploadDoc(appId, file) {
  const { uploadUrl, docKey } = await api("POST", `/applications/${appId}/documents`, { filename: file.name, kind: "resume" });
  const put = await fetch(uploadUrl, { method: "PUT", body: file });
  if (!put.ok) throw new Error("résumé upload failed");
  return { docKey, filename: file.name, kind: "resume", at: Math.floor(Date.now() / 1000) };
}

async function delApp(id) {
  if (!confirm("Delete this application? This can't be undone.")) return;
  await api("DELETE", "/applications/" + id);
  closeDetail(); await load();
}

function exportCsv() {
  const cols = ["company", "title", "status", "priority", "dateApplied", "location", "state", "workMode", "seniority",
    "salary", "source", "url", "contactName", "contactEmail", "sponsors", "nextAction", "nextDue", "tags", "requiredSkills"];
  const q = (v) => `"${String(v == null ? "" : v).replace(/"/g, '""')}"`;
  const rows = [cols.join(",")].concat(APPS.map((a) => cols.map((c) => q(a[c])).join(",")));
  const url = URL.createObjectURL(new Blob([rows.join("\n")], { type: "text/csv" }));
  const link = document.createElement("a"); link.href = url; link.download = "job-applications.csv"; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

// ---------- prospects (ingested job feed, surface-only) --------------------
async function loadProspects() {
  try { PROSPECTS = (await api("GET", "/prospects")).prospects || []; } catch (_e) { PROSPECTS = []; }
  const c = $("#prospects-count"); if (c) c.textContent = PROSPECTS.length;
}
async function renderProspects() {
  const el = $("#prospects-view");
  el.innerHTML = `<div class="page-head"><div><h1>🧲 Prospects</h1><p class="sub">Postings pulled from your configured feed. Review and Track — the tool never applies for you.</p></div></div>
    <div id="prospects-list" class="grid projects-grid"></div><p id="prospects-empty" class="empty" hidden></p>`;
  await loadProspects();
  if (!PROSPECTS.length) {
    $("#prospects-empty").hidden = false;
    $("#prospects-empty").innerHTML = `No prospects yet. Set the <b>feed_url</b> Terraform variable to a job feed (JSON or RSS/Atom) and they'll appear here every 12 hours.`;
    return;
  }
  $("#prospects-list").innerHTML = PROSPECTS.map((p) => `<article class="card">
      <h3>${esc(p.title || "(untitled)")}</h3>
      <p class="sub">${esc(p.company || "")}${p.location ? ` · ${esc(p.location)}` : ""}</p>
      <p class="prospect-desc">${esc((p.description || "").slice(0, 220))}${(p.description || "").length > 220 ? "…" : ""}</p>
      <div class="card-actions">
        ${p.url ? `<a class="btn" href="${esc(p.url)}" target="_blank" rel="noopener">↗ View</a>` : ""}
        <button class="btn primary" data-track="${esc(p.id)}">＋ Track</button>
        <button class="btn" data-dismiss="${esc(p.id)}">Dismiss</button>
      </div></article>`).join("");
  $$("#prospects-list [data-track]").forEach((b) => (b.onclick = () => trackProspect(PROSPECTS.find((x) => x.id === b.dataset.track))));
  $$("#prospects-list [data-dismiss]").forEach((b) => (b.onclick = () => dismissProspect(b.dataset.dismiss)));
}
function trackProspect(p) {
  if (!p) return;
  openEdit(null);
  const f = $("#app-form");
  if (p.company) f.company.value = p.company;
  if (p.title) f.title.value = p.title;
  if (p.url) f.url.value = p.url;
  if (p.location) f.location.value = p.location;
  $("#jd").value = p.description || "";
  dismissProspect(p.id, true); // it's an application now — clear it from the queue
}
async function dismissProspect(id, silent) {
  try { await api("DELETE", "/prospects/" + encodeURIComponent(id)); } catch (_e) { /* ignore */ }
  PROSPECTS = PROSPECTS.filter((p) => p.id !== id);
  const c = $("#prospects-count"); if (c) c.textContent = PROSPECTS.length;
  if (!silent && currentView === "prospects") renderProspects();
}

// ---------- conversion analytics -------------------------------------------
const _RANK = { applied: 1, screen: 2, interview: 3, offer: 4, rejected: 1, ghosted: 1 };
function _maxStage(a) {
  // best stage reached: current status, or any stage its timeline mentions (so a
  // now-rejected app that was interviewed still counts toward the interview stage)
  let r = _RANK[a.status] || 1;
  (a.timeline || []).forEach((t) => {
    const e = (t.event || "").toLowerCase();
    if (e.includes("offer")) r = Math.max(r, 4);
    else if (e.includes("interview")) r = Math.max(r, 3);
    else if (e.includes("screen")) r = Math.max(r, 2);
  });
  return r;
}
function renderAnalytics() {
  const el = $("#analytics"); const n = APPS.length;
  if (!n) { el.innerHTML = `<div class="container-head">📊 Analytics</div><div class="container-body"><p class="muted">Log some applications to see analytics.</p></div>`; return; }
  const pct = (a, b) => (b ? Math.round(100 * a / b) : 0);
  const atLeast = (lvl) => APPS.filter((a) => _maxStage(a) >= lvl).length;
  const applied = n, screen = atLeast(2), interview = atLeast(3), offer = atLeast(4);
  const funnel = [["Applied", applied, 100], ["Screen+", screen, pct(screen, applied)], ["Interview+", interview, pct(interview, applied)], ["Offer", offer, pct(offer, applied)]];
  const bySrc = {};
  APPS.forEach((a) => { const s = a.source || "—"; (bySrc[s] = bySrc[s] || { n: 0, resp: 0, intv: 0 }); bySrc[s].n++; if (["screen", "interview", "offer", "rejected"].includes(a.status)) bySrc[s].resp++; if (_maxStage(a) >= 3) bySrc[s].intv++; });
  const scored = APPS.filter((a) => a.matchPercent != null);
  const avg = (arr) => (arr.length ? Math.round(arr.reduce((x, y) => x + y, 0) / arr.length) : null);
  const iAvg = avg(scored.filter((a) => _maxStage(a) >= 3).map((a) => a.matchPercent));
  const nAvg = avg(scored.filter((a) => _maxStage(a) < 3).map((a) => a.matchPercent));
  el.innerHTML = `<div class="container-head">📊 Analytics <span class="filenote">— best stage reached per application</span></div><div class="container-body">
    <div class="an-funnel">${funnel.map(([k, v, p]) => `<div class="an-row"><span class="an-k">${k}</span><span class="an-bar"><i style="width:${Math.max(2, p)}%"></i></span><span class="an-v">${v} · ${p}%</span></div>`).join("")}</div>
    <h4 class="an-h">By source</h4>
    <table class="an-tbl"><tr><th>Source</th><th>Apps</th><th>Response</th><th>Interview</th></tr>
    ${Object.entries(bySrc).sort((a, b) => b[1].n - a[1].n).map(([s, d]) => `<tr><td>${esc(s)}</td><td>${d.n}</td><td>${pct(d.resp, d.n)}%</td><td>${pct(d.intv, d.n)}%</td></tr>`).join("")}</table>
    ${scored.length >= 2 ? `<h4 class="an-h">Résumé match vs. outcome</h4><p class="filenote">Avg match of apps that reached interview: <b>${iAvg != null ? iAvg + "%" : "—"}</b> · that didn't: <b>${nAvg != null ? nAvg + "%" : "—"}</b>${(iAvg != null && nAvg != null && iAvg > nAvg) ? " — higher-match résumés are converting better ✓" : ""}</p>` : ""}
  </div>`;
}

// ---------- settings: change password --------------------------------------
async function changePassword() {
  const msg = $("#pw-msg"); msg.className = "err"; msg.textContent = "";
  try {
    await cognito("ChangePassword", { AccessToken: localStorage.getItem(LS.access), PreviousPassword: $("#pw-old").value, ProposedPassword: $("#pw-new").value });
    msg.className = "ok"; msg.textContent = "Password updated ✓"; $("#pw-old").value = ""; $("#pw-new").value = "";
  } catch (e) { msg.textContent = e.message; }
}

// ---------- notifications (inbox findings) ---------------------------------
let NOTIFS = [];
const NOTIF_SEEN = "jhcc_notif_seen";
const CAT_ICON = { interview: "📅", offer: "🎉", rejection: "🚫", recruiter_reply: "💬", confirmation: "✅" };
async function loadNotifications() { try { NOTIFS = (await api("GET", "/notifications")).notifications || []; } catch (_e) { NOTIFS = []; } renderNotifBadge(); }
function renderNotifBadge() {
  const last = +(localStorage.getItem(NOTIF_SEEN) || 0);
  const unread = NOTIFS.filter((n) => (n.receivedAt || 0) > last).length;
  const b = $("#notif-badge"); if (!b) return;
  if (unread > 0) { b.textContent = unread > 9 ? "9+" : unread; b.hidden = false; } else b.hidden = true;
}
function renderNotifList() {
  const l = $("#notif-list");
  l.innerHTML = NOTIFS.length ? NOTIFS.slice(0, 30).map((n) =>
    `<button class="notif-item" data-app="${esc(n.appId || "")}"><span class="notif-cat">${CAT_ICON[n.category] || "📨"}</span>
      <div><b>${esc(n.subject || "(no subject)")}</b><small>${n.action ? `<b class="notif-act">✓ ${esc(n.action)}</b> · ` : ""}${esc((n.category || "").replace(/_/g, " "))}${n.summary ? " — " + esc(n.summary) : ""}</small></div></button>`).join("")
    : `<p class="notif-empty">No inbox findings yet.<br><span class="filenote">Turn on Gmail scanning and recruiter replies, rejections &amp; interviews will appear here automatically.</span></p>`;
  $$("#notif-list .notif-item").forEach((el) => (el.onclick = () => { const id = el.dataset.app; $("#notif-pop").hidden = true; if (id && id !== "unmatched") openDetail(id); }));
}
function togglePop(sel) { ["#acct-pop", "#notif-pop"].forEach((s) => { if (s !== sel) $(s).hidden = true; }); const p = $(sel); p.hidden = !p.hidden; }

// ---------- Ask AI (natural-language over your applications) ----------------
function aiBubble(role, html) {
  const d = document.createElement("div");
  d.className = "ai-msg " + role;
  d.innerHTML = html;
  $("#ai-log").appendChild(d); $("#ai-log").scrollTop = $("#ai-log").scrollHeight;
  return d;
}
async function askAI(q) {
  q = (q || "").trim(); if (!q) return;
  $("#ai-chips").hidden = true;
  aiBubble("me", esc(q));
  const t = aiBubble("ai", `<span class="ai-typing">thinking…</span>`);
  try {
    const r = await api("POST", "/ask", { question: q });
    const cards = (r.appIds || []).map((id) => APPS.find((a) => a.appId === id)).filter(Boolean);
    t.innerHTML = esc(r.answer || "No answer.") + (cards.length
      ? `<div class="ai-results">${cards.map((a) => `<button class="ai-result" data-id="${a.appId}"><b>${esc(a.company || "—")}</b> <span class="pill ${a.status}">${a.status}</span><span class="ai-rt">${esc(a.title || "")}</span></button>`).join("")}</div>` : "");
    t.querySelectorAll(".ai-result").forEach((el) => (el.onclick = () => { $("#ai-panel").hidden = true; openDetail(el.dataset.id); }));
  } catch (e) { t.innerHTML = esc(e.message || "Something went wrong."); }
  $("#ai-log").scrollTop = $("#ai-log").scrollHeight;
}

// ---------- wiring ---------------------------------------------------------
function fillStateSelects() { $("#state-select").innerHTML = `<option value="">—</option>` + US_STATES.map((s) => `<option>${s}</option>`).join(""); }

function show(authed) {
  $("#login").hidden = authed; $("#app").hidden = !authed;
  if (authed) {
    load().catch((e) => console.error(e));
    loadNotifications();
    loadProspects();
  }
}

$("#login-form").onsubmit = async (e) => {
  e.preventDefault(); $("#login-err").textContent = ""; $("#login-btn").disabled = true;
  try { await login($("#email").value.trim(), $("#password").value); }
  catch (err) { $("#login-err").textContent = err.message; }
  finally { $("#login-btn").disabled = false; }
};
$("#logout").onclick = logout;
$("#pw-open").onclick = () => { $("#acct-pop").hidden = true; $("#settings").hidden = false; $("#pw-msg").textContent = ""; };
$("#settings-close").onclick = () => ($("#settings").hidden = true);
$("#pw-save").onclick = changePassword;
$("#acct-btn").onclick = (e) => { e.stopPropagation(); togglePop("#acct-pop"); };
$("#notif-btn").onclick = (e) => { e.stopPropagation(); renderNotifList(); togglePop("#notif-pop"); };
$("#notif-read").onclick = () => { localStorage.setItem(NOTIF_SEEN, String(Math.floor(Date.now() / 1000))); renderNotifBadge(); };
$$("#views .view").forEach((b) => (b.onclick = () => { setView(b.dataset.v); closeDrawer(); }));
$("#ai-fab").onclick = () => { const p = $("#ai-panel"); p.hidden = !p.hidden; if (!p.hidden) $("#ai-input").focus(); };
$("#ai-close").onclick = () => ($("#ai-panel").hidden = true);
$("#ai-form").onsubmit = (e) => { e.preventDefault(); const q = $("#ai-input").value; $("#ai-input").value = ""; askAI(q); };
$$("#ai-chips .ai-chip").forEach((b) => (b.onclick = () => askAI(b.dataset.q)));
document.addEventListener("click", (e) => { if (!e.target.closest(".pop-wrap")) { $("#acct-pop").hidden = true; $("#notif-pop").hidden = true; } });
const closeDrawer = () => document.body.classList.remove("nav-open");
$("#hamburger").onclick = () => document.body.classList.toggle("nav-open");
$("#nav-backdrop").onclick = closeDrawer;
$("#home-logo").onclick = () => { filterStatus = "all"; filterState = ""; filterDate = ""; filterOpt = false; query = ""; $("#search").value = ""; $("#f-state").value = ""; $("#f-date").value = ""; $("#f-opt").checked = false; render(); setView("all"); closeDrawer(); window.scrollTo(0, 0); };
$("#sidebar").addEventListener("click", (e) => { if (e.target.closest(".chip")) closeDrawer(); });
$("#add").onclick = () => openEdit(null);
$("#cancel").onclick = cancelEdit;
$("#e-back").onclick = cancelEdit;
$("#autofill").onclick = autofill;
$("#gen-resume").onclick = generateResume;
$("#cf-add").onclick = () => $("#cf-rows").appendChild(cfRow());
// remember the model + style choice across generations (so Haiku stays picked for bulk)
["gen-model", "gen-template"].forEach((id) => {
  const el = $("#" + id), k = "jhcc_" + id, saved = localStorage.getItem(k);
  if (el && saved) el.value = saved;
  if (el) el.onchange = () => localStorage.setItem(k, el.value);
});
$("#app-form").onsubmit = saveApp;
$("#export").onclick = exportCsv;
$("#activity-btn").onclick = () => ($("#activity").hidden = !$("#activity").hidden);
$("#analytics-btn").onclick = () => { const a = $("#analytics"); a.hidden = !a.hidden; if (!a.hidden) renderAnalytics(); };
$("#f-state").onchange = (e) => { filterState = e.target.value; renderList(); };
$("#f-date").onchange = (e) => { filterDate = e.target.value; renderActivity(); renderList(); };
$("#f-opt").onchange = (e) => { filterOpt = e.target.checked; renderList(); };
$("#f-clear").onclick = () => { filterState = ""; filterDate = ""; filterOpt = false; $("#f-state").value = ""; $("#f-date").value = ""; $("#f-opt").checked = false; renderActivity(); renderList(); renderStateFilter(); };
$("#search").oninput = (e) => { query = e.target.value; currentDetail = null; showOnly("#list-view"); renderList(); };
$("#sort").onchange = (e) => { sortBy = e.target.value; renderList(); };
document.onkeydown = (e) => {
  if (e.key === "Escape") { if (!$("#edit-view").hidden) cancelEdit(); else if (!$("#detail-view").hidden) closeDetail(); }
  if (e.key === "/" && !$("#app").hidden && !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) { e.preventDefault(); $("#search").focus(); }
};

// boot
fillStateSelects();
if (!CFG.apiBase) { $("#login").hidden = false; $("#login-err").textContent = "config.js not set."; }
else show(!!localStorage.getItem(LS.id));
