// Job Tracker — dashboard.
const CFG = window.JHCC_CONFIG || {};
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (s) => (s == null ? "" : String(s)).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const LS = { id: "jhcc_id", access: "jhcc_access", refresh: "jhcc_refresh", email: "jhcc_email" };

const STATUSES = ["applied", "screen", "interview", "offer", "rejected", "ghosted"];
const US_STATES = ["Remote", "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC"];
const FORM_FIELDS = ["company", "title", "dateApplied", "status", "priority", "location", "state", "workMode",
  "seniority", "salary", "source", "url", "nextAction", "nextDue", "tags", "requiredSkills", "niceToHave"];
// Local YYYY-MM-DD (viewer's own timezone) — NOT toISOString(), which is UTC and rolls the
// date over in the evening for US viewers.
const _ymd = (d) => { const p = (n) => String(n).padStart(2, "0"); return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`; };
const fmtDate = (epoch) => { try { return _ymd(new Date(epoch * 1000)); } catch (_e) { return ""; } };

let APPS = [];
let editing = null, currentDetail = null;
let filterStatus = "all", filterState = "", filterDate = "", filterOpt = false, filterReach = "", query = "", sortBy = "updated";

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
  if (filterReach) rows = rows.filter(matchesReach);
  if (query) {
    const q = query.toLowerCase();
    rows = rows.filter((a) => [a.company, a.title, a.location, a.state, a.tags, a.requiredSkills].join(" ").toLowerCase().includes(q));
  }
  return rows;
}

function renderList() {
  const rows = sortApps(visible());
  $("#f-clear").hidden = !(filterState || filterDate || filterOpt || filterReach);
  $("#empty").hidden = APPS.length !== 0;
  $("#list").innerHTML = rows.map(card).join("");
  $$("#list .card").forEach((c) => (c.onclick = () => openDetail(c.dataset.id)));
}

function ini(a) { return esc((a.company || "?").trim().charAt(0).toUpperCase() || "?"); }

function card(a) {
  const due = a.nextDue ? `<span class="due">⏰ ${esc(a.nextAction || "next")} · ${a.nextDue}</span>` : "";
  const rod = reachDue(a); const roOver = reachOverdue(a);
  const roDue = rod.length ? `<span class="due ro ${roOver ? "over" : ""}">📣 ${roOver ? "reach-out overdue" : rod.length + " reach-out" + (rod.length > 1 ? "s" : "") + " due"}</span>` : "";
  const spons = a.sponsorVerdict ? sponBadge(a) : (a.sponsors ? `<span class="tag sp">sponsors</span>` : "");
  const ref = a.referralStatus === "Referral secured" ? `<span class="tag rf">★ referral</span>` : (a.referralStatus === "Reached out" ? `<span class="tag rf out">↗ outreach</span>` : "");
  const st = a.state ? `<span class="tag st">${esc(a.state)}</span>` : "";
  const mt = a.matchPercent != null ? `<span class="tag mt ${a.matchPercent >= 75 ? "good" : a.matchPercent >= 50 ? "ok" : "low"}">${a.matchPercent}% match</span>` : "";
  const pr = a.priority ? `<span class="tag pr ${a.priority.toLowerCase()}">${esc(a.priority)}</span>` : "";
  const tags = (a.tags || "").split(",").map((t) => t.trim()).filter(Boolean).slice(0, 3).map((t) => `<span class="tag">${esc(t)}</span>`).join("");
  return `<article class="card" data-id="${a.appId}">
    <div class="card-h"><span class="card-ico">${ini(a)}</span><b>${esc(a.company || "—")}</b><span class="pill ${a.status}">${a.status}</span></div>
    <div class="role">${esc(a.title || "")}</div>
    <div class="meta">${esc(a.dateApplied || "")}${a.location ? " · " + esc(a.location) : ""}${a.workMode ? " · " + esc(a.workMode) : ""}</div>
    <div class="tags">${pr}${mt}${st}${spons}${ref}${tags}</div>${due}${roDue}</article>`;
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
function showOnly(sel) { ["#list-view", "#detail-view", "#edit-view", "#todo-view", "#inbox-view", "#openings-view"].forEach((s) => ($(s).hidden = s !== sel)); }
function openDetail(id) { const a = APPS.find((x) => x.appId === id); if (!a) return; currentDetail = id; renderDetail(a); showOnly("#detail-view"); window.scrollTo(0, 0); }
function closeDetail() { currentDetail = null; showOnly(currentView === "todo" ? "#todo-view" : "#list-view"); }

let currentView = "all";
function setView(v) {
  currentView = v; currentDetail = null;
  $$("#views .view").forEach((b) => b.classList.toggle("on", b.dataset.v === v));
  if (v === "todo") { renderTodo(); showOnly("#todo-view"); }
  else if (v === "inbox") { renderInbox(); showOnly("#inbox-view"); }
  else if (v === "openings") { renderOpenings(); showOnly("#openings-view"); }
  else { showOnly("#list-view"); }
}

// ---------- follow-ups (next action + due + importance) --------------------
function todoItems() {
  const tasks = [];
  APPS.forEach((a) => {
    getReachOuts(a).forEach((r) => { if (r.due) tasks.push({ a, when: r.due, kind: "reachout", label: r.name ? `Reach out to ${r.name}` : "Reach out" }); });
    if (a.nextAction || a.nextDue) tasks.push({ a, when: a.nextDue || "", kind: "followup", label: a.nextAction || "Follow up" });
  });
  return tasks.sort((x, y) => (x.when || "9999").localeCompare(y.when || "9999"));
}
function renderTodo() {
  const rows = todoItems();
  const t = today();
  const body = rows.length ? rows.map((r) => {
    const a = r.a;
    const overdue = r.when && r.when < t;
    const soon = r.when && r.when === t;
    const when = r.when ? (overdue ? `overdue · ${r.when}` : soon ? `due today` : `due ${r.when}`) : "no date";
    const pr = a.priority ? `<span class="tag pr ${a.priority.toLowerCase()}">${esc(a.priority)}</span>` : "";
    const icon = r.kind === "reachout" ? "📣" : "⏰";
    return `<div class="todo-row ${overdue ? "over" : soon ? "soon" : ""}" data-id="${a.appId}">
      <span class="todo-when">${esc(when)}</span>
      <div class="todo-main"><b>${esc(a.company || "—")}</b> — ${esc(a.title || "")}<div class="todo-act">${icon} ${esc(r.label)}</div></div>
      <span class="pill ${a.status}">${a.status}</span>${pr}</div>`;
  }).join("") : `<p class="empty">Nothing due. Add a <b>reach-out date</b> or a <b>next action + due date</b> to an application to see it here.</p>`;
  $("#todo-view").innerHTML = `<div class="page-head"><div><h1>⏰ Follow-ups &amp; reach-outs</h1><p class="sub">Reach-outs (📣) and follow-ups (⏰), soonest first. Overdue in red — clear these before applying to anything new.</p></div></div>
    <div class="container"><div class="todo-list">${body}</div></div>`;
  $$("#todo-view .todo-row").forEach((r) => (r.onclick = () => openDetail(r.dataset.id)));
}

function kvRow(label, val) { return val ? `<div><span>${label}</span><b>${esc(val)}</b></div>` : ""; }

// ---------- reach out (referral / outreach per application) -----------------
// A reach-out is a person to contact for a referral: { name, link, email, msg, due }.
// Stored as an array on the application so you can add as many people as you want.
function getReachOuts(a) {
  if (Array.isArray(a.reachOuts)) return a.reachOuts;
  // migrate a legacy single-contact application into one entry
  if (a.contactName || a.contactLink || a.contactEmail || a.reachOutMsg || a.reachOutDue) {
    return [{ name: a.contactName || "", link: a.contactLink || "", email: a.contactEmail || "", msg: a.reachOutMsg || "", due: a.reachOutDue || "" }];
  }
  return [];
}
function reachDue(a) { return getReachOuts(a).filter((r) => r.due); }
function reachOverdue(a) { const t = today(); return getReachOuts(a).some((r) => r.due && r.due < t); }
function matchesReach(a) {
  const ros = getReachOuts(a);
  switch (filterReach) {
    case "has": return ros.length > 0;                    // reached out / contacts logged
    case "none": return ros.length === 0;                 // no one added yet
    case "due": return reachDue(a).length > 0;            // someone has a due date
    case "overdue": return reachOverdue(a);               // a due date has passed
    default: return true;
  }
}
function reachOutCard(a) {
  const t = today();
  const ros = getReachOuts(a);
  if (!ros.length) {
    return `<div class="container ro-card"><div class="container-head">📣 Reach out</div><div class="container-body">
      <p class="muted">No one added yet. A referral converts far better than a cold app — add the people to contact for this role.</p>
      <button class="btn primary" id="ro-edit">＋ Add people</button></div></div>`;
  }
  const items = ros.map((r, i) => {
    const overdue = r.due && r.due < t, soon = r.due && r.due === t;
    const dueTxt = r.due ? (overdue ? `⚠ overdue · ${r.due}` : soon ? `due today` : `by ${r.due}`) : "";
    const links = [
      r.link ? `<a class="btn sm" href="${esc(r.link)}" target="_blank" rel="noopener">Open profile</a>` : "",
      r.email ? `<a class="btn sm" href="mailto:${esc(r.email)}">✉ Email</a>` : "",
    ].filter(Boolean).join(" ");
    return `<div class="ro-item ${overdue ? "over" : soon ? "soon" : ""}">
      <div class="ro-who">${r.name ? `<b>${esc(r.name)}</b>` : `<span class="muted">(no name)</span>`}${dueTxt ? ` <span class="ro-due ${overdue ? "over" : soon ? "soon" : ""}">${esc(dueTxt)}</span>` : ""}</div>
      ${links ? `<div class="ro-links">${links}</div>` : ""}
      ${r.msg ? `<pre class="ro-msg">${esc(r.msg)}</pre><div class="ro-actions"><button class="btn sm ro-copy" data-i="${i}">Copy message</button></div>` : ""}
    </div>`;
  }).join("");
  return `<div class="container ro-card"><div class="container-head">📣 Reach out <span class="ro-count">${ros.length}</span></div>
    <div class="container-body">${items}
      <div class="ro-actions" style="margin-top:.6rem"><button class="btn sm" id="ro-edit">✎ Edit people</button></div>
    </div></div>`;
}

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
        <span class="pill ${a.status}">${esc(a.status)}</span>${a.state ? ` <span class="tag st">${esc(a.state)}</span>` : ""}${a.sponsorVerdict ? " " + sponBadge(a) : (a.sponsors ? ` <span class="tag sp">sponsors OPT</span>` : "")}</div>
      <div class="detail-actions">
        ${a.url ? `<a class="btn" href="${esc(a.url)}" target="_blank" rel="noopener">Posting</a>` : ""}
        <button class="btn" id="d-edit">✎ Edit</button>
        <button class="btn danger" id="d-del">🗑 Delete</button>
      </div>
    </div>
    <div class="detail-grid">
      <div class="detail-main">
        <div class="container"><div class="container-head">🛂 Visa sponsorship</div><div class="container-body" id="d-spon">${sponInner(a)}</div></div>
        <div class="container"><div class="container-head">🎯 JD ↔ résumé match</div><div class="container-body" id="d-match">${matchInner(a)}</div></div>
        <div class="container"><div class="container-head">🎤 Interview prep</div><div class="container-body" id="d-prep">${prepInner(a)}</div></div>
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
        ${reachOutCard(a)}
      </div>
    </div>`;

  const ros = getReachOuts(a);
  $$("#detail-view .ro-copy").forEach((b) => (b.onclick = () => {
    const msg = (ros[+b.dataset.i] || {}).msg || "";
    const w = navigator.clipboard && navigator.clipboard.writeText ? navigator.clipboard.writeText(msg) : Promise.reject();
    w.then(() => { b.textContent = "Copied ✓"; setTimeout(() => (b.textContent = "Copy message"), 1200); }).catch(() => window.prompt("Copy:", msg));
  }));
  const roEdit = $("#ro-edit"); if (roEdit) roEdit.onclick = () => openEdit(a.appId);

  $("#d-back").onclick = closeDetail;
  $("#d-edit").onclick = () => openEdit(a.appId);
  $("#d-del").onclick = () => delApp(a.appId);
  const mb = $("#d-match-btn"); if (mb) mb.onclick = () => runMatch(a.appId);
  const sb = $("#d-spon-btn"); if (sb) sb.onclick = () => runSponsor(a.appId);
  const pb = $("#d-prep-btn"); if (pb) pb.onclick = () => runPrep(a.appId);
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

// ---------- visa sponsorship -----------------------------------------------
const SPON_CLASS = { likely: "sp-good", possible: "sp-ok", capexempt: "sp-exempt", caution: "sp-warn", rare: "sp-warn", none: "sp-none", unlikely: "sp-bad", unknown: "sp-none" };
const SPON_ICON = { likely: "✓", possible: "≈", capexempt: "★", caution: "⚠", rare: "⚠", none: "∅", unlikely: "✗", unknown: "?" };

function sponLinksFor(company) {
  const q = encodeURIComponent((company || "").trim());
  const slug = (company || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  return { h1bdata: `https://h1bdata.info/index.php?em=${q}`, myvisajobs: `https://www.myvisajobs.com/search/?q=${q}`, h1bgrader: `https://h1bgrader.com/search?q=${q}&slug=${slug}` };
}
function sponBadge(a) {
  if (!a.sponsorVerdict) return "";
  const lvl = a.sponsorVerdict;
  return `<span class="tag spon ${SPON_CLASS[lvl] || "sp-none"}" title="${esc(a.sponsorLabel || "")}">${SPON_ICON[lvl] || "?"} ${esc(a.sponsorLabel || lvl)}</span>`;
}
function h1bBlock(h) {
  if (!h || !h.count) return `<p class="filenote">No H-1B filings found for this exact name — try the parent-company name.</p>`;
  const wage = h.medianTechSalary || h.medianSalary;
  return `<div class="h1b-grid">
      <div><b>${h.count}${h.capped ? "+" : ""}</b><span>LCA filings${h.recentYear ? " · " + h.recentYear : ""}</span></div>
      <div><b>${h.techCount || 0}</b><span>tech / eng roles</span></div>
      ${wage ? `<div><b>$${Math.round(wage / 1000)}k</b><span>median wage</span></div>` : ""}
    </div>
    ${(h.topTitles && h.topTitles.length) ? `<div class="h1b-titles">Top sponsored: ${h.topTitles.map((t) => `<span class="tag">${esc(t[0])} · ${t[1]}</span>`).join(" ")}</div>` : ""}
    ${h.employer ? `<div class="filenote">Matched: ${esc(h.employer)}${h.matchedVia ? ` (searched “${esc(h.matchedVia)}”)` : ""}</div>` : ""}`;
}
function sponVerdictHtml(v, company, capExempt, label, level) {
  const cls = SPON_CLASS[level] || "sp-none";
  const L = sponLinksFor(company);
  return `<div class="spon-verdict ${cls}"><span class="spon-badge">${SPON_ICON[level] || "?"}</span>
      <div><b>${esc(label || level)}</b>${capExempt ? `<span class="spon-note">lottery-proof</span>` : ""}</div></div>
    <ul class="spon-reasons">${(v.reasons || []).map((r) => `<li>${esc(r)}</li>`).join("")}</ul>
    ${h1bBlock(v.h1b)}
    <div class="spon-verify">Dig deeper: <a href="${esc(L.h1bdata)}" target="_blank" rel="noopener">h1bdata</a> · <a href="${esc(L.myvisajobs)}" target="_blank" rel="noopener">myvisajobs</a> · <a href="${esc(L.h1bgrader)}" target="_blank" rel="noopener">h1bgrader</a></div>
    <p class="spon-disc">A signal, not a guarantee — sponsorship varies by team & role. Confirm on the posting.</p>`;
}
function sponInner(a) {
  if (a.sponsorVerdict) {
    return sponVerdictHtml({ reasons: a.sponsorReasons, h1b: a.sponsorH1b }, a.company, a.sponsorCapExempt, a.sponsorLabel, a.sponsorVerdict)
      + `<button class="btn sm" id="d-spon-btn">↻ Re-check</button> <span id="d-spon-msg" class="filenote"></span>`;
  }
  if (!a.company) return `<p class="muted">Add the company name (via <b>Edit</b>) to check visa sponsorship.</p>`;
  return `<p class="muted">Scan this employer's H-1B track record and the JD's sponsorship language — no more bouncing between h1bdata / myvisajobs.</p>
    <button class="btn primary" id="d-spon-btn">🛂 Check sponsorship</button> <span id="d-spon-msg" class="filenote"></span>`;
}
async function runSponsor(id) {
  const btn = $("#d-spon-btn"), msg = $("#d-spon-msg");
  if (btn) { btn.disabled = true; btn.textContent = "Checking H-1B records…"; }
  try {
    await api("POST", "/sponsorship", { appId: id });
    await load();
    const a = APPS.find((x) => x.appId === id); if (a) renderDetail(a);
  } catch (e) { if (msg) msg.textContent = e.message; if (btn) { btn.disabled = false; btn.textContent = "Check sponsorship"; } }
}

// Quick check from the editor (no appId — ephemeral; ticks the sponsors box).
async function checkSponsorEdit() {
  const company = $("#app-form").company.value.trim();
  const jd = $("#jd").value.trim();
  const out = $("#spon-out"); out.hidden = false;
  if (!company) { out.innerHTML = `<p class="err">Enter the company name first (or ✨ Autofill from the JD).</p>`; return; }
  const btn = $("#check-spon"); btn.disabled = true;
  out.innerHTML = `<p class="gen-loading">🛂 Checking ${esc(company)}'s H-1B track record…</p>`;
  try {
    const { sponsorship: s } = await api("POST", "/sponsorship", { company, jd });
    out.innerHTML = sponVerdictHtml(s, company, s.capExempt, s.label, s.level)
      + (s.sponsors ? `<p class="filenote">✓ Ticked “Sponsors / accepts OPT” — save to keep it on this application.</p>` : "");
    if (s.sponsors) $("#app-form").sponsors.checked = true;
  } catch (e) { out.innerHTML = `<p class="err">${esc(e.message)}</p>`; }
  finally { btn.disabled = false; }
}

// ---------- interview prep --------------------------------------------------
function prepInner(a) {
  if (a.interviewPrep && a.interviewPrepAt) return prepResult(a);
  if (!a.jd) return `<p class="muted">Add the job description (via <b>Edit</b>) to generate tailored interview prep.</p>`;
  const hasR = (a.documents || []).length > 0;
  return `<p class="muted">Turn this JD${hasR ? " + your résumé" : ""} into likely questions, talking points from your background, and sharp questions to ask them.</p>
    <button class="btn primary" id="d-prep-btn">🎤 Generate interview prep</button> <span id="d-prep-msg" class="filenote"></span>`;
}
function prepResult(a) {
  const p = a.interviewPrep || {};
  const qs = (arr, sub) => (arr || []).map((x) => `<li><b>${esc(x.q)}</b>${x[sub] ? `<span class="prep-hint">${esc(x[sub])}</span>` : ""}</li>`).join("");
  const plain = (arr) => (arr || []).map((x) => `<li>${esc(x)}</li>`).join("");
  const sec = (title, body, cls) => body ? `<div class="prep-sec ${cls || ""}"><h4>${title}</h4><ul>${body}</ul></div>` : "";
  return sec("💻 Technical", qs(p.technical, "hint"))
    + sec("🗣️ Behavioral", qs(p.behavioral, "angle"))
    + sec("⭐ Your talking points", plain(p.talkingPoints), "prep-good")
    + sec("△ Likely gaps to prep", plain(p.gaps), "prep-gap")
    + sec("❓ Ask them", plain(p.askThem))
    + `<button class="btn sm" id="d-prep-btn">↻ Regenerate</button> <span id="d-prep-msg" class="filenote"></span>`;
}
async function runPrep(id) {
  const btn = $("#d-prep-btn"), msg = $("#d-prep-msg");
  if (btn) { btn.disabled = true; btn.textContent = "Coaching… (~15–30s)"; }
  try {
    await api("POST", `/applications/${id}/interview-prep`, {});
    await load();
    const a = APPS.find((x) => x.appId === id); if (a) renderDetail(a);
  } catch (e) { if (msg) msg.textContent = e.message; if (btn) { btn.disabled = false; btn.textContent = "Generate interview prep"; } }
}

// ---------- edit view (inline, page-style) ---------------------------------
function openEdit(id, prefill) {
  editing = id || null;
  pendingOpeningId = (!id && prefill && prefill._openingId) || null;
  const a = id ? APPS.find((x) => x.appId === id) : (prefill || {});
  const f = $("#app-form");
  f.reset();
  $("#edit-title").textContent = id ? "Edit application" : "Log application";
  $("#e-back-label").textContent = id ? "Back to application" : "Back to list";
  $("#form-err").textContent = ""; $("#resume-status").textContent = ""; $("#resume").value = ""; $("#autofill-status").textContent = "";
  $("#spon-out").hidden = true; $("#spon-out").innerHTML = "";
  $("#jd").value = a.jd || "";
  populateCf(a.attributes);
  populateRo(getReachOuts(a));
  lastGen = null; $("#gen-out").hidden = true; $("#gen-out").innerHTML = ""; $("#gen-cover").checked = false;
  FORM_FIELDS.forEach((k) => { if (f[k] != null) f[k].value = a[k] || ""; });
  if (!f.status.value) f.status.value = "applied";
  if (!f.dateApplied.value) f.dateApplied.value = today();
  f.sponsors.checked = !!a.sponsors;
  renderDocs(a.documents || []);
  showOnly("#edit-view"); window.scrollTo(0, 0);
}
function cancelEdit() { const id = editing; editing = null; pendingOpeningId = null; if (id) openDetail(id); else showOnly("#list-view"); }
const today = () => _ymd(new Date());  // local date (viewer's timezone), not UTC

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
      coverLetter: $("#gen-cover").checked, jd, model: $("#gen-model").value,
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

// ---------- reach-out people (repeatable: name, LinkedIn, email, message, due) --
function roRow(e = {}) {
  const row = document.createElement("div");
  row.className = "ro-row";
  row.innerHTML = `
    <input class="ro-name" placeholder="Name" value="${esc(e.name || "")}" />
    <input class="ro-link" type="url" placeholder="LinkedIn profile URL" value="${esc(e.link || "")}" />
    <input class="ro-email" type="email" placeholder="Email" value="${esc(e.email || "")}" />
    <input class="ro-date" type="date" title="Reach out by" value="${esc(e.due || "")}" />
    <button type="button" class="ro-del" title="Remove person">✕</button>
    <textarea class="ro-msg-in" rows="3" placeholder="Reach-out message…">${esc(e.msg || "")}</textarea>`;
  row.querySelector(".ro-del").onclick = () => row.remove();
  return row;
}
function populateRo(list) { const box = $("#ro-rows"); box.innerHTML = ""; (list || []).forEach((e) => box.appendChild(roRow(e))); }
function collectRo() {
  return $$("#ro-rows .ro-row").map((r) => ({
    name: r.querySelector(".ro-name").value.trim(),
    link: r.querySelector(".ro-link").value.trim(),
    email: r.querySelector(".ro-email").value.trim(),
    due: r.querySelector(".ro-date").value,
    msg: r.querySelector(".ro-msg-in").value.trim(),
  })).filter((e) => e.name || e.link || e.email || e.msg || e.due);
}

async function saveApp(e) {
  e.preventDefault();
  const f = $("#app-form");
  if (!f.company.value.trim() || !f.title.value.trim() || !f.dateApplied.value) {
    $("#form-err").textContent = "Company, title, and date applied are required."; return;
  }
  if (!editing) { // warn on a likely duplicate (same company + title already tracked)
    const nc = f.company.value.trim().toLowerCase(), nt = f.title.value.trim().toLowerCase();
    const dup = APPS.find((a) => (a.company || "").trim().toLowerCase() === nc && (a.title || "").trim().toLowerCase() === nt);
    if (dup && !confirm(`You already logged “${dup.company} — ${dup.title}” (${dup.dateApplied || "no date"}). Log another anyway?`)) return;
  }
  const rec = { jd: $("#jd").value.trim() };
  FORM_FIELDS.forEach((k) => (rec[k] = f[k].value.trim ? f[k].value.trim() : f[k].value));
  rec.sponsors = f.sponsors.checked;
  rec.attributes = collectCf();
  rec.reachOuts = collectRo();
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
    if (pendingOpeningId) { // logged from an opening -> drop it from the radar + flag it tracked
      const oid = pendingOpeningId;
      OPENINGS = OPENINGS.filter((o) => o.id !== oid);
      const c = $("#openings-count"); if (c) c.textContent = OPENINGS.length;
      api("POST", `/openings/${encodeURIComponent(oid)}/track`, { appId: savedId }).catch(() => {});
    }
    pendingOpeningId = null;
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
    "salary", "source", "url", "contactName", "contactEmail", "referredBy", "referralStatus", "sponsors", "sponsorVerdict",
    "nextAction", "nextDue", "tags", "requiredSkills"];
  const q = (v) => `"${String(v == null ? "" : v).replace(/"/g, '""')}"`;
  const rows = [cols.join(",")].concat(APPS.map((a) => cols.map((c) => q(a[c])).join(",")));
  const url = URL.createObjectURL(new Blob([rows.join("\n")], { type: "text/csv" }));
  const link = document.createElement("a"); link.href = url; link.download = "job-applications.csv"; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
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
async function loadNotifications() {
  try { NOTIFS = (await api("GET", "/notifications")).notifications || []; } catch (_e) { NOTIFS = []; }
  renderNotifBadge();
  const ic = $("#inbox-count"); if (ic) ic.textContent = NOTIFS.length;
  if (currentView === "inbox" && !$("#inbox-view").hidden) renderInbox();
}
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

// ---------- Inbox view (full-page list of job-related emails) ---------------
function inboxRow(n) {
  const app = (n.appId && n.appId !== "unmatched") ? APPS.find((a) => a.appId === n.appId) : null;
  const when = n.receivedAt ? fmtDate(n.receivedAt) : "";
  const linked = app ? `<span class="inbox-app">🔗 ${esc(app.company || "application")}</span>` : `<span class="inbox-unlinked">unlinked</span>`;
  return `<article class="inbox-item${app ? " clickable" : ""}" data-app="${esc(n.appId || "")}">
      <span class="inbox-cat">${CAT_ICON[n.category] || "📨"}</span>
      <div class="inbox-body">
        <div class="inbox-top"><b>${esc(n.subject || "(no subject)")}</b><span class="inbox-when">${esc(when)}</span></div>
        <div class="inbox-meta"><span class="inbox-badge">${esc((n.category || "").replace(/_/g, " "))}</span> ${linked}${n.action ? ` · <b class="notif-act">✓ ${esc(n.action)}</b>` : ""}</div>
        ${n.summary ? `<p class="inbox-summary">${esc(n.summary)}</p>` : ""}
        <div class="inbox-from">${esc(n.from || "")}</div>
      </div>
    </article>`;
}
function renderInbox() {
  const el = $("#inbox-view");
  el.innerHTML = `<div class="page-head"><div><h1>📨 Inbox</h1><p class="sub">Job-related emails your scanner classified and linked to applications — recruiter replies, interviews, rejections, offers, confirmations. Click one to open its application.</p></div></div>
    ${NOTIFS.length ? `<div class="inbox-list">${NOTIFS.map(inboxRow).join("")}</div>`
      : `<p class="empty">No job-related emails yet. Once your inbox scanner runs (or a recruiter / ATS emails you), interviews, rejections and confirmations show up here — automatically linked to the right application.</p>`}`;
  $$("#inbox-view .inbox-item.clickable").forEach((r) => (r.onclick = () => openDetail(r.dataset.app)));
  localStorage.setItem(NOTIF_SEEN, String(Math.floor(Date.now() / 1000))); // opening the tab clears the bell
  renderNotifBadge();
}
function togglePop(sel) { ["#acct-pop", "#notif-pop"].forEach((s) => { if (s !== sel) $(s).hidden = true; }); const p = $(sel); p.hidden = !p.hidden; }

// ---------- Openings Radar (scanned, ranked job discovery) ------------------
let OPENINGS = [];
let pendingOpeningId = null; // set when the editor was opened from an opening -> track on save
const NEW_WINDOW = 36 * 3600;   // "new" if first seen within ~a scan cycle
const SOON_WINDOW = 2 * 86400;  // "leaving soon" if it ages out within 2 days
function relAgo(ts) {
  if (!ts) return "";
  const s = Math.floor(Date.now() / 1000) - ts;
  if (s < 3600) return Math.max(1, Math.floor(s / 60)) + "m ago";
  if (s < 86400) return Math.floor(s / 3600) + "h ago";
  return Math.floor(s / 86400) + "d ago";
}
// --- Openings mode -----------------------------------------------------------
// "launchpad" = curated search links + keywords (no scraping — the daily scanner
// Lambda is also disabled in AWS). Flip to "scan" to restore the auto-scraped list
// (the scan/list/dedup/suppression code below is all retained, just bypassed).
const OPENINGS_MODE = "launchpad";
async function loadOpenings() {
  if (OPENINGS_MODE === "launchpad") {
    const c0 = $("#openings-count"); if (c0) c0.textContent = "";
    if (currentView === "openings" && $("#openings-view") && !$("#openings-view").hidden) renderOpenings();
    return;
  }
  try { OPENINGS = (await api("GET", "/openings")).openings || []; } catch (_e) { OPENINGS = []; }
  const c = $("#openings-count"); if (c) c.textContent = OPENINGS.length;
  if (currentView === "openings" && $("#openings-view") && !$("#openings-view").hidden) renderOpenings();
}
function opRisk(o) {
  if (o.capExempt) return `<span class="op-spon ok" title="University/hospital — H-1B lottery-proof">🎓 Cap-exempt sponsor</span>`;
  if (o.sponsorRisk === "low") return `<span class="op-spon ok">✓ sponsor-friendly</span>`;
  return `<span class="op-spon med">~ verify sponsorship</span>`;
}
function opFitClass(f) { return f >= 75 ? "good" : f >= 55 ? "ok" : "low"; }
const SRC_LABEL = { greenhouse: "Greenhouse", ashby: "Ashby", amazon: "Amazon", workday: "Workday",
  lever: "Lever", adzuna: "Adzuna", "github-simplify": "GitHub · Simplify", "github-vansh": "GitHub · NewGrad" };
const PLAT_LABEL = { greenhouse: "Greenhouse", ashby: "Ashby", amazon: "Amazon", workday: "Workday",
  lever: "Lever", adzuna: "Adzuna (aggregator)", github: "GitHub feeds" };
function opSrcLabel(s) { return SRC_LABEL[s] || String(s || ""); }
function opPlatKey(o) { const s = o.source || ""; return s.indexOf("github") === 0 ? "github" : s; }
function opAvailPlatforms() { const s = new Set(); OPENINGS.forEach((o) => s.add(opPlatKey(o))); return [...s].filter(Boolean).sort(); }
function opGeoPill(o) {
  if (o.geo === 0) return `<span class="op-badge tx">📍 Texas</span>`;
  if (o.geo === 1) return `<span class="op-badge rem">🏠 Remote</span>`;
  return "";
}
function opBadges(o) {
  const now = Math.floor(Date.now() / 1000);
  let h = "";
  if (o.capExempt) h += `<span class="op-badge cap" title="Cap-exempt employer — no H-1B lottery">🎓 Cap-exempt</span>`;
  if (o.staffing) h += `<span class="op-badge stf" title="Likely a staffing / consulting firm — verify before applying">⚠️ Staffing</span>`;
  if (o.firstSeenAt && now - o.firstSeenAt < NEW_WINDOW) h += `<span class="op-badge new">🆕 New</span>`;
  if (o.expireAt && o.expireAt - now < SOON_WINDOW) h += `<span class="op-badge soon" title="Aging out soon — Apply or Track it to keep it">⏳ Leaving soon</span>`;
  return h;
}
function opCard(o, i) {
  const fit = o.fit || 0;
  return `<article class="op-card">
    <div class="op-rank">${i + 1}</div>
    <div class="op-main">
      <div class="op-top"><b>${esc(o.company || "—")}</b><span class="op-src">${esc(opSrcLabel(o.source))}</span>${opGeoPill(o)}${opBadges(o)}</div>
      <div class="op-title">${esc(o.title || "")}</div>
      <div class="op-loc">${esc(o.location || "")}</div>
      ${o.reason ? `<p class="op-reason">${esc(o.reason)}</p>` : ""}
      <div class="op-tags">${opRisk(o)}</div>
    </div>
    <div class="op-side">
      <div class="op-fit ${opFitClass(fit)}" title="Match estimate from stack + seniority overlap — always verify the full JD"><b>${fit}%</b><span>fit</span></div>
      <a class="btn sm" href="${esc(o.url || "#")}" target="_blank" rel="noopener">Apply</a>
      <button class="btn sm primary op-track" data-id="${esc(o.id || "")}">+ Track</button>
      <button class="btn sm ghost op-dismiss" data-id="${esc(o.id || "")}" title="Not interested — hide this">✕ Not interested</button>
    </div>
  </article>`;
}
function opSourcesPanel() {
  return `<details class="op-sources">
    <summary>ℹ️ Where these come from — and what to check yourself</summary>
    <div class="op-src-body">
      <div class="op-src-col">
        <h4>✅ Auto-scanned daily (all sponsor-gated)</h4>
        <p>Refreshed every morning + on Rescan. Confirmed no-sponsorship roles are dropped:</p>
        <ul>
          <li><b>~65 sponsor-friendly companies</b> on Greenhouse / Ashby / Lever (Twilio, Cloudflare, Datadog, Stripe, Snowflake, Confluent, OpenAI, Palantir, Databricks, MongoDB…)</li>
          <li><b>Amazon / AWS</b> · <b>Red Hat</b> &amp; <b>Nvidia</b> (Workday)</li>
          <li><b>GitHub 🛂 feeds</b> (SimplifyJobs + New-Grad-2027) — explicit sponsorship tags</li>
          <li><b>Adzuna aggregator</b> — broad, incl. reposts of LinkedIn/Indeed roles (opt-in key)</li>
        </ul>
      </div>
      <div class="op-src-col">
        <h4>🔍 Not covered — search these yourself</h4>
        <ul>
          <li><b>LinkedIn Jobs &amp; Indeed</b> — biggest volume, and they carry OPT/visa filters</li>
          <li><b>Google, Microsoft, Meta, Apple, Salesforce, Nvidia, IBM, Cisco, Adobe</b> — custom / Workday portals not wired in</li>
          <li>Other ATSes: <b>Lever, SmartRecruiters, iCIMS, Oracle, Jobvite</b>, plus Workday tenants beyond Red Hat</li>
          <li>Startup boards: <b>Wellfound, BuiltIn, YC Work-at-a-Startup</b></li>
        </ul>
      </div>
      <div class="op-src-col">
        <h4>💡 How to search them fast</h4>
        <ul>
          <li><b>LinkedIn:</b> <i>"cloud support engineer" OR devops OR "site reliability"</i> → filter <i>Past week</i> + <i>Entry/Associate</i> → save it as a job alert.</li>
          <li><b>Google dorks:</b> <code>site:boards.greenhouse.io ("devops" OR "sre") "united states"</code> — swap in <code>jobs.lever.co</code> or <code>jobs.ashbyhq.com</code>.</li>
          <li><b>Check sponsorship first:</b> look a company up on myvisajobs.com / h1bgrader.com (or the 🛂 checker here), then go to its careers page.</li>
          <li><b>Wellfound</b> has a "will sponsor visa" toggle — fast filter.</li>
        </ul>
      </div>
    </div>
    <p class="op-src-note">Limits, honestly: this reads ATS boards only — if a company switches ATS or gates its board, that source can go quiet with no error. It's a strong daily shortlist, not the whole market. A manual LinkedIn/Indeed pass is the other half of the job.</p>
  </details>`;
}
// ---- Openings filters + sorting (all client-side over the loaded list) ------
let opFilters = { q: "", state: "", sponsor: "friendly", platform: "", minFit: 0, sort: "geo",
  onlyNew: false, onlySoon: false, onlyAI: false, onlyCap: false, hideStaffing: false };
const STATE_NAMES = { alabama: "AL", alaska: "AK", arizona: "AZ", arkansas: "AR", california: "CA",
  colorado: "CO", connecticut: "CT", delaware: "DE", florida: "FL", georgia: "GA", hawaii: "HI",
  idaho: "ID", illinois: "IL", indiana: "IN", iowa: "IA", kansas: "KS", kentucky: "KY",
  louisiana: "LA", maine: "ME", maryland: "MD", massachusetts: "MA", michigan: "MI",
  minnesota: "MN", mississippi: "MS", missouri: "MO", montana: "MT", nebraska: "NE", nevada: "NV",
  "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
  "north carolina": "NC", "north dakota": "ND", ohio: "OH", oklahoma: "OK", oregon: "OR",
  pennsylvania: "PA", "rhode island": "RI", "south carolina": "SC", "south dakota": "SD",
  tennessee: "TN", texas: "TX", utah: "UT", vermont: "VT", virginia: "VA", washington: "WA",
  "west virginia": "WV", wisconsin: "WI", wyoming: "WY", "district of columbia": "DC" };
const _geoVal = (o) => (o.geo == null ? 2 : o.geo);
function opStatesOf(o) {
  const loc = o.location || "", set = new Set();
  let m; const re = /,\s*([A-Za-z]{2})\b/g;
  while ((m = re.exec(loc))) { const c = m[1].toUpperCase(); if (US_STATES.includes(c)) set.add(c); }
  const low = loc.toLowerCase();
  Object.keys(STATE_NAMES).forEach((name) => { if (low.includes(name)) set.add(STATE_NAMES[name]); });
  return set;
}
function opAvailStates() {
  const s = new Set();
  OPENINGS.forEach((o) => opStatesOf(o).forEach((c) => s.add(c)));
  return [...s].sort();
}
function opMatchesFilters(o) {
  const f = opFilters;
  if (f.q) {
    const hay = ((o.company || "") + " " + (o.title || "") + " " + (o.location || "")).toLowerCase();
    if (!hay.includes(f.q.toLowerCase())) return false;
  }
  if (f.state) {
    if (f.state === "Remote") { if (!/remote/i.test(o.location || "")) return false; }
    else if (!opStatesOf(o).has(f.state)) return false;
  }
  if (f.sponsor) {
    if (f.sponsor === "friendly" && o.sponsorRisk !== "low") return false;
    if (f.sponsor === "cap" && !o.capExempt) return false;
    if (f.sponsor === "verify" && o.sponsorRisk !== "med") return false;
  }
  if (f.platform && opPlatKey(o) !== f.platform) return false;
  if (f.minFit && (o.fit || 0) < f.minFit) return false;
  const now = Math.floor(Date.now() / 1000);
  if (f.onlyNew && !(o.firstSeenAt && now - o.firstSeenAt < NEW_WINDOW)) return false;
  if (f.onlySoon && !(o.expireAt && o.expireAt - now < SOON_WINDOW)) return false;
  if (f.onlyCap && !o.capExempt) return false;
  if (f.hideStaffing && o.staffing) return false;
  return true;
}
function opSortCmp(a, b) {
  switch (opFilters.sort) {
    case "fit": return (b.fit || 0) - (a.fit || 0);
    case "new": return (b.firstSeenAt || 0) - (a.firstSeenAt || 0);
    case "soon": return (a.expireAt || 9e15) - (b.expireAt || 9e15);
    case "company": return (a.company || "").localeCompare(b.company || "");
    default: return _geoVal(a) - _geoVal(b) || (b.fit || 0) - (a.fit || 0);
  }
}
function applyOpFilters() { return OPENINGS.filter(opMatchesFilters).sort(opSortCmp); }
function opSel(v, cur) { return v === cur ? " selected" : ""; }
function opFilterBar() {
  const f = opFilters;
  const stateOpts = [`<option value=""${opSel("", f.state)}>All locations</option>`,
    `<option value="Remote"${opSel("Remote", f.state)}>🏠 Remote</option>`]
    .concat(opAvailStates().map((c) => `<option value="${c}"${opSel(c, f.state)}>${c}${c === "TX" ? " ★ Texas" : ""}</option>`)).join("");
  const opt = (pairs, cur) => pairs.map(([v, l]) => `<option value="${v}"${opSel(String(v), String(cur))}>${l}</option>`).join("");
  const platOpts = [`<option value=""${opSel("", f.platform)}>All sources</option>`]
    .concat(opAvailPlatforms().map((k) => `<option value="${k}"${opSel(k, f.platform)}>${PLAT_LABEL[k] || k}</option>`)).join("");
  return `<div class="op-filters">
    <input id="opf-q" class="op-f" type="search" placeholder="🔎 Search company / title…" value="${esc(f.q)}">
    <select id="opf-state" class="op-f" title="Location / state">${stateOpts}</select>
    <select id="opf-sponsor" class="op-f" title="Sponsorship">${opt([["", "All sponsorship"], ["friendly", "✓ Sponsor-friendly"], ["cap", "🎓 Cap-exempt"], ["verify", "~ Verify"]], f.sponsor)}</select>
    <select id="opf-plat" class="op-f" title="Source / platform">${platOpts}</select>
    <select id="opf-fit" class="op-f" title="Minimum match %">${opt([[0, "Any match %"], [50, "50%+ match"], [70, "70%+ match"], [80, "80%+ match"]], f.minFit)}</select>
    <label class="op-f-sort">Sort <select id="opf-sort" class="op-f">${opt([["geo", "Texas first"], ["fit", "Best match %"], ["new", "Newest"], ["soon", "Leaving soon"], ["company", "Company A–Z"]], f.sort)}</select></label>
    <span id="op-count-live" class="op-count"></span>
    <button id="opf-clear" class="btn sm ghost">Clear</button>
    <div class="op-chips">${opChipBar()}</div>
  </div>`;
}
function opChipBar() {
  const f = opFilters, now = Math.floor(Date.now() / 1000);
  const n = {
    onlyNew: OPENINGS.filter((o) => o.firstSeenAt && now - o.firstSeenAt < NEW_WINDOW).length,
    onlySoon: OPENINGS.filter((o) => o.expireAt && o.expireAt - now < SOON_WINDOW).length,
    onlyCap: OPENINGS.filter((o) => o.capExempt).length,
    hideStaffing: OPENINGS.filter((o) => o.staffing).length,
  };
  const chip = (key, label) => `<button class="op-chip${f[key] ? " on" : ""}" data-chip="${key}">${label}${n[key] ? ` <b>${n[key]}</b>` : ""}</button>`;
  return chip("onlyNew", "🆕 New") + chip("onlySoon", "⏳ Leaving soon")
    + chip("onlyCap", "🎓 Cap-exempt") + chip("hideStaffing", "🚫 Hide staffing");
}
function wireOpCardButtons() {
  $$("#openings-view .op-track").forEach((b) => (b.onclick = () => {
    const o = OPENINGS.find((x) => x.id === b.dataset.id); if (!o) return;
    openEdit(null, { company: o.company, title: o.title, location: o.location, url: o.url, jd: o.jd, workMode: /remote/i.test(o.location || "") ? "Remote" : "", _openingId: o.id });
  }));
  $$("#openings-view .op-dismiss").forEach((b) => (b.onclick = () => dismissOpening(b.dataset.id)));
}
function resetOpFilters() { opFilters = { q: "", state: "", sponsor: "", platform: "", minFit: 0, sort: "geo", onlyNew: false, onlySoon: false, onlyAI: false, onlyCap: false, hideStaffing: false }; renderOpenings(); }
function renderOpList() {
  const box = $("#op-results"); if (!box) return;
  const rows = applyOpFilters();
  const cnt = $("#op-count-live");
  if (cnt) cnt.textContent = rows.length === OPENINGS.length ? `${rows.length} shown` : `${rows.length} of ${OPENINGS.length}`;
  box.innerHTML = rows.length
    ? `<div class="op-list">${rows.map(opCard).join("")}</div>`
    : `<p class="empty">No openings match these filters. <button id="opf-clear2" class="linkish">Clear filters</button></p>`;
  const c2 = $("#opf-clear2"); if (c2) c2.onclick = resetOpFilters;
  wireOpCardButtons();
}
function updateOpMeta() {
  const now = Math.floor(Date.now() / 1000);
  const nNew = OPENINGS.filter((o) => o.firstSeenAt && now - o.firstSeenAt < NEW_WINDOW).length;
  const nSoon = OPENINGS.filter((o) => o.expireAt && o.expireAt - now < SOON_WINDOW).length;
  const last = OPENINGS.reduce((m, o) => Math.max(m, o.lastSeenAt || 0), 0);
  const meta = [];
  if (last) meta.push(`Last scan ${relAgo(last)}`);
  meta.push(`${OPENINGS.length} live`);
  if (nNew) meta.push(`<b class="op-c new">${nNew} new</b>`);
  if (nSoon) meta.push(`<b class="op-c soon">${nSoon} leaving soon</b>`);
  const el = $("#openings-view .op-meta"); if (el) el.innerHTML = meta.join(" · ");
}
// --- Launchpad: curated search links + keywords (replaces scraping) ----------
const LP_TITLES = ["Cloud Engineer", "DevOps Engineer", "Site Reliability Engineer", "Cloud Support Engineer",
  "Platform Engineer", "Infrastructure Engineer", "Cloud Operations", "Associate Solutions Architect"];
const LP_BOOLEAN = '("Cloud Engineer" OR "DevOps Engineer" OR "Site Reliability Engineer" OR "Cloud Support Engineer" OR "Platform Engineer") AND (AWS OR Terraform OR Kubernetes) AND (Associate OR Junior OR "Entry Level" OR "New Grad")';
const LP_SPONSOR = '-"no sponsorship" -"US citizen" -"security clearance" -"must be authorized to work" -"clearance required"';
const LP_PLATFORMS = [
  { name: "LinkedIn Jobs", tag: "best coverage", tip: "Filter Experience = Entry level + Associate, Date = Past week, sort Most recent. Paste the boolean string into the keyword box.", links: [
    { l: "Cloud / DevOps · Texas · entry · past week", u: 'https://www.linkedin.com/jobs/search/?keywords=%22Cloud%20Engineer%22%20OR%20%22DevOps%20Engineer%22%20OR%20%22Site%20Reliability%22&location=Texas&f_E=2%2C3&f_TPR=r604800&sortBy=DD' },
    { l: "Cloud / DevOps · Remote US · entry · past week", u: 'https://www.linkedin.com/jobs/search/?keywords=%22Cloud%20Engineer%22%20OR%20%22DevOps%20Engineer%22%20OR%20%22Site%20Reliability%22&location=United%20States&f_WT=2&f_E=2%2C3&f_TPR=r604800&sortBy=DD' },
    { l: "Cloud Support Engineer · US · past week", u: 'https://www.linkedin.com/jobs/search/?keywords=%22Cloud%20Support%20Engineer%22&location=United%20States&f_E=2%2C3&f_TPR=r604800&sortBy=DD' },
  ] },
  { name: "Indeed", tag: "high volume", tip: "Add the Entry-Level filter chip and set 'Last 7 days'. Watch for staffing reposts.", links: [
    { l: "Cloud Engineer · Texas · last 7 days", u: 'https://www.indeed.com/jobs?q=cloud+engineer&l=Texas&fromage=7&sort=date&sc=0kf%3Aexplvl(ENTRY_LEVEL)%3B' },
    { l: "DevOps Engineer · Remote · last 7 days", u: 'https://www.indeed.com/jobs?q=devops+engineer&l=Remote&fromage=7&sort=date&sc=0kf%3Aexplvl(ENTRY_LEVEL)%3B' },
  ] },
  { name: "Dice", tag: "tech-only, great for cloud", tip: "Tech-focused board; strong for AWS/DevOps roles. Set Posted = last 7 days.", links: [
    { l: "DevOps / Cloud · Texas · last 7 days", u: 'https://www.dice.com/jobs?q=DevOps%20Engineer&location=Texas,%20USA&filters.postedDate=SEVEN&filters.employmentType=FULLTIME' },
    { l: "Cloud Engineer · Remote · last 7 days", u: 'https://www.dice.com/jobs?q=Cloud%20Engineer&filters.postedDate=SEVEN&filters.isRemote=true' },
  ] },
  { name: "Amazon / AWS Jobs", tag: "OPT-friendly pipeline", tip: "Cloud Support Associate/Engineer is a classic new-grad + sponsorship pipeline into AWS.", links: [
    { l: "Cloud Support Engineer · US", u: 'https://www.amazon.jobs/en/search?base_query=cloud+support+engineer&loc_query=United+States' },
    { l: "Cloud Support Associate · US", u: 'https://www.amazon.jobs/en/search?base_query=cloud+support+associate&loc_query=United+States' },
  ] },
  { name: "Google Jobs", tag: "aggregates everything", tip: "Pulls from most boards at once; use the 'Date posted' + 'Remote' chips on the results panel.", links: [
    { l: "Cloud engineer jobs · Texas", u: 'https://www.google.com/search?q=cloud+engineer+jobs+in+texas&ibp=htl;jobs' },
    { l: "DevOps engineer · remote", u: 'https://www.google.com/search?q=remote+devops+engineer+jobs&ibp=htl;jobs' },
  ] },
  { name: "Built In", tag: "startups that sponsor", tip: "Many venture-backed cos here sponsor. Filter to Entry level + Remote or your city.", links: [
    { l: "Cloud engineer roles", u: 'https://builtin.com/jobs?search=cloud%20engineer' },
    { l: "DevOps roles", u: 'https://builtin.com/jobs?search=devops%20engineer' },
  ] },
  { name: "Wellfound (AngelList)", tag: "startup-friendly to visas", tip: "Startups; many are open to sponsorship. Set the role + United States.", links: [
    { l: "DevOps · US", u: 'https://wellfound.com/role/l/devops-engineer/united-states' },
    { l: "Cloud · US", u: 'https://wellfound.com/role/l/cloud-engineer/united-states' },
  ] },
];
const LP_SPONSOR_TOOLS = [
  { l: "h1bdata.info — who sponsored this exact title (+ salaries)", u: 'https://h1bdata.info/index.php?em=&job=cloud+engineer&city=&year=All+Years', note: "Search a job title → every employer that filed an LCA for it. Your fastest 'do they sponsor?' check." },
  { l: "MyVisaJobs — top H-1B sponsors & company reports", u: 'https://www.myvisajobs.com/', note: "Rankings + per-company sponsorship history." },
  { l: "H1BGrader — sponsor search + approval rates", u: 'https://www.h1bgrader.com/', note: "Approval odds and filing volume per employer." },
  { l: "USCIS H-1B Employer Data Hub (official)", u: 'https://www.uscis.gov/tools/reports-and-studies/h-1b-employer-data-hub', note: "Ground-truth government data on approvals/denials by employer." },
  { l: "GitHub · SimplifyJobs New-Grad Positions (🛂 tagged)", u: 'https://github.com/SimplifyJobs/New-Grad-Positions', note: "Live new-grad list with sponsorship flags." },
  { l: "GitHub · vanshb03 New-Grad-2027 (🛂 tagged)", u: 'https://github.com/vanshb03/New-Grad-2027', note: "Second curated new-grad feed, updated daily." },
];
// Target companies — categorized, apply direct from their career pages. (Full list also in
// _local/company-targets.md.) Verify sponsorship on the specific req before applying.
const LP_CAPEXEMPT = [   // 🎓 H-1B lottery-proof — universities & nonprofit hospitals (apply here first)
  { l: "University of Houston 📍 (your school)", u: 'https://uh.wd1.myworkdayjobs.com/UHCareers' },
  { l: "MD Anderson Cancer Center 📍", u: 'https://careers.mdanderson.org/' },
  { l: "Houston Methodist 📍", u: 'https://jobs.houstonmethodist.org/' },
  { l: "Baylor College of Medicine 📍", u: 'https://jobs.bcm.edu/' },
  { l: "Texas Children's Hospital 📍", u: 'https://jobs.texaschildrens.org/' },
  { l: "UTHealth Houston 📍", u: 'https://go.uth.edu/careers' },
  { l: "Memorial Hermann 📍", u: 'https://careers.memorialhermann.org/' },
  { l: "Rice University 📍", u: 'https://jobs.rice.edu/' },
  { l: "UT Austin", u: 'https://jobs.utexas.edu/' },
  { l: "Texas A&M", u: 'https://jobs.tamu.edu/' },
  { l: "UT Dallas", u: 'https://jobs.utdallas.edu/' },
];
const LP_TEXAS = [   // 📍 Texas employers — high UH-alumni density, no relocation
  { l: "H-E-B Digital (San Antonio/Houston)", u: 'https://careers.heb.com/' },
  { l: "USAA (San Antonio) — verify sponsorship", u: 'https://www.usaajobs.com/' },
  { l: "Charles Schwab (Southlake)", u: 'https://www.schwabjobs.com/' },
  { l: "Fidelity (Westlake) — heavy sponsor", u: 'https://jobs.fidelity.com/' },
  { l: "Tyler Technologies (Plano)", u: 'https://www.tylertech.com/careers' },
  { l: "Dell (Round Rock)", u: 'https://jobs.dell.com/' },
  { l: "Texas Instruments (commercial only)", u: 'https://careers.ti.com/' },
  { l: "Rackspace (San Antonio)", u: 'https://www.rackspace.com/careers' },
  { l: "Indeed (Austin)", u: 'https://www.indeed.jobs/' },
];
const LP_ENERGY = [   // 🛢️ Houston energy majors — large local cloud/IT orgs, many UH grads
  { l: "ExxonMobil 📍", u: 'https://jobs.exxonmobil.com/' },
  { l: "Chevron 📍", u: 'https://careers.chevron.com/' },
  { l: "ConocoPhillips 📍", u: 'https://careers.conocophillips.com/' },
  { l: "SLB / Schlumberger 📍 (strong tech sponsor)", u: 'https://careers.slb.com/' },
  { l: "Halliburton 📍", u: 'https://jobs.halliburton.com/' },
  { l: "Baker Hughes 📍", u: 'https://careers.bakerhughes.com/' },
  { l: "Phillips 66 📍", u: 'https://jobs.phillips66.com/' },
  { l: "Occidental (Oxy) 📍", u: 'https://www.oxy.com/careers/' },
];
const LP_BIGTECH = [   // 🏆 Big-tech + ☁️ cloud/infra product companies — best stack fit, sponsor at scale
  { l: "Amazon / AWS (Cloud Support Engineer = bullseye)", u: 'https://www.amazon.jobs/' },
  { l: "Microsoft (Azure Support roles)", u: 'https://careers.microsoft.com/' },
  { l: "Google (Technical Solutions Engineer)", u: 'https://www.google.com/about/careers/applications/' },
  { l: "Meta (Production Engineer, University Grad)", u: 'https://www.metacareers.com/' },
  { l: "NVIDIA", u: 'https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite' },
  { l: "IBM", u: 'https://www.ibm.com/careers/search' },
  { l: "Red Hat (heavy sponsor)", u: 'https://redhat.wd5.myworkdayjobs.com/jobs' },
  { l: "HashiCorp (makers of Terraform)", u: 'https://www.hashicorp.com/careers/open-positions' },
  { l: "Datadog", u: 'https://careers.datadoghq.com/' },
  { l: "Cloudflare", u: 'https://www.cloudflare.com/careers/jobs/' },
  { l: "MongoDB", u: 'https://www.mongodb.com/company/careers' },
  { l: "Snowflake", u: 'https://careers.snowflake.com/us/en' },
  { l: "Confluent", u: 'https://careers.confluent.io/' },
  { l: "GitLab", u: 'https://about.gitlab.com/jobs/all-jobs/' },
  { l: "Databricks", u: 'https://www.databricks.com/company/careers' },
  { l: "DigitalOcean", u: 'https://careers.digitalocean.com/' },
  { l: "Akamai", u: 'https://jobs.akamai.com/en/sites/CX_1/jobs' },
];
const LP_FINTECH = [   // 💳 Fintech / finance — heavy sponsors, huge cloud/SRE orgs
  { l: "Capital One (AWS-first; Plano 📍)", u: 'https://www.capitalonecareers.com/' },
  { l: "JPMorgan Chase (Plano/Houston 📍)", u: 'https://careers.jpmorgan.com/' },
  { l: "Goldman Sachs", u: 'https://www.goldmansachs.com/careers/' },
  { l: "Bloomberg", u: 'https://careers.bloomberg.com/' },
  { l: "Stripe", u: 'https://stripe.com/jobs/search' },
  { l: "PayPal", u: 'https://careers.pypl.com/' },
  { l: "Coinbase", u: 'https://www.coinbase.com/careers' },
  { l: "Plaid", u: 'https://plaid.com/careers/' },
  { l: "Robinhood", u: 'https://careers.robinhood.com/' },
  { l: "Chime", u: 'https://careers.chime.com/' },
];
const LP_CONSULTANCY = [   // 🤝 OPT→H-1B consultancies + AWS partners — structured early-career cloud tracks
  { l: "Accenture (Technology Development Program)", u: 'https://www.accenture.com/us-en/careers' },
  { l: "Slalom (AWS Cloud Residency)", u: 'https://www.slalom.com/careers' },
  { l: "Infosys", u: 'https://www.infosys.com/careers/' },
  { l: "EPAM", u: 'https://www.epam.com/careers' },
  { l: "Thoughtworks", u: 'https://www.thoughtworks.com/careers' },
  { l: "Capgemini", u: 'https://www.capgemini.com/careers/' },
  { l: "Cognizant", u: 'https://careers.cognizant.com/' },
  { l: "Caylent (AWS Premier Partner)", u: 'https://caylent.com/careers' },
  { l: "Mission Cloud (AWS partner)", u: 'https://www.missioncloud.com/careers' },
  { l: "DoiT (cloud)", u: 'https://www.doit.com/careers/' },
];
// Sponsor-first aggregators + startup boards — where you HAVEN'T been (beyond LinkedIn/Indeed/Dice).
const LP_AGGREGATORS = [
  { l: "hiring.cafe — has a real ‘Visa Sponsorship’ filter", u: 'https://hiring.cafe/', note: "Aggregates thousands of company-direct posts. Set the Visa Sponsorship filter + your titles — best signal-to-noise for your situation." },
  { l: "Simplify — new-grad roles WITH visa sponsorship", u: 'https://simplify.jobs/l/New-Grad-Roles-with-Visa-Sponsorship', note: "Curated sponsor-tagged list; the Simplify extension also autofills applications." },
  { l: "Work at a Startup (Y Combinator)", u: 'https://www.workatastartup.com/companies?roles=eng', note: "YC startups — they sponsor more readily and you often reach the founder directly. Less competition than big boards." },
  { l: "a16z portfolio jobs", u: 'https://portfoliojobs.a16z.com/jobs', note: "One search across a16z-backed startups — many sponsor early hires." },
  { l: "Levels.fyi jobs", u: 'https://www.levels.fyi/jobs', note: "Great filters + salary transparency; strong for cloud/infra roles." },
];
// University of Houston — your warmest, most underused channel (alumni + cap-exempt + fall recruiting).
const LP_UH = [
  { l: "UH Handshake — student/alumni job board + fall recruiting", u: 'https://uh.joinhandshake.com/', note: "Log in with your UH account. Fall new-grad recruiting opens now — employers here WANT to hire UH grads." },
  { l: "Find UH alumni at any company (LinkedIn)", u: 'https://www.linkedin.com/school/university-of-houston/people/', note: "Open this, then type a company in the search box → see UH grads who work there → message them for a referral. Warmest path you have." },
  { l: "UH University Career Services", u: 'https://www.uh.edu/ucs/', note: "Résumé reviews, career fairs, employer connections." },
  { l: "UH International Student Services (OPT/visa job help)", u: 'https://www.uh.edu/oiss/', note: "OPT/CPT guidance + international-student career resources; ask if UH gives you Interstride." },
];
function lpLinks(arr) {
  return arr.map((x) => `<a class="lp-link" href="${esc(x.u)}" target="_blank" rel="noopener">${esc(x.l)}${x.note ? `<span class="lp-note">${esc(x.note)}</span>` : ""}</a>`).join("");
}
// Weekly-reset checklist for the target companies. Stored in this browser (localStorage),
// keyed to the current week (Monday) — so the checkmarks clear automatically each new week.
const LP_COMPANIES = () => [...LP_CAPEXEMPT, ...LP_TEXAS, ...LP_ENERGY, ...LP_BIGTECH, ...LP_FINTECH, ...LP_CONSULTANCY];
function weekKey() {                              // Monday of the current week, as YYYY-MM-DD
  const d = new Date(today() + "T00:00:00");
  d.setDate(d.getDate() - ((d.getDay() + 6) % 7));
  return _ymd(d);
}
function lpChecks() {
  try {
    const s = JSON.parse(localStorage.getItem("lp-checks") || "{}");
    return s.date === weekKey() ? { date: s.date, checked: s.checked || {} } : { date: weekKey(), checked: {} };
  } catch (_e) { return { date: weekKey(), checked: {} }; }
}
function lpSaveChecks(s) { try { localStorage.setItem("lp-checks", JSON.stringify(s)); } catch (_e) { /* private mode */ } }
function lpCoLinks(arr, checked) {
  return arr.map((x) => {
    const on = !!checked[x.u];
    return `<div class="lp-co${on ? " done" : ""}">
      <input type="checkbox" class="lp-co-chk" data-u="${esc(x.u)}"${on ? " checked" : ""} title="Mark done for today" />
      <a class="lp-link" href="${esc(x.u)}" target="_blank" rel="noopener">${esc(x.l)}</a>
    </div>`;
  }).join("");
}
function renderLaunchpad(el) {
  const checks = lpChecks(); lpSaveChecks(checks);          // persist the weekly reset
  const coAll = LP_COMPANIES(), coTotal = coAll.length;
  const coDone = () => coAll.filter((x) => lpChecks().checked[x.u]).length;
  el.innerHTML = `<div class="page-head"><div>
      <h1>🚀 Job-Search Launchpad</h1>
      <p class="sub">Curated deep-links into every job platform — pre-filtered for <b>your</b> profile (entry/associate cloud · DevOps · SRE, Texas + remote, recent postings) — plus sponsor-first boards, your UH alumni channel, and a categorized list of target companies to apply to direct. Open a link, it lands you on a live search. No scraping; always fresh.</p>
    </div></div>
    <div class="container"><div class="container-body lp-wrap">

      <section class="lp-callout">
        <h3>⚡ The move now: outreach beats screening</h3>
        <p>You've applied at real volume — but every one is a <b>cold</b> application, and for a sponsorship candidate those convert worst. A <b>referral is worth ~5–10 cold applications.</b> For each role you apply to: find <b>one person</b> at that company (a <b>UH alum is warmest</b>), send them a short referral note, and set a <b>5–7 day follow-up</b>. Fewer new applications, more human contact on the ones you have.</p>
      </section>

      <section class="lp-sec">
        <h3>🔑 Your search terms</h3>
        <div class="lp-chips">${LP_TITLES.map((t) => `<button class="lp-chip" data-copytext="${esc(t)}" title="Click to copy">${esc(t)}</button>`).join("")}</div>
        <div class="lp-copyrow"><code class="lp-code" id="lp-bool">${esc(LP_BOOLEAN)}</code><button class="btn sm" id="lp-copy-bool">Copy boolean</button></div>
        <div class="lp-copyrow"><code class="lp-code" id="lp-spon">${esc(LP_SPONSOR)}</code><button class="btn sm" id="lp-copy-spon">Copy sponsor filter</button></div>
        <p class="lp-hint">Paste the boolean into LinkedIn/Indeed keyword boxes. Append the sponsor filter to hide roles that exclude visas. Click any title chip to copy it.</p>
      </section>

      <section class="lp-sec">
        <h3>🌐 Job platforms <span class="lp-muted">— pre-filtered searches</span></h3>
        <div class="lp-grid">
          ${LP_PLATFORMS.map((p) => `<div class="lp-card">
            <div class="lp-card-h"><b>${esc(p.name)}</b><span class="lp-tag">${esc(p.tag)}</span></div>
            <p class="lp-tip">${esc(p.tip)}</p>
            <div class="lp-links">${lpLinks(p.links)}</div>
          </div>`).join("")}
        </div>
      </section>

      <section class="lp-sec">
        <h3>🚀 Sponsor-first &amp; startup boards <span class="lp-muted">— where you haven't been yet</span></h3>
        <p class="lp-hint">Beyond LinkedIn/Indeed/Dice. These either filter for visa sponsorship or surface startups (which sponsor more readily and have less competition).</p>
        <div class="lp-links wide">${lpLinks(LP_AGGREGATORS)}</div>
      </section>

      <section class="lp-sec">
        <h3>🎓 University of Houston <span class="lp-muted">— your warmest, most underused channel</span></h3>
        <p class="lp-hint">Alumni referrals + cap-exempt hiring + fall recruiting (opening now). Start here before another cold board.</p>
        <div class="lp-links wide">${lpLinks(LP_UH)}</div>
      </section>

      <section class="lp-sec">
        <h3>🛂 Visa-sponsorship research <span class="lp-muted">— check before you apply</span></h3>
        <div class="lp-links wide">${lpLinks(LP_SPONSOR_TOOLS)}</div>
      </section>

      <section class="lp-sec">
        <div class="lp-co-head">
          <h3>🎯 Target companies <span class="lp-muted">— apply direct from their career pages</span></h3>
          <div class="lp-co-tools"><span id="lp-co-progress" class="lp-co-prog">${coDone()} / ${coTotal} done this week</span><button class="btn sm" id="lp-co-reset">Reset week</button></div>
        </div>
        <p class="lp-hint">Tick a company off once you've applied to / reviewed it — the checkmarks <b>reset every week</b> (each Monday) so it's a fresh weekly pass (saved in this browser). <b>Verify the sponsorship clause on the specific req.</b> 📍 = Texas / no relocation.</p>
        <h4 class="lp-sub">🎓 Cap-exempt — Texas <span class="lp-muted">(H-1B lottery-proof — apply here first)</span></h4>
        <div class="lp-links grid3">${lpCoLinks(LP_CAPEXEMPT, checks.checked)}</div>
        <h4 class="lp-sub">📍 Texas employers <span class="lp-muted">(home-field: UH-alumni density)</span></h4>
        <div class="lp-links grid3">${lpCoLinks(LP_TEXAS, checks.checked)}</div>
        <h4 class="lp-sub">🛢️ Houston energy majors <span class="lp-muted">(large local cloud/IT orgs)</span></h4>
        <div class="lp-links grid3">${lpCoLinks(LP_ENERGY, checks.checked)}</div>
        <h4 class="lp-sub">🏆 Big-tech &amp; cloud-product <span class="lp-muted">(best stack fit, sponsor at scale)</span></h4>
        <div class="lp-links grid3">${lpCoLinks(LP_BIGTECH, checks.checked)}</div>
        <h4 class="lp-sub">💳 Fintech &amp; finance <span class="lp-muted">(heavy sponsors, big cloud/SRE orgs)</span></h4>
        <div class="lp-links grid3">${lpCoLinks(LP_FINTECH, checks.checked)}</div>
        <h4 class="lp-sub">🤝 Consultancies &amp; AWS partners <span class="lp-muted">(OPT→H-1B early-career tracks)</span></h4>
        <div class="lp-links grid3">${lpCoLinks(LP_CONSULTANCY, checks.checked)}</div>
      </section>

    </div></div>`;
  const copy = (text, btn, done) => {
    const write = navigator.clipboard && navigator.clipboard.writeText
      ? navigator.clipboard.writeText(text) : Promise.reject();
    write.then(() => { const o = btn.textContent; btn.textContent = done || "Copied ✓"; setTimeout(() => (btn.textContent = o), 1200); })
      .catch(() => { window.prompt("Copy:", text); });
  };
  const cb = $("#lp-copy-bool"); if (cb) cb.onclick = () => copy(LP_BOOLEAN, cb);
  const cs = $("#lp-copy-spon"); if (cs) cs.onclick = () => copy(LP_SPONSOR, cs);
  $$("#openings-view .lp-chip").forEach((c) => (c.onclick = () => copy(c.dataset.copytext, c, "Copied ✓")));
  // Target-company daily checklist
  const refreshProg = () => { const p = $("#lp-co-progress"); if (p) p.textContent = `${coDone()} / ${coTotal} done this week`; };
  $$("#openings-view .lp-co-chk").forEach((c) => (c.onchange = () => {
    const s = lpChecks();
    if (c.checked) s.checked[c.dataset.u] = true; else delete s.checked[c.dataset.u];
    lpSaveChecks(s);
    c.closest(".lp-co").classList.toggle("done", c.checked);
    refreshProg();
  }));
  const rst = $("#lp-co-reset"); if (rst) rst.onclick = () => {
    lpSaveChecks({ date: weekKey(), checked: {} });
    $$("#openings-view .lp-co-chk").forEach((c) => { c.checked = false; c.closest(".lp-co").classList.remove("done"); });
    refreshProg();
  };
}
function renderOpenings() {
  const el = $("#openings-view"); if (!el) return;
  if (OPENINGS_MODE === "launchpad") return renderLaunchpad(el);
  const now = Math.floor(Date.now() / 1000);
  const nNew = OPENINGS.filter((o) => o.firstSeenAt && now - o.firstSeenAt < NEW_WINDOW).length;
  const nSoon = OPENINGS.filter((o) => o.expireAt && o.expireAt - now < SOON_WINDOW).length;
  const last = OPENINGS.reduce((m, o) => Math.max(m, o.lastSeenAt || 0), 0);
  const meta = [];
  if (last) meta.push(`Last scan ${relAgo(last)}`);
  meta.push(`${OPENINGS.length} live`);
  if (nNew) meta.push(`<b class="op-c new">${nNew} new</b>`);
  if (nSoon) meta.push(`<b class="op-c soon">${nSoon} leaving soon</b>`);
  el.innerHTML = `<div class="page-head"><div><h1>🔎 Openings</h1><p class="sub">Entry-level cloud · DevOps · SRE · support roles scanned across sponsor-friendly companies, pulled daily from ATS boards (Greenhouse/Ashby/Amazon/Workday/Lever), the GitHub 🛂 sponsorship feeds, and the Adzuna aggregator — then <b>every confirmed no-sponsorship role is dropped</b> (only sponsor-enabled or likely-to-sponsor kept), scored by stack + seniority overlap, and ranked <b>Texas → remote → rest of US</b> by match %. Only ≥50% matches kept. Anything you log to the tracker or mark <b>Not interested</b> never comes back, and duplicates are collapsed to one. Filter by source · state · sponsorship · match %, or the quick chips (🆕 New · ⏳ Leaving soon · 🎓 Cap-exempt · 🚫 Hide staffing).</p>
      <p class="op-meta">${meta.join(" · ")}</p></div>
      <div class="head-actions"><button id="op-rescan" class="btn">↻ Rescan</button></div></div>
    ${opSourcesPanel()}
    <div class="container"><div class="container-body">
      ${OPENINGS.length ? `${opFilterBar()}<div id="op-results"></div>`
      : `<p class="empty">No openings right now. Hit <b>↻ Rescan</b> to pull the latest (~1–2 min); new finds are added without wiping what's here.</p>`}
    </div></div>`;
  $("#op-rescan").onclick = rescanOpenings;
  const q = $("#opf-q"); if (q) q.oninput = () => { opFilters.q = q.value; renderOpList(); };
  const st = $("#opf-state"); if (st) st.onchange = () => { opFilters.state = st.value; renderOpList(); };
  const sp = $("#opf-sponsor"); if (sp) sp.onchange = () => { opFilters.sponsor = sp.value; renderOpList(); };
  const pl = $("#opf-plat"); if (pl) pl.onchange = () => { opFilters.platform = pl.value; renderOpList(); };
  const ft = $("#opf-fit"); if (ft) ft.onchange = () => { opFilters.minFit = +ft.value; renderOpList(); };
  const so = $("#opf-sort"); if (so) so.onchange = () => { opFilters.sort = so.value; renderOpList(); };
  const cl = $("#opf-clear"); if (cl) cl.onclick = resetOpFilters;
  $$("#openings-view .op-chip").forEach((c) => (c.onclick = () => {
    const k = c.dataset.chip; opFilters[k] = !opFilters[k];
    c.classList.toggle("on", opFilters[k]); renderOpList();
  }));
  if (OPENINGS.length) renderOpList();
}
async function dismissOpening(id) {
  const idx = OPENINGS.findIndex((x) => x.id === id); if (idx < 0) return;
  OPENINGS.splice(idx, 1);
  const c = $("#openings-count"); if (c) c.textContent = OPENINGS.length;
  renderOpList(); updateOpMeta();
  if (id) { try { await api("POST", `/openings/${encodeURIComponent(id)}/dismiss`, {}); } catch (_e) { /* stays hidden locally regardless */ } }
}
async function rescanOpenings() {
  const btn = $("#op-rescan"); if (btn) { btn.disabled = true; btn.textContent = "Scanning… (~1–2 min)"; }
  const baseline = OPENINGS.reduce((m, o) => Math.max(m, o.lastSeenAt || 0), 0);
  try {
    await api("POST", "/openings/scan", {});
    // Openings persist now, so poll until a fresher scan lands (newer lastSeenAt) or timeout.
    for (let i = 0; i < 10; i++) {
      await new Promise((r) => setTimeout(r, 12000));
      await loadOpenings();
      if (OPENINGS.reduce((m, o) => Math.max(m, o.lastSeenAt || 0), 0) > baseline) break;
    }
  } catch (_e) { /* leave button re-enabled below */ }
  const b = $("#op-rescan"); if (b) { b.disabled = false; b.textContent = "↻ Rescan"; }
  renderOpenings();
}

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
    loadOpenings();
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
$("#home-logo").onclick = () => { filterStatus = "all"; filterState = ""; filterDate = ""; filterOpt = false; filterReach = ""; query = ""; $("#search").value = ""; $("#f-state").value = ""; $("#f-date").value = ""; $("#f-opt").checked = false; $("#f-reach").value = ""; render(); setView("all"); closeDrawer(); window.scrollTo(0, 0); };
$("#sidebar").addEventListener("click", (e) => { if (e.target.closest(".chip")) closeDrawer(); });
$("#add").onclick = () => openEdit(null);
$("#cancel").onclick = cancelEdit;
$("#e-back").onclick = cancelEdit;
$("#autofill").onclick = autofill;
$("#check-spon").onclick = checkSponsorEdit;
$("#gen-resume").onclick = generateResume;
$("#cf-add").onclick = () => $("#cf-rows").appendChild(cfRow());
$("#ro-add").onclick = () => $("#ro-rows").appendChild(roRow());
// remember the model choice across generations (so Haiku stays picked for bulk)
(function () {
  const el = $("#gen-model"), k = "jhcc_gen-model", saved = localStorage.getItem(k);
  if (el && saved) el.value = saved;
  if (el) el.onchange = () => localStorage.setItem(k, el.value);
})();
$("#app-form").onsubmit = saveApp;
$("#export").onclick = exportCsv;
$("#activity-btn").onclick = () => ($("#activity").hidden = !$("#activity").hidden);
$("#analytics-btn").onclick = () => { const a = $("#analytics"); a.hidden = !a.hidden; if (!a.hidden) renderAnalytics(); };
$("#f-state").onchange = (e) => { filterState = e.target.value; renderList(); };
$("#f-date").onchange = (e) => { filterDate = e.target.value; renderActivity(); renderList(); };
$("#f-opt").onchange = (e) => { filterOpt = e.target.checked; renderList(); };
$("#f-reach").onchange = (e) => { filterReach = e.target.value; renderList(); };
$("#f-clear").onclick = () => { filterState = ""; filterDate = ""; filterOpt = false; filterReach = ""; $("#f-state").value = ""; $("#f-date").value = ""; $("#f-opt").checked = false; $("#f-reach").value = ""; renderActivity(); renderList(); renderStateFilter(); };
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
