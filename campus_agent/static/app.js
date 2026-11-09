"use strict";

/* Ker Avel assistant: a small client for the campus agent API. No framework, no build step. */

const SUGGESTIONS = {
  "stu-0001": [
    "Qu'est-ce que j'ai cette semaine ?",
    "Prends-moi un rendez-vous avec mon enseignant d'apprentissage automatique pour parler de mon projet",
    "J'étais absente au TP de vendredi, j'ai un certificat médical",
    "Réserve une salle de travail mardi prochain à 14 h pour deux heures avec Lucas Le Corre",
    "Il me faut une attestation de scolarité",
  ],
  "stu-0003": [
    "Quelles sont mes notes ?",
    "Combien de temps ai-je pour justifier une absence ?",
    "Le wifi du bâtiment C ne marche pas",
    "Ai-je le droit d'utiliser ChatGPT pour un TP ?",
  ],
  "tch-001": [
    "Je ne peux pas assurer mon TP de vendredi : déplace-le la semaine prochaine et préviens les étudiants",
    "Quels étudiants sont en difficulté en apprentissage automatique ?",
    "Qu'est-ce que j'ai au programme cette semaine ?",
  ],
  "tch-008": [
    "Organise un rattrapage d'analyse de deux heures pour le groupe 1A-TD1 la semaine prochaine",
    "Combien d'absences en attente dans mes cours ?",
  ],
  "stf-01": [
    "Quelles justifications d'absence sont en attente ?",
    "Génère une attestation de scolarité pour Maël Guivarc'h",
    "Réserve la salle du conseil jeudi prochain à 14 h pour une réunion d'une heure avec Yann Le Goff et Sophie Durand",
  ],
};

const KINDS = { cours: "Cours", td: "TD", tp: "TP", examen: "Examen", permanence: "Permanence",
  rendez_vous: "Rendez-vous", reservation: "Réservation", reunion: "Réunion", autre: "Personnel" };
const TOOL_LABELS = { search_people: "Recherche d'une personne", search_courses: "Catalogue des cours",
  get_schedule: "Lecture d'agenda", find_free_slots: "Recherche de créneaux", find_available_rooms: "Salles libres",
  get_grades: "Notes", get_absences: "Absences", search_documents: "Règlement", get_messages: "Messages",
  list_tickets: "Tickets", generate_certificate: "Attestation", create_event: "Création d'événement",
  update_event: "Modification d'événement", cancel_event: "Annulation d'événement",
  justify_absence: "Justification d'absence", review_absence: "Examen d'une justification",
  send_announcement: "Envoi de message", create_ticket: "Ticket de support" };
const DAY_START = 8, DAY_END = 19, PX_PER_HOUR = 60;

const state = { token: null, actor: null, session: null, weekStart: null, today: null, busy: false, cost: 0 };
const $ = (id) => document.getElementById(id);

function store(key, value) {
  try { value === null ? sessionStorage.removeItem(key) : sessionStorage.setItem(key, value); } catch (_) { /* private mode */ }
}
function load(key) { try { return sessionStorage.getItem(key); } catch (_) { return null; } }

async function api(path, options = {}) {
  const headers = { "content-type": "application/json" };
  if (state.token) headers.authorization = `Bearer ${state.token}`;
  const res = await fetch(path, { ...options, headers });
  const data = await res.json().catch(() => ({}));
  if (res.status === 401 && state.token) {
    signOut(data.error || "Session expirée : choisis à nouveau un profil.");
    throw new Error(data.error || "Session expirée.");
  }
  if (!res.ok) throw new Error(data.error || `Erreur ${res.status}`);
  return data;
}

/* ---------------------------------------------------------------- text rendering (escaped first) */

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function inline(s) {
  return s
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\[([a-z0-9-]+#\d+)\]/g, '<span class="ref" title="Référence du règlement">$1</span>');
}
function renderText(text) {
  const lines = escapeHtml(text).split("\n");
  let html = "", list = false;
  for (const raw of lines) {
    const line = raw.trim();
    const item = line.match(/^[-*•]\s+(.*)$/) || line.match(/^\d+[.)]\s+(.*)$/);
    if (item) {
      if (!list) { html += "<ul>"; list = true; }
      html += `<li>${inline(item[1])}</li>`;
      continue;
    }
    if (list) { html += "</ul>"; list = false; }
    if (line) html += `<p>${inline(line.replace(/^#+\s*/, ""))}</p>`;
  }
  return html + (list ? "</ul>" : "");
}

/* ---------------------------------------------------------------- landing */

async function showLanding() {
  $("workspace").hidden = true; $("who").hidden = true; $("landing").hidden = false; $("notice").hidden = true;
  const list = $("personas");
  list.textContent = "";
  try {
    const { personas } = await api("/api/personas");
    for (const p of personas) {
      const li = document.createElement("li");
      const b = document.createElement("button");
      b.innerHTML = `<span class="p-name"></span><span class="p-label"></span><span class="p-go">Essayer</span><span class="p-try"></span>`;
      b.querySelector(".p-name").textContent = p.name;
      b.querySelector(".p-label").textContent = p.label.split(", ").slice(1).join(", ");
      const first = (SUGGESTIONS[p.id] || [])[0];
      if (first) b.querySelector(".p-try").textContent = `Par exemple : « ${first} »`;
      b.addEventListener("click", () => signIn(p.id));
      li.append(b); list.append(li);
    }
    const h = await api("/api/health");
    $("health").textContent = h.agent_configured
      ? `Modèle ${h.model}. Budget de la démonstration aujourd'hui : ${h.spent_today_usd.toFixed(2)} $ dépensés sur ${h.daily_budget_usd.toFixed(2)} $.`
      : "La clé de l'API Claude n'est pas configurée : l'agenda fonctionne, mais l'assistant ne répondra pas.";
    if (h.spent_today_usd >= h.daily_budget_usd) {
      $("health").textContent = "Le budget quotidien de la démonstration est atteint : l'assistant répondra de nouveau demain. L'agenda reste consultable.";
    }
  } catch (e) { $("health").textContent = e.message; }
}

async function signIn(personId) {
  // The previous token lets the server keep this visitor's copy of the school across personas.
  const previous = state.token || load("visitor") || "";
  let data;
  try {
    data = await api("/api/login", { method: "POST", body: JSON.stringify({ person_id: personId, previous_token: previous }) });
  } catch (e) { showNotice(e.message); return; }
  state.token = data.token; state.actor = data.actor;
  store("token", data.token); store("visitor", data.token);
  await startWorkspace(true);
}

function signOut(message) {
  state.token = null; state.actor = null; state.session = null; state.cost = 0;
  store("token", null); store("session", null);
  if (message) { store("visitor", null); }
  showLanding().then(() => { if (message) showNotice(message); });
}

function showNotice(text) {
  const n = $("notice");
  n.textContent = text; n.hidden = false;
  n.scrollIntoView({ block: "center" });
}

/* ---------------------------------------------------------------- workspace */

async function startWorkspace(fresh) {
  const me = await api("/api/me");
  state.actor = me.actor;
  showQuota(me.quota);
  $("landing").hidden = true; $("workspace").hidden = false; $("who").hidden = false;
  $("who-name").textContent = `${state.actor.name}`;
  $("thread").textContent = "";
  state.session = fresh ? null : load("session");
  if (state.session) {
    try {
      const h = await api(`/api/sessions/${state.session}/history`);
      for (const m of h.messages) {
        if (m.role === "decision") continue;
        addMessage(m.role, m.text);
      }
      for (const a of h.pending_actions) addSlip(a);
    } catch (_) { state.session = null; }
  }
  if (!state.session) {
    state.session = (await api("/api/sessions", { method: "POST" })).session_id;
    store("session", state.session);
    addMessage("assistant", greeting());
  }
  renderSuggestions();
  refreshSide();
}

function greeting() {
  const first = state.actor.name.split(" ")[0];
  const role = { student: "tes cours, tes absences, tes notes, un rendez-vous ou une salle de travail",
    teacher: "tes séances, tes groupes, une salle ou un message à tes étudiants",
    staff: "les justifications d'absence, les attestations, les salles ou les messages" }[state.actor.role];
  return `Bonjour ${first}. Je peux t'aider pour ${role}. Je te demanderai toujours de confirmer avant de modifier quoi que ce soit.`;
}

function renderSuggestions() {
  const box = $("suggestions");
  box.textContent = "";
  box.hidden = Boolean(document.querySelector(".msg.user"));
  for (const s of SUGGESTIONS[state.actor.id] || []) {
    const b = document.createElement("button");
    b.type = "button"; b.textContent = s;
    b.addEventListener("click", () => { $("input").value = s; $("input").focus(); });
    box.append(b);
  }
}

function addMessage(role, text) {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  if (role === "assistant") div.innerHTML = renderText(text); else div.textContent = text;
  $("thread").append(div);
  div.scrollIntoView({ block: "end" });
  return div;
}

function addTrace(container, trace) {
  const tools = trace.filter((s) => s.type === "tool");
  for (const step of tools) {
    if (step.download_url) {
      const b = document.createElement("button");
      b.className = "download"; b.textContent = "Télécharger l'attestation (PDF)";
      b.addEventListener("click", () => download(step.download_url));
      container.append(b);
    }
  }
  if (!tools.length) return;
  const d = document.createElement("details");
  d.className = "trace";
  d.innerHTML = `<summary></summary><ol></ol>`;
  d.querySelector("summary").textContent = `${tools.length} outil${tools.length > 1 ? "s" : ""} utilisé${tools.length > 1 ? "s" : ""}`;
  for (const step of tools) {
    const li = document.createElement("li");
    const label = TOOL_LABELS[step.name] || step.name;
    li.innerHTML = `<strong></strong> <code></code><br><span></span>`;
    li.querySelector("strong").textContent = label;
    li.querySelector("code").textContent = JSON.stringify(step.input);
    const span = li.querySelector("span");
    span.textContent = step.preview; if (!step.ok) span.className = "err";
    d.querySelector("ol").append(li);
  }
  container.append(d);
}

function addSlip(action) {
  const node = $("tpl-action").content.firstElementChild.cloneNode(true);
  node.querySelector(".slip-summary").textContent = action.summary;
  node.setAttribute("aria-label", `Action à confirmer : ${action.summary}`);
  node.querySelector(".confirm").addEventListener("click", () => decide(action.id, true, node));
  node.querySelector(".cancel").addEventListener("click", () => decide(action.id, false, node));
  $("thread").append(node);
  node.scrollIntoView({ block: "end" });
  node.querySelector(".confirm").focus({ preventScroll: true });
}

function closeSlip(node, ok, text) {
  node.classList.add("done");
  node.querySelector(".slip-title").textContent = "Action traitée";
  const buttons = node.querySelector(".slip-buttons");
  buttons.textContent = "";
  const p = document.createElement("p");
  p.className = `outcome ${ok ? "ok" : "ko"}`; p.textContent = text;
  buttons.append(p);
}

function showTurn(turn) {
  const reply = addMessage("assistant", turn.reply || "…");
  addTrace(reply, turn.trace || []);
  for (const a of turn.pending_actions || []) addSlip(a);
  state.cost += turn.usage ? turn.usage.cost_usd : 0;
  $("cost").textContent = `Coût de la conversation : ${(state.cost * 100).toFixed(3)} centime(s) de dollar` +
    (turn.usage ? `, dernier échange : ${turn.usage.calls} appel(s) au modèle en ${turn.usage.latency_s.toFixed(1)} s.` : ".");
  showQuota(turn.quota);
}

function showQuota(q) {
  if (!q) { $("quota").hidden = true; return; }
  $("quota").hidden = false;
  $("quota").textContent = q.remaining > 0
    ? `Il te reste ${q.remaining} message${q.remaining > 1 ? "s" : ""} sur ${q.messages_limit} dans cette démonstration.`
    : `Tu as utilisé les ${q.messages_limit} messages de cette démonstration. Le code complet est sur GitHub.`;
}

function setBusy(b) {
  state.busy = b;
  $("send").disabled = b;
  document.querySelectorAll(".slip:not(.done) button").forEach((x) => { x.disabled = b; });
}

async function send(text) {
  if (state.busy || !text.trim()) return;
  $("suggestions").hidden = true;
  addMessage("user", text);
  const wait = addMessage("note", "L'assistant travaille…");
  wait.classList.add("thinking");
  setBusy(true);
  try {
    const turn = await api(`/api/sessions/${state.session}/messages`, { method: "POST", body: JSON.stringify({ text }) });
    wait.remove();
    showTurn(turn);
    refreshSide();
  } catch (e) { wait.textContent = e.message; wait.classList.remove("thinking"); }
  finally { setBusy(false); }
}

async function decide(actionId, confirm, node) {
  if (state.busy) return;
  setBusy(true);
  try {
    const turn = await api(`/api/sessions/${state.session}/actions/${actionId}`,
      { method: "POST", body: JSON.stringify({ confirm }) });
    const d = turn.decision || {};
    closeSlip(node, confirm && d.ok, !confirm ? "Annulée : rien n'a été modifié."
      : d.ok ? "Confirmée et exécutée." : `Échec : ${(d.result && d.result.error) || "action impossible"}`);
    showTurn(turn);
    refreshSide();
  } catch (e) { closeSlip(node, false, e.message); }
  finally { setBusy(false); }
}

async function download(url) {
  const res = await fetch(url, { headers: { authorization: `Bearer ${state.token}` } });
  if (!res.ok) { addMessage("note", "Téléchargement impossible."); return; }
  const blob = await res.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = url.split("/").pop() + ".pdf";
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 10_000);
}

/* ---------------------------------------------------------------- side panel */

function refreshSide() { loadWeek(); loadInbox(); loadAudit(); }

function isoDate(d) { return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; }
function parseLocal(s) { const [d, t] = s.split("T"); const [y, m, dd] = d.split("-").map(Number); const [h, mi] = (t || "0:0").split(":").map(Number); return new Date(y, m - 1, dd, h, mi); }

async function loadWeek() {
  const q = state.weekStart ? `?start=${state.weekStart}` : "";
  let data;
  try { data = await api(`/api/calendar${q}`); } catch (_) { return; }
  state.weekStart = data.from;
  const now = parseLocal(data.now);
  const monday = parseLocal(`${data.from}T00:00`);
  $("week-label").textContent = `Semaine du ${monday.toLocaleDateString("fr-FR", { day: "numeric", month: "long" })}`;
  const grid = $("week");
  grid.textContent = "";
  grid.append(document.createElement("div"));
  const days = [...Array(5).keys()].map((i) => new Date(monday.getFullYear(), monday.getMonth(), monday.getDate() + i));
  for (const d of days) {
    const h = document.createElement("div");
    h.className = "dayhead" + (isoDate(d) === isoDate(now) ? " today" : "");
    h.textContent = d.toLocaleDateString("fr-FR", { weekday: "short", day: "numeric" });
    grid.append(h);
  }
  const hours = document.createElement("div");
  hours.className = "hours";
  for (let h = DAY_START; h <= DAY_END; h += 1) {
    const s = document.createElement("span"); s.textContent = `${h} h`;
    s.style.top = `${(h - DAY_START) * PX_PER_HOUR}px`;
    hours.append(s);
  }
  grid.append(hours);
  const cols = days.map((d) => {
    const c = document.createElement("div"); c.className = "col"; c.dataset.day = isoDate(d); grid.append(c); return c;
  });
  const place = (el, startStr, endStr) => {
    const s = parseLocal(startStr), e = parseLocal(endStr);
    const col = cols.find((c) => c.dataset.day === isoDate(s));
    if (!col) return;
    const top = (s.getHours() + s.getMinutes() / 60 - DAY_START) * PX_PER_HOUR;
    const height = Math.max(14, ((e - s) / 3_600_000) * PX_PER_HOUR - 2);
    el.style.top = `${Math.max(0, top)}px`; el.style.height = `${height}px`;
    col.append(el);
  };
  for (const ev of data.events || []) {
    const el = document.createElement("div");
    el.className = "ev";
    el.style.background = `var(--k-${ev.kind}, var(--k-autre))`;
    el.title = `${ev.title}\n${ev.when}${ev.room ? "\n" + ev.room.name : ""}`;
    el.innerHTML = "<b></b><small></small>";
    el.querySelector("b").textContent = ev.course_id || ev.title;
    el.querySelector("small").textContent = `${KINDS[ev.kind] || ev.kind}${ev.room ? " · " + ev.room.id : ""}`;
    place(el, ev.start, ev.end);
  }
  for (const b of data.busy || []) {
    const el = document.createElement("div"); el.className = "ev busy"; el.textContent = "Occupé";
    place(el, b.start, b.end);
  }
  const col = cols.find((c) => c.dataset.day === isoDate(now));
  if (col && now.getHours() >= DAY_START && now.getHours() < DAY_END) {
    const line = document.createElement("div"); line.className = "now";
    line.style.top = `${(now.getHours() + now.getMinutes() / 60 - DAY_START) * PX_PER_HOUR}px`;
    col.append(line);
  }
  if (!$("legend")) {
    const legend = document.createElement("div"); legend.id = "legend"; legend.className = "legend";
    for (const [k, label] of Object.entries(KINDS)) {
      const span = document.createElement("span"); span.innerHTML = "<i></i>";
      span.querySelector("i").style.background = `var(--k-${k})`;
      span.append(label); legend.append(span);
    }
    $("panel-week").append(legend);
  }
}

function shiftWeek(delta) {
  const d = parseLocal(`${state.weekStart}T00:00`);
  d.setDate(d.getDate() + 7 * delta);
  state.weekStart = isoDate(d);
  loadWeek();
}

async function loadInbox() {
  let data;
  try { data = await api("/api/messages"); } catch (_) { return; }
  const ul = $("inbox");
  ul.textContent = "";
  $("inbox-count").textContent = data.received.length ? `(${data.received.length})` : "";
  if (!data.received.length) { ul.innerHTML = "<li class='meta'>Aucun message pour l'instant.</li>"; return; }
  for (const m of data.received) {
    const li = document.createElement("li");
    li.innerHTML = "<div class='subj'></div><div class='meta'></div><div class='body'></div>";
    li.querySelector(".subj").textContent = m.subject;
    li.querySelector(".meta").textContent = `${m.from}, ${m.date.replace("T", " à ")}`;
    li.querySelector(".body").textContent = m.body;
    ul.append(li);
  }
}

async function loadAudit() {
  let data;
  try { data = await api("/api/audit"); } catch (_) { return; }
  const ul = $("audit");
  ul.textContent = "";
  for (const e of data.entries) {
    const li = document.createElement("li");
    li.innerHTML = "<span class='outcome'></span> <code></code><div class='meta'></div>";
    const o = li.querySelector(".outcome"); o.textContent = e.outcome; o.classList.add(`o-${e.outcome}`);
    li.querySelector("code").textContent = e.tool;
    li.querySelector(".meta").textContent = `${e.ts.replace("T", " ")}, ${e.actor_id}, via ${e.channel}${e.detail ? " : " + e.detail : ""}`;
    ul.append(li);
  }
}

/* ---------------------------------------------------------------- wiring */

document.addEventListener("DOMContentLoaded", async () => {
  $("composer").addEventListener("submit", (e) => { e.preventDefault(); const t = $("input").value; $("input").value = ""; send(t); });
  $("input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("composer").requestSubmit(); }
  });
  $("switch").addEventListener("click", () => signOut());
  $("prev-week").addEventListener("click", () => shiftWeek(-1));
  $("next-week").addEventListener("click", () => shiftWeek(1));
  document.querySelectorAll(".tabs button").forEach((tab) => tab.addEventListener("click", () => {
    document.querySelectorAll(".tabs button").forEach((t) => t.setAttribute("aria-selected", String(t === tab)));
    for (const name of ["week", "inbox", "audit"]) $(`panel-${name}`).hidden = name !== tab.dataset.tab;
  }));
  state.token = load("token");
  if (state.token) {
    try { await startWorkspace(false); return; } catch (_) { state.token = null; }
  }
  showLanding();
});
