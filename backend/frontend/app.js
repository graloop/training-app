const TOKEN_KEY = "at_token";
const EMAIL_KEY = "at_email";

function getToken() {
  return localStorage.getItem(TOKEN_KEY);
}

function setSession(token, email) {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(EMAIL_KEY, email);
}

function clearSession() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(EMAIL_KEY);
}

async function api(path, options = {}) {
  const headers = options.headers || {};
  headers["Content-Type"] = "application/json";
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(path, { ...options, headers });
  // A 401 without a token is a failed login, not an expired session.
  if (res.status === 401 && token) {
    const message = "Your session expired, please log in again.";
    clearSession();
    showAuthView();
    const errorEl = document.getElementById("auth-error");
    errorEl.textContent = message;
    errorEl.classList.remove("hidden");
    throw new Error(message);
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch (_) {
      /* ignore */
    }
    throw new Error(detail);
  }
  if (res.status === 204) return null;
  return res.json();
}

// --- Auth view ---

function showAuthView() {
  document.getElementById("auth-view").classList.remove("hidden");
  document.getElementById("app-view").classList.add("hidden");
  document.getElementById("user-actions").classList.add("hidden");
}

function showAppView() {
  document.getElementById("auth-view").classList.add("hidden");
  document.getElementById("app-view").classList.remove("hidden");
  document.getElementById("user-actions").classList.remove("hidden");
  document.getElementById("user-email").textContent = localStorage.getItem(EMAIL_KEY) || "";
  loadAll();
}

document.getElementById("auth-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const email = document.getElementById("auth-email").value.trim();
  const password = document.getElementById("auth-password").value;
  const errorEl = document.getElementById("auth-error");
  const submitBtn = document.getElementById("auth-submit");
  errorEl.classList.add("hidden");
  submitBtn.disabled = true;
  submitBtn.textContent = "Logging in to Garmin...";

  try {
    const data = await api("/api/auth/login", { method: "POST", body: JSON.stringify({ email, password }) });
    document.getElementById("auth-password").value = "";
    setSession(data.access_token, email);
    showAppView();
    runSync();
  } catch (err) {
    errorEl.textContent = err.message;
    errorEl.classList.remove("hidden");
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = "Log in";
  }
});

document.getElementById("logout-btn").addEventListener("click", () => {
  setMenuOpen(false);
  clearSession();
  showAuthView();
});

// --- Settings menu (AI provider + API key) ---

const settingsBtn = document.getElementById("settings-btn");
const settingsMenu = document.getElementById("settings-menu");
const aiProvider = document.getElementById("ai-provider");
const aiModel = document.getElementById("ai-model");
const aiKey = document.getElementById("ai-key");
const aiKeyState = document.getElementById("ai-key-state");
const aiKeyLink = document.getElementById("ai-key-link");
const aiClear = document.getElementById("ai-clear");
const aiStatus = document.getElementById("ai-status");
const aiCustomFields = document.getElementById("ai-custom-fields");
const aiBaseUrl = document.getElementById("ai-base-url");
const aiFormat = document.getElementById("ai-format");

let aiSettings = null;

function setMenuOpen(open) {
  settingsMenu.classList.toggle("hidden", !open);
  settingsBtn.setAttribute("aria-expanded", String(open));
  if (open) {
    aiStatus.classList.add("hidden");
    if (aiSettings) renderAISettings();
    aiProvider.focus();
  }
}

settingsBtn.addEventListener("click", () => setMenuOpen(settingsMenu.classList.contains("hidden")));
document.addEventListener("click", (e) => {
  if (!settingsMenu.classList.contains("hidden") && !e.target.closest(".menu")) setMenuOpen(false);
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !settingsMenu.classList.contains("hidden")) {
    setMenuOpen(false);
    settingsBtn.focus();
  }
});

function selectedProvider() {
  return aiSettings.providers.find((p) => p.id === aiProvider.value);
}

// Placeholders and key status for whichever provider is selected in the
// dropdown, which may not be the saved one yet.
function renderProviderFields() {
  const provider = selectedProvider();
  const isSaved = provider.id === aiSettings.provider;
  aiModel.placeholder = provider.custom ? "Model name (required)" : provider.default_model;
  aiKeyLink.href = provider.key_url;
  aiKeyLink.classList.toggle("hidden", !provider.key_url);
  aiCustomFields.classList.toggle("hidden", !provider.custom);

  if (isSaved && aiSettings.has_api_key) {
    aiKey.placeholder = `•••• ${aiSettings.api_key_hint}`;
    aiKeyState.textContent = "Key saved. Leave empty to keep it.";
  } else if (isSaved && aiSettings.using_server_key) {
    aiKey.placeholder = "Optional";
    aiKeyState.textContent = "Using the server's Claude key.";
  } else {
    aiKey.placeholder = `Paste your ${provider.label} key`;
    aiKeyState.textContent = isSaved ? "No key saved." : "Switching provider removes the current key.";
  }
  aiClear.classList.toggle("hidden", !(isSaved && aiSettings.has_api_key));
}

function renderAISettings() {
  aiProvider.innerHTML = "";
  for (const p of aiSettings.providers) {
    const opt = document.createElement("option");
    opt.value = p.id;
    opt.textContent = p.label;
    aiProvider.appendChild(opt);
  }
  aiProvider.value = aiSettings.provider;
  aiModel.value = aiSettings.custom_model ? aiSettings.model : "";
  aiBaseUrl.value = aiSettings.base_url || "";
  aiFormat.value = aiSettings.api_format || "openai";
  aiKey.value = "";
  renderProviderFields();
  renderChatLabel();
}

function renderChatLabel() {
  const label = document.getElementById("chat-ai-label");
  const provider = aiSettings.providers.find((p) => p.id === aiSettings.provider);
  const ready = aiSettings.has_api_key || aiSettings.using_server_key;
  const name = provider.custom && aiSettings.base_url ? `Custom (${new URL(aiSettings.base_url).host})` : provider.label;
  label.textContent = ready
    ? `· ${name} · ${aiSettings.model}`
    : `· no API key for ${provider.label}, add one in ⚙️ Settings`;
}

aiProvider.addEventListener("change", () => {
  aiModel.value = "";
  renderProviderFields();
});

async function saveAISettings(body) {
  try {
    aiSettings = await api("/api/ai-settings", { method: "PUT", body: JSON.stringify(body) });
    renderAISettings();
    showStatus(aiStatus, "Saved.");
  } catch (err) {
    showStatus(aiStatus, err.message, true);
  }
}

document.getElementById("ai-save").addEventListener("click", () =>
  saveAISettings({
    provider: aiProvider.value,
    model: aiModel.value,
    api_key: aiKey.value || null,
    base_url: aiBaseUrl.value,
    api_format: aiFormat.value,
  })
);
aiKey.addEventListener("keydown", (e) => {
  if (e.key === "Enter") document.getElementById("ai-save").click();
});
aiClear.addEventListener("click", () =>
  saveAISettings({
    provider: aiSettings.provider,
    model: aiModel.value,
    clear_api_key: true,
    base_url: aiBaseUrl.value,
    api_format: aiFormat.value,
  })
);

async function loadAISettings() {
  aiSettings = await api("/api/ai-settings");
  renderAISettings();
}

// --- Calendar ---

function fmtDate(d) {
  return new Date(d + "T00:00:00").toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}

const ACTIVITY_ICONS = [
  ["swim", "🏊"],
  ["run", "🏃"],
  ["walk", "🚶"],
  ["hik", "🥾"],
  ["bik", "🚴"],
  ["cycl", "🚴"],
  ["ride", "🚴"], // Garmin's typeKey for Zwift is "virtual_ride"
  ["zwift", "🚴"],
  ["strength", "🏋️"],
  ["yoga", "🧘"],
  ["row", "🚣"],
  ["ski", "⛷️"],
  ["snowboard", "🏂"],
  ["padd", "🛶"],
  ["elliptical", "🌀"],
  ["rest", "😴"],
];

function activityIcon(type) {
  const key = (type || "").toLowerCase();
  for (const [needle, icon] of ACTIVITY_ICONS) {
    if (key.includes(needle)) return icon;
  }
  return "🏅";
}

function makeEntry(className, type, detail) {
  const entry = document.createElement("div");
  entry.className = `entry ${className}`;
  entry.title = type;

  const icon = document.createElement("span");
  icon.className = "entry-icon";
  icon.textContent = activityIcon(type);
  entry.appendChild(icon);

  const detailEl = document.createElement("span");
  detailEl.className = "entry-detail";
  detailEl.textContent = detail;
  entry.appendChild(detailEl);

  return entry;
}

function isoDate(d) {
  return [d.getFullYear(), d.getMonth() + 1, d.getDate()].map((n) => String(n).padStart(2, "0")).join("-");
}

function addDays(d, n) {
  const copy = new Date(d);
  copy.setDate(copy.getDate() + n);
  return copy;
}

// Whole Monday–Sunday weeks covering roughly 2 weeks back and 2 weeks ahead.
function calendarRange() {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const from = addDays(today, -14);
  const to = addDays(today, 14);
  const start = addDays(from, -((from.getDay() + 6) % 7)); // back to Monday
  const end = addDays(to, (7 - to.getDay()) % 7); // forward to Sunday
  return { start: isoDate(start), end: isoDate(end) };
}

function fmtHours(minutes) {
  const total = Math.round(minutes || 0);
  if (total < 60) return `${total} min`;
  const h = Math.floor(total / 60);
  const m = total % 60;
  return m ? `${h} h ${String(m).padStart(2, "0")}` : `${h} h`;
}

function sleepLevel(score) {
  if (score >= 80) return "good";
  if (score >= 60) return "fair";
  return "poor";
}

function makeSleepBadge(sleep) {
  if (!sleep || sleep.sleep_score == null) return null;
  const score = Math.round(sleep.sleep_score);
  const badge = document.createElement("span");
  badge.className = `sleep-badge ${sleepLevel(score)}`;
  badge.textContent = `😴 ${score}`;
  const hours = sleep.total_sleep_seconds ? ` · ${fmtHours(sleep.total_sleep_seconds / 60)} of sleep` : "";
  badge.title = `Sleep score ${score}${hours}`;
  return badge;
}

function renderWeekSummary(week) {
  let plannedMin = 0;
  let doneMin = 0;
  const sleepScores = [];
  for (const day of week) {
    for (const p of day.planned) plannedMin += p.planned_duration_minutes || 0;
    for (const a of day.activities) doneMin += (a.duration_seconds || 0) / 60;
    if (day.sleep && day.sleep.sleep_score != null) sleepScores.push(day.sleep.sleep_score);
  }

  const cell = document.createElement("div");
  cell.className = "week-summary";

  const title = document.createElement("div");
  title.className = "week-title";
  title.textContent = `Week of ${new Date(week[0].date + "T00:00:00").toLocaleDateString(undefined, { month: "short", day: "numeric" })}`;
  cell.appendChild(title);

  for (const [label, minutes, cls] of [["Planned", plannedMin, "planned"], ["Done", doneMin, "done"]]) {
    const row = document.createElement("div");
    row.className = `week-row ${cls}`;
    const name = document.createElement("span");
    name.textContent = label;
    const value = document.createElement("strong");
    value.textContent = fmtHours(minutes);
    row.append(name, value);
    cell.appendChild(row);
  }

  const sleepRow = document.createElement("div");
  sleepRow.className = "week-row sleep";
  const sleepName = document.createElement("span");
  sleepName.textContent = "Sleep avg";
  const sleepValue = document.createElement("strong");
  if (sleepScores.length) {
    const avg = Math.round(sleepScores.reduce((a, b) => a + b, 0) / sleepScores.length);
    sleepValue.textContent = `😴 ${avg}`;
    sleepValue.className = sleepLevel(avg);
    sleepRow.title = `Average sleep score over ${sleepScores.length} night${sleepScores.length > 1 ? "s" : ""}`;
  } else {
    sleepValue.textContent = "—";
    sleepRow.title = "No sleep data this week";
  }
  sleepRow.append(sleepName, sleepValue);
  cell.appendChild(sleepRow);

  if (plannedMin > 0) {
    const pct = Math.round((doneMin / plannedMin) * 100);
    const bar = document.createElement("div");
    bar.className = "week-bar";
    bar.title = `${pct}% of planned time done`;
    const fill = document.createElement("div");
    fill.style.width = `${Math.min(pct, 100)}%`;
    bar.appendChild(fill);
    cell.appendChild(bar);
  }
  return cell;
}

function renderCalendar(data) {
  const grid = document.getElementById("calendar-grid");
  grid.innerHTML = "";
  const today = isoDate(new Date());

  for (const label of ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun", "Week total"]) {
    const head = document.createElement("div");
    head.className = "grid-head";
    head.textContent = label;
    grid.appendChild(head);
  }

  for (let i = 0; i < data.days.length; i += 7) {
    const week = data.days.slice(i, i + 7);
    for (const day of week) {
      const cell = document.createElement("div");
      cell.className = "day-cell" + (day.date === today ? " today" : "") + (day.date < today ? " past" : "");

      const head = document.createElement("div");
      head.className = "day-head";
      const dateEl = document.createElement("span");
      dateEl.className = "day-date";
      dateEl.textContent = fmtDate(day.date);
      head.appendChild(dateEl);
      const sleepBadge = makeSleepBadge(day.sleep);
      if (sleepBadge) head.appendChild(sleepBadge);
      cell.appendChild(head);

      for (const a of day.activities) {
        const detail = a.duration_seconds ? fmtHours(a.duration_seconds / 60) : "";
        cell.appendChild(makeEntry("done", a.activity_type, detail));
      }

      for (const p of day.planned) {
        const detail = p.planned_duration_minutes ? fmtHours(p.planned_duration_minutes) : "";
        const entry = makeEntry("planned", p.activity_type, detail);
        entry.tabIndex = 0;
        entry.setAttribute("role", "button");
        entry.title = `${sportLabel(p.activity_type)} · planned${p.source === "ai" ? " by the coach" : ""}. Click to edit.`;
        entry.addEventListener("click", () => openPlanDialog(day.date, p));
        entry.addEventListener("keydown", (e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            openPlanDialog(day.date, p);
          }
        });
        cell.appendChild(entry);
      }

      const add = document.createElement("button");
      add.className = "add-btn";
      add.type = "button";
      add.title = "Plan a session";
      add.setAttribute("aria-label", `Plan a session on ${fmtDate(day.date)}`);
      add.addEventListener("click", () => openPlanDialog(day.date));
      cell.appendChild(add);

      grid.appendChild(cell);
    }
    grid.appendChild(renderWeekSummary(week));
  }
}

async function loadCalendar() {
  const { start, end } = calendarRange();
  const data = await api(`/api/calendar?start=${start}&end=${end}`);
  renderCalendar(data);
}

// --- Plan-a-session dialog ---

const SPORTS = [
  { type: "swimming", label: "Swimming" },
  { type: "cycling", label: "Cycling" },
  { type: "running", label: "Running" },
  { type: "strength_training", label: "Strength" },
];

function sportLabel(type) {
  const sport = SPORTS.find((s) => s.type === type);
  return sport ? sport.label : (type || "").replace(/_/g, " ");
}

const planDialog = document.getElementById("plan-dialog");
const planForm = document.getElementById("plan-form");
const planSports = document.getElementById("plan-sports");
const planHours = document.getElementById("plan-hours");
const planMinutes = document.getElementById("plan-minutes");
const planError = document.getElementById("plan-error");
const planDelete = document.getElementById("plan-delete");
let planTarget = null; // { date, existing }

function renderSportOptions(extraType) {
  planSports.innerHTML = "";
  const sports = [...SPORTS];
  if (extraType && !SPORTS.some((s) => s.type === extraType)) sports.push({ type: extraType, label: sportLabel(extraType) });
  for (const sport of sports) {
    const label = document.createElement("label");
    label.className = "sport-option";
    const input = document.createElement("input");
    input.type = "radio";
    input.name = "sport";
    input.value = sport.type;
    input.required = true;
    const icon = document.createElement("span");
    icon.className = "sport-icon";
    icon.textContent = activityIcon(sport.type);
    const text = document.createElement("span");
    text.textContent = sport.label;
    label.append(input, icon, text);
    planSports.appendChild(label);
  }
}

function openPlanDialog(date, existing = null) {
  planTarget = { date, existing };
  renderSportOptions(existing && existing.activity_type);
  document.getElementById("plan-title").textContent = existing ? "Edit session" : "Plan a session";
  document.getElementById("plan-date").textContent = new Date(date + "T00:00:00").toLocaleDateString(undefined, {
    weekday: "long",
    month: "long",
    day: "numeric",
  });

  const minutes = existing ? Math.round(existing.planned_duration_minutes || 0) : 60;
  planHours.value = Math.floor(minutes / 60);
  planMinutes.value = minutes % 60;
  const checked = planSports.querySelector(`input[value="${existing ? existing.activity_type : ""}"]`);
  if (checked) checked.checked = true;

  planDelete.classList.toggle("hidden", !existing);
  planError.classList.add("hidden");
  planDialog.showModal();
  (checked || planSports.querySelector("input")).focus();
}

planForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const sport = planForm.querySelector('input[name="sport"]:checked');
  const minutes = (Number(planHours.value) || 0) * 60 + (Number(planMinutes.value) || 0);
  if (!sport) return showStatus(planError, "Pick a sport.", true);
  if (minutes <= 0) return showStatus(planError, "Set how long the session is.", true);

  const body = { activity_type: sport.value, planned_duration_minutes: minutes };
  try {
    if (planTarget.existing) {
      await api(`/api/planned/${planTarget.existing.id}`, { method: "PATCH", body: JSON.stringify(body) });
    } else {
      await api("/api/planned", { method: "POST", body: JSON.stringify({ ...body, date: planTarget.date }) });
    }
    planDialog.close();
    await loadCalendar();
  } catch (err) {
    showStatus(planError, err.message, true);
  }
});

planDelete.addEventListener("click", async () => {
  try {
    await api(`/api/planned/${planTarget.existing.id}`, { method: "DELETE" });
    planDialog.close();
    await loadCalendar();
  } catch (err) {
    showStatus(planError, err.message, true);
  }
});

document.getElementById("plan-cancel").addEventListener("click", () => planDialog.close());
planDialog.addEventListener("click", (e) => {
  if (e.target === planDialog) planDialog.close(); // click on the backdrop
});

// --- Garmin sync ---

const syncBtn = document.getElementById("sync-btn");
const syncStatus = document.getElementById("sync-status");

function showStatus(el, text, isError = false) {
  el.textContent = text;
  el.classList.remove("hidden");
  el.classList.toggle("error", isError);
  el.classList.toggle("status", !isError);
}

async function runSync() {
  syncBtn.disabled = true;
  showStatus(syncStatus, "Syncing with Garmin...");
  try {
    const result = await api("/api/garmin/sync", { method: "POST" });
    showStatus(
      syncStatus,
      `Synced ${result.activities_synced} activities and ${result.sleep_records_synced} sleep records.`
    );
    await loadCalendar();
  } catch (err) {
    showStatus(syncStatus, err.message, true);
  } finally {
    syncBtn.disabled = false;
  }
}

syncBtn.addEventListener("click", runSync);

// --- Objectives (set by the coach, editable here) ---

const objectivesList = document.getElementById("objectives-list");

function fmtEventDate(iso) {
  return new Date(iso + "T00:00:00").toLocaleDateString(undefined, {
    weekday: "short",
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function daysUntil(iso) {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const days = Math.round((new Date(iso + "T00:00:00") - today) / 86400000);
  if (days === 0) return "today";
  if (days === 1) return "tomorrow";
  if (days > 1) return `in ${days} days`;
  return days === -1 ? "yesterday" : `${-days} days ago`;
}

function objectiveField(label, value) {
  const row = document.createElement("div");
  row.className = "objective-field";
  const name = document.createElement("span");
  name.className = "objective-label";
  name.textContent = label;
  row.append(name, value);
  return row;
}

function renderObjectiveView(card, obj) {
  card.innerHTML = "";
  const title = document.createElement("h3");
  title.textContent = obj.title;
  card.appendChild(title);

  const date = document.createElement("span");
  if (obj.event_date) {
    date.textContent = fmtEventDate(obj.event_date);
    const countdown = document.createElement("span");
    countdown.className = "subtle";
    countdown.textContent = ` · ${daysUntil(obj.event_date)}`;
    date.appendChild(countdown);
  } else {
    date.textContent = "Not set";
    date.className = "subtle";
  }
  card.appendChild(objectiveField("Event date", date));

  const target = document.createElement("span");
  target.textContent = obj.target_time || "Not set";
  if (!obj.target_time) target.className = "subtle";
  card.appendChild(objectiveField("Target time", target));

  const actions = document.createElement("div");
  actions.className = "objective-actions";
  const edit = document.createElement("button");
  edit.type = "button";
  edit.className = "btn-link";
  edit.textContent = "Edit";
  edit.addEventListener("click", () => renderObjectiveEdit(card, obj));
  const del = document.createElement("button");
  del.type = "button";
  del.className = "btn-link danger";
  del.textContent = "Delete";
  del.addEventListener("click", async () => {
    if (!confirm(`Delete the objective "${obj.title}"?`)) return;
    await api(`/api/objectives/${obj.id}`, { method: "DELETE" });
    await loadObjectives();
  });
  actions.append(edit, del);
  card.appendChild(actions);
}

function renderObjectiveEdit(card, obj) {
  card.innerHTML = "";
  const form = document.createElement("form");
  form.className = "objective-form";
  form.noValidate = true;

  const fields = [
    ["Title", "text", "title", obj.title, "e.g. Montreal half marathon"],
    ["Event date", "date", "event_date", obj.event_date || "", ""],
    ["Target time", "text", "target_time", obj.target_time || "", "e.g. 1:45:00"],
  ];
  const inputs = {};
  for (const [label, type, key, value, placeholder] of fields) {
    const id = `obj-${obj.id}-${key}`;
    const l = document.createElement("label");
    l.htmlFor = id;
    l.textContent = label;
    const input = document.createElement("input");
    input.id = id;
    input.type = type;
    input.value = value;
    input.placeholder = placeholder;
    inputs[key] = input;
    form.append(l, input);
  }
  inputs.title.required = true;

  const error = document.createElement("p");
  error.className = "error hidden";
  error.setAttribute("role", "alert");
  const actions = document.createElement("div");
  actions.className = "objective-actions";
  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.className = "btn-ghost";
  cancel.textContent = "Cancel";
  cancel.addEventListener("click", () => renderObjectiveView(card, obj));
  const save = document.createElement("button");
  save.type = "submit";
  save.textContent = "Save";
  actions.append(cancel, save);
  form.append(error, actions);

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!inputs.title.value.trim()) return showStatus(error, "Give the objective a title.", true);
    try {
      await api(`/api/objectives/${obj.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          title: inputs.title.value.trim(),
          event_date: inputs.event_date.value || null,
          target_time: inputs.target_time.value.trim(),
        }),
      });
      await loadObjectives();
    } catch (err) {
      showStatus(error, err.message, true);
    }
  });

  card.appendChild(form);
  inputs.title.focus();
}

function renderObjectives(objectives) {
  objectivesList.innerHTML = "";
  if (!objectives.length) {
    const empty = document.createElement("p");
    empty.className = "subtle";
    empty.textContent =
      'No objective yet. Tell the coach about your next event, e.g. "My goal is the Montreal half marathon on April 20, aiming for 1:45:00."';
    objectivesList.appendChild(empty);
    return;
  }
  for (const obj of objectives) {
    const card = document.createElement("div");
    card.className = "objective-card";
    renderObjectiveView(card, obj);
    objectivesList.appendChild(card);
  }
}

async function loadObjectives() {
  const data = await api("/api/objectives");
  renderObjectives(data.objectives);
}

// --- Chat ---

function appendChatMessage(role, content) {
  const log = document.getElementById("chat-log");
  const el = document.createElement("div");
  el.className = `chat-msg ${role}`;
  el.textContent = content;
  log.appendChild(el);
  log.scrollTop = log.scrollHeight;
}

async function loadChatHistory() {
  const data = await api("/api/chat/history");
  const log = document.getElementById("chat-log");
  log.innerHTML = "";
  for (const m of data.messages) {
    appendChatMessage(m.role, m.content);
  }
}

document.getElementById("chat-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = document.getElementById("chat-input");
  const message = input.value.trim();
  if (!message) return;
  input.value = "";
  appendChatMessage("user", message);

  try {
    const data = await api("/api/chat", { method: "POST", body: JSON.stringify({ message }) });
    appendChatMessage("assistant", data.reply);
    // The coach may have set or changed an objective.
    const refresh = [loadObjectives()];
    if (data.plan_changes && data.plan_changes.length > 0) refresh.push(loadCalendar());
    await Promise.all(refresh);
  } catch (err) {
    appendChatMessage("assistant", `Error: ${err.message}`);
  }
});

// --- Init ---

async function loadAll() {
  await Promise.all([loadCalendar(), loadObjectives(), loadChatHistory(), loadAISettings()]);
}

if (getToken()) {
  showAppView();
} else {
  showAuthView();
}
