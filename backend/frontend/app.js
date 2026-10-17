const TOKEN_KEY = "at_token";

function getToken() {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch (_) {
    return null;
  }
}

function setSession(token) {
  try {
    localStorage.setItem(TOKEN_KEY, token);
  } catch (_) {
    /* private mode: the session lasts until the tab closes */
  }
  memoryToken = token;
}

function clearSession() {
  try {
    localStorage.removeItem(TOKEN_KEY);
  } catch (_) {
    /* ignore */
  }
  memoryToken = null;
  me = null;
}

let memoryToken = null; // fallback when localStorage is unavailable
let me = null; // { id, username, is_admin, garmin_connected, garmin_email }

async function api(path, options = {}) {
  const headers = options.headers || {};
  headers["Content-Type"] = "application/json";
  const token = getToken() || memoryToken;
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
      // Not the app's answer: Cloudflare's own page when the server is off or took too long (502, 504, 524...).
      if (res.status >= 500) detail = `The server didn't answer (error ${res.status}). Try again in a minute.`;
    }
    if (!detail) detail = `Error ${res.status}`;
    if (typeof detail !== "string") detail = detail.map((d) => d.msg).join("; "); // validation errors
    const error = new Error(detail);
    error.status = res.status;
    throw error;
  }
  if (res.status === 204) return null;
  return res.json();
}

// --- Auth view ---

const VIEWS = ["auth-view", "app-view", "admin-view"];

function showView(id) {
  for (const view of VIEWS) document.getElementById(view).classList.toggle("hidden", view !== id);
  document.getElementById("user-actions").classList.toggle("hidden", id === "auth-view");
}

function showAuthView() {
  showView("auth-view");
}

// Regular accounts get the calendar; admin accounts get the dashboard instead.
async function showAppView({ sync = false } = {}) {
  try {
    me = await api("/api/auth/me");
  } catch (err) {
    if (err.status !== 401) {
      showAuthView();
      showStatus(document.getElementById("auth-error"), err.message, true);
    }
    return;
  }
  // Nothing from a previous session should linger (status lines, half-typed forms).
  for (const el of document.querySelectorAll(
    "#app-view .status, #app-view .error, #admin-view .status, #admin-view .error, #settings-menu .status"
  ))
    el.classList.add("hidden");
  document.getElementById("garmin-password").value = "";
  document.getElementById("user-name").textContent = me.username + (me.is_admin ? " · admin" : "");
  document.getElementById("garmin-settings").classList.toggle("hidden", me.is_admin);

  if (me.is_admin) {
    showView("admin-view");
    document.getElementById("new-password").value = randomPassword();
    await loadDashboard();
    return;
  }
  weekOffset = 0;
  showView("app-view");
  renderGarminState();
  const hash = location.hash === "#logs" ? "#history" : location.hash; // the History tab used to be "Logs"
  showTab(TABS.find((tab) => `#${tab}` === hash) || "calendar", { load: false });
  await loadAll();
  if (currentTab === "athlete") await loadAthletePage();
  if (currentTab === "plan") await loadPlanPage();
  if (currentTab === "import") await loadImportPage();
  if (currentTab === "history") await loadHistoryPage();
  if (sync && me.garmin_connected) runSync();
}

document.getElementById("auth-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const username = document.getElementById("auth-username").value.trim();
  const password = document.getElementById("auth-password").value;
  const errorEl = document.getElementById("auth-error");
  const submitBtn = document.getElementById("auth-submit");
  errorEl.classList.add("hidden");
  submitBtn.disabled = true;
  submitBtn.textContent = "Logging in…";

  try {
    const data = await api("/api/auth/login", { method: "POST", body: JSON.stringify({ username, password }) });
    document.getElementById("auth-password").value = "";
    setSession(data.access_token);
    await showAppView({ sync: true });
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

// --- Settings menu (Garmin connection, password, log out) ---

const settingsBtn = document.getElementById("settings-btn");
const settingsMenu = document.getElementById("settings-menu");

function setMenuOpen(open) {
  settingsMenu.classList.toggle("hidden", !open);
  settingsBtn.setAttribute("aria-expanded", String(open));
  if (open) {
    document.getElementById("pw-status").classList.add("hidden");
    document.getElementById("pw-current").focus();
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

// --- Garmin connection ---

const garminPanel = document.getElementById("garmin-panel");
const garminForm = document.getElementById("garmin-form");
const garminError = document.getElementById("garmin-error");

function renderGarminState(reason = "") {
  const connected = me && me.garmin_connected && !reason;
  garminPanel.classList.toggle("hidden", connected);
  document.getElementById("garmin-reason").textContent =
    reason || "Connect Garmin so your activities and sleep show up on the calendar.";
  document.getElementById("garmin-state").textContent =
    me && me.garmin_connected ? `Connected as ${me.garmin_email}.` : "Not connected.";
  document.getElementById("garmin-disconnect").classList.toggle("hidden", !(me && me.garmin_connected));
  syncBtn.disabled = !(me && me.garmin_connected);
  syncBtn.title = me && me.garmin_connected ? "" : "Connect Garmin first";
  if (!connected && me && me.garmin_email) document.getElementById("garmin-email").value = me.garmin_email;
}

function showGarminConnect(reason) {
  renderGarminState(reason);
  garminPanel.scrollIntoView({ behavior: "smooth", block: "start" });
  document.getElementById("garmin-password").focus({ preventScroll: true });
}

garminForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const submit = document.getElementById("garmin-submit");
  garminError.classList.add("hidden");
  submit.disabled = true;
  submit.textContent = "Logging in to Garmin…";
  try {
    me = await api("/api/garmin/connect", {
      method: "POST",
      body: JSON.stringify({
        garmin_email: document.getElementById("garmin-email").value.trim(),
        garmin_password: document.getElementById("garmin-password").value,
      }),
    });
    document.getElementById("garmin-password").value = "";
    renderGarminState();
    runSync();
  } catch (err) {
    showStatus(garminError, err.message, true);
  } finally {
    submit.disabled = false;
    submit.textContent = "Connect Garmin";
  }
});

document.getElementById("garmin-reconnect").addEventListener("click", () => {
  setMenuOpen(false);
  showGarminConnect("Log in to Garmin again to refresh the connection.");
});

document.getElementById("garmin-disconnect").addEventListener("click", async () => {
  if (!confirm("Disconnect Garmin? Data already synced stays on the calendar.")) return;
  try {
    me = await api("/api/garmin/connect", { method: "DELETE" });
  } catch (err) {
    if (err.status !== 401) alert(`Couldn't disconnect Garmin: ${err.message}`);
    return;
  }
  setMenuOpen(false);
  renderGarminState();
});

// --- Change own password ---

document.getElementById("password-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const status = document.getElementById("pw-status");
  const current = document.getElementById("pw-current");
  const next = document.getElementById("pw-new");
  if (next.value.length < 8) return showStatus(status, "The new password needs 8+ characters.", true);
  try {
    const data = await api("/api/auth/password", {
      method: "POST",
      body: JSON.stringify({ current_password: current.value, new_password: next.value }),
    });
    setSession(data.access_token);
    current.value = next.value = "";
    showStatus(status, "Password changed. Your other devices are logged out.");
    status.classList.add("ok");
  } catch (err) {
    showStatus(status, err.message, true);
  }
});

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

function makeEntry(className, type, detail, extra) {
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
  if (extra) {
    const extraEl = document.createElement("span");
    extraEl.className = "entry-extra";
    extraEl.textContent = extra;
    detailEl.appendChild(extraEl);
  }
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

// Whole Monday–Sunday weeks covering roughly 2 weeks back and 2 weeks ahead
// of today, shifted by the Earlier/Later buttons (2 weeks per click).
let weekOffset = 0;

function calendarRange() {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const center = addDays(today, weekOffset * 7);
  const from = addDays(center, -14);
  const to = addDays(center, 14);
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
  badge.setAttribute("aria-label", badge.title);
  return badge;
}

function makeRestingHrBadge(sleep) {
  if (!sleep || sleep.resting_heart_rate == null) return null;
  const bpm = Math.round(sleep.resting_heart_rate);
  const badge = document.createElement("span");
  badge.className = "rhr-badge";
  badge.textContent = `❤️ ${bpm}`;
  badge.title = `Resting heart rate ${bpm} bpm`;
  badge.setAttribute("aria-label", badge.title);
  return badge;
}

function renderWeekSummary(week) {
  let plannedMin = 0;
  let doneMin = 0;
  const sleepScores = [];
  const restingHrs = [];
  let load = 0;
  let loadCount = 0;
  for (const day of week) {
    for (const p of day.planned) plannedMin += p.planned_duration_minutes || 0;
    for (const a of day.activities) {
      doneMin += (a.duration_seconds || 0) / 60;
      if (a.training_load != null) {
        load += a.training_load;
        loadCount += 1;
      }
    }
    if (day.sleep && day.sleep.sleep_score != null) sleepScores.push(day.sleep.sleep_score);
    if (day.sleep && day.sleep.resting_heart_rate != null) restingHrs.push(day.sleep.resting_heart_rate);
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

  const loadRow = document.createElement("div");
  loadRow.className = "week-row load";
  const loadName = document.createElement("span");
  loadName.textContent = "Load";
  const loadValue = document.createElement("strong");
  loadValue.textContent = loadCount ? String(Math.round(load)) : "—";
  loadRow.title = loadCount
    ? `Total Garmin training load from ${loadCount} activit${loadCount > 1 ? "ies" : "y"}`
    : "No training load this week";
  loadRow.append(loadName, loadValue);
  cell.appendChild(loadRow);

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

  const rhrRow = document.createElement("div");
  rhrRow.className = "week-row rhr";
  const rhrName = document.createElement("span");
  rhrName.textContent = "Resting HR";
  const rhrValue = document.createElement("strong");
  if (restingHrs.length) {
    const avg = Math.round(restingHrs.reduce((a, b) => a + b, 0) / restingHrs.length);
    rhrValue.textContent = `❤️ ${avg}`;
    rhrRow.title = `Average resting heart rate over ${restingHrs.length} day${restingHrs.length > 1 ? "s" : ""}: ${avg} bpm`;
  } else {
    rhrValue.textContent = "—";
    rhrRow.title = "No resting heart rate this week";
  }
  rhrRow.append(rhrName, rhrValue);
  cell.appendChild(rhrRow);

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

// Planned vs. done, same day and same sport: within 15% of the planned time is
// "done as planned"; any other duration is still done, just not as planned.
const MATCH_TOLERANCE = 0.15;

function sportCategory(type) {
  const key = (type || "").toLowerCase();
  if (key.includes("swim")) return "swim";
  if (key.includes("run")) return "run";
  if (["cycl", "bik", "ride", "zwift"].some((n) => key.includes(n))) return "bike";
  if (key.includes("strength")) return "strength";
  return null;
}

// Pairs each planned session with the closest same-sport activity of the day:
// first those within tolerance, then any duration. One activity covers one
// planned session (split sessions are not added up). Returns the pairs, with
// `asPlanned`, plus whatever is left unmatched.
function matchDay(day) {
  const activities = day.activities.filter((a) => a.duration_seconds);
  const unmatchable = day.activities.filter((a) => !a.duration_seconds);
  const pairs = [];
  const left = new Set(day.planned);
  for (const strict of [true, false]) {
    for (const p of day.planned) {
      if (!left.has(p)) continue;
      const category = sportCategory(p.activity_type);
      const plannedMin = p.planned_duration_minutes || 0;
      if (!category || plannedMin <= 0) continue;
      let best = null;
      for (const a of activities) {
        if (sportCategory(a.activity_type) !== category) continue;
        const diff = Math.abs(a.duration_seconds / 60 - plannedMin) / plannedMin;
        if ((!strict || diff <= MATCH_TOLERANCE) && (!best || diff < best.diff)) best = { a, diff };
      }
      if (best) {
        pairs.push({ planned: p, activity: best.a, asPlanned: best.diff <= MATCH_TOLERANCE });
        activities.splice(activities.indexOf(best.a), 1);
        left.delete(p);
      }
    }
  }
  return { pairs, activities: [...activities, ...unmatchable], planned: day.planned.filter((p) => left.has(p)) };
}

// What was answered on the watch after the session: ", effort 7/10, felt strong".
const FEEL_WORDS = { 0: "very weak", 25: "weak", 50: "normal", 75: "strong", 100: "very strong" };

function effortText(a) {
  const effort = a.perceived_effort ? `, effort ${a.perceived_effort}/10` : "";
  const felt = a.feel != null ? `, felt ${FEEL_WORDS[a.feel] || `${a.feel}/100`}` : "";
  return effort + felt;
}

function makeActivityEntry(className, a) {
  const detail = a.duration_seconds ? fmtHours(a.duration_seconds / 60) : "";
  const load = a.training_load != null ? Math.round(a.training_load) : null;
  const entry = makeEntry(className, a.activity_type, detail, load != null ? ` · load ${load}` : "");
  const te = a.aerobic_training_effect != null ? `, aerobic TE ${a.aerobic_training_effect.toFixed(1)}` : "";
  entry.title = `Done (Garmin): ${a.name || sportLabel(a.activity_type)}, ${detail}${load != null ? `, training load ${load}` : ""}${te}${effortText(a)}${legsText(a, true)}`;
  entry.setAttribute("role", "note");
  entry.setAttribute("aria-label", entry.title);
  if (a.legs && a.legs.length) addNotes(entry, legsText(a));
  return entry;
}

// A multisport race's legs: "🏊 0:42 · 🚴 2:40 · 🏃 1:45" (transitions in the long form only).
function legsText(a, long = false) {
  if (!a.legs || !a.legs.length) return "";
  const parts = [];
  let transition = 0;
  for (const leg of a.legs) {
    const time = fmtHM((leg.duration_seconds || 0) / 60);
    if (leg.sport === "other" && (leg.type || "").includes("transition")) {
      transition += 1;
      if (long) parts.push(`T${transition} ${time}`);
      continue;
    }
    const km = leg.distance_meters >= 100 ? ` ${(leg.distance_meters / 1000).toFixed(1)} km` : "";
    const name = { swim: "Swim", bike: "Bike", run: "Run" }[leg.sport] || sportLabel(leg.type);
    parts.push(long ? `${name} ${time}${km}` : `${activityIcon(leg.type)} ${time}`);
  }
  return long ? `. Legs: ${parts.join(", ")}` : parts.join(" · ");
}

function addNotes(entry, notes) {
  if (!notes) return;
  const el = document.createElement("span");
  el.className = "entry-notes";
  el.textContent = notes;
  entry.appendChild(el);
}

function makeEditable(entry, date, planned) {
  entry.tabIndex = 0;
  entry.setAttribute("role", "button");
  entry.setAttribute("aria-label", entry.title);
  entry.addEventListener("click", () => openPlanDialog(date, planned));
  entry.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      openPlanDialog(date, planned);
    }
  });
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
      cell.dataset.date = day.date;

      const head = document.createElement("div");
      head.className = "day-head";
      const dateEl = document.createElement("span");
      dateEl.className = "day-date";
      dateEl.textContent = fmtDate(day.date);
      dateEl.title = day.date;
      head.appendChild(dateEl);
      const stats = document.createElement("span");
      stats.className = "day-stats";
      for (const badge of [makeSleepBadge(day.sleep), makeRestingHrBadge(day.sleep)]) {
        if (badge) stats.appendChild(badge);
      }
      if (stats.childElementCount) head.appendChild(stats);
      cell.appendChild(head);

      const { pairs, activities, planned } = matchDay(day);

      for (const { planned: p, activity: a, asPlanned } of pairs) {
        const entry = makeActivityEntry(asPlanned ? "completed" : "completed-off", a);
        const load = a.training_load != null ? ` · load ${Math.round(a.training_load)}` : "";
        entry.title =
          `✓ ${asPlanned ? "Done as planned" : "Done, other duration"}: ${sportLabel(p.activity_type)}, planned ${fmtHours(p.planned_duration_minutes)}, ` +
          `did ${fmtHours(a.duration_seconds / 60)}${load}${effortText(a)}${p.notes ? `. Plan: ${p.notes}` : ""}. Click to edit the plan.`;
        addNotes(entry, p.notes);
        makeEditable(entry, day.date, p);
        cell.appendChild(entry);
      }

      for (const a of activities) cell.appendChild(makeActivityEntry("done", a));

      for (const p of planned) {
        const detail = p.planned_duration_minutes ? fmtHours(p.planned_duration_minutes) : "";
        const entry = makeEntry("planned", p.activity_type, detail);
        entry.title =
          `Planned ${sportLabel(p.activity_type)}, ${detail}` +
          `${p.notes ? `: ${p.notes}` : ""}. Click to edit.`;
        addNotes(entry, p.notes);
        makeEditable(entry, day.date, p);
        cell.appendChild(entry);
      }

      const add = document.createElement("button");
      add.className = "add-btn";
      add.type = "button";
      add.title = "Plan a session";
      add.setAttribute("aria-label", `Plan a session on ${fmtLongDate(day.date)} (${day.date})`);
      add.addEventListener("click", () => openPlanDialog(day.date));
      cell.appendChild(add);

      grid.appendChild(cell);
    }
    grid.appendChild(renderWeekSummary(week));
  }
}

async function loadCalendar() {
  const { start, end } = calendarRange();
  let data;
  try {
    data = await api(`/api/calendar?start=${start}&end=${end}`);
  } catch (err) {
    if (err.status !== 401) showStatus(syncStatus, `Couldn't load the calendar: ${err.message}`, true);
    return;
  }
  renderCalendar(data);
  document.getElementById("cal-range").textContent = `Showing ${fmtLongDate(start)} to ${fmtLongDate(end)}`;
  document.getElementById("cal-today").disabled = weekOffset === 0;
}

function fmtLongDate(iso) {
  return new Date(iso + "T00:00:00").toLocaleDateString(undefined, {
    weekday: "short",
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

for (const [id, change] of [["cal-prev", -2], ["cal-next", 2], ["cal-today", null]]) {
  document.getElementById(id).addEventListener("click", () => {
    weekOffset = change === null ? 0 : weekOffset + change;
    loadCalendar();
  });
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
const planNotes = document.getElementById("plan-notes");
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
  planNotes.value = existing ? existing.notes || "" : "";
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

  const body = { activity_type: sport.value, planned_duration_minutes: minutes, notes: planNotes.value.trim() || null };
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
const logSyncBtn = document.getElementById("log-sync");
const syncStatus = document.getElementById("sync-status");

function showStatus(el, text, isError = false) {
  el.classList.remove("ok");
  el.textContent = text;
  el.classList.remove("hidden");
  el.classList.toggle("error", isError);
  el.classList.toggle("status", !isError);
}

// The sync runs on the server (a first one has weeks of sleep, recovery data
// and session details to fetch, in batches): start it, then ask how it goes.
const SYNC_POLL_MS = 1500;
const SYNC_GIVE_UP_MS = 30 * 60 * 1000; // stop asking; the server keeps going
let syncing = false;

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function syncProgressText(job) {
  if (job.state === "waiting") return "Waiting for the background sync of your account to finish…";
  if (job.rounds === 0) return "Syncing with Garmin…";
  return `Fetching older days and session details (step ${job.rounds + 1})…`;
}

async function refreshAfterSync() {
  await loadCalendar();
  if (currentTab === "history") await loadHistoryPage();
  if (currentTab === "athlete") await loadAthletePage();
}

async function runSync(statusEl = syncStatus) {
  if (!(statusEl instanceof Element)) statusEl = syncStatus; // called as a click handler
  if (syncing) return;
  syncing = true;
  for (const btn of [syncBtn, logSyncBtn]) btn.disabled = true;
  try {
    let job = await api("/api/garmin/sync/start", { method: "POST" });
    let roundsShown = job.rounds;
    const giveUpAt = Date.now() + SYNC_GIVE_UP_MS;
    while ((job.state === "waiting" || job.state === "running") && Date.now() < giveUpAt) {
      showStatus(statusEl, syncProgressText(job));
      await wait(SYNC_POLL_MS);
      job = await api("/api/garmin/sync/status");
      // The calendar fills in batch by batch; the History tab is rebuilt once, at the end.
      if (job.rounds > roundsShown && job.state === "running") {
        roundsShown = job.rounds;
        await loadCalendar();
      }
    }
    if (job.state === "failed") throw new Error(job.error || "The sync failed.");
    await refreshAfterSync();
    if (job.state !== "done") {
      showStatus(statusEl, "The sync is still running on the server. Refresh the page in a few minutes.");
      return;
    }
    showStatus(
      statusEl,
      `Synced ${job.activities_synced} ${job.activities_synced === 1 ? "activity" : "activities"}, ` +
        `${plural(job.days_synced, "day")} of sleep and recovery` +
        (job.details_synced ? `, effort, feel and laps for ${plural(job.details_synced, "session")}` : "") +
        (job.more_to_sync ? ". Older data is still waiting: sync again to continue." : ".")
    );
    if (!job.more_to_sync) statusEl.classList.add("ok");
  } catch (err) {
    if (err.status === 409) {
      statusEl.classList.add("hidden");
      showTab("calendar", { load: false });
      showGarminConnect(err.message);
    } else if (err.status !== 401) {
      showStatus(statusEl, err.message, true);
    }
  } finally {
    syncing = false;
    logSyncBtn.disabled = false;
    syncBtn.disabled = !(me && me.garmin_connected);
  }
}

syncBtn.addEventListener("click", () => runSync(syncStatus));

// --- Objectives (added and edited here) ---

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
    countdown.className = "objective-countdown";
    countdown.textContent = daysUntil(obj.event_date);
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
  if (obj.notes) {
    const notes = document.createElement("p");
    notes.className = "objective-notes";
    notes.textContent = obj.notes;
    card.appendChild(objectiveField("Notes", notes));
  }

  const actions = document.createElement("div");
  actions.className = "objective-actions";
  const edit = document.createElement("button");
  edit.type = "button";
  edit.className = "btn-link";
  edit.textContent = "Edit";
  edit.setAttribute("aria-label", `Edit objective ${obj.title}`);
  edit.addEventListener("click", () => renderObjectiveEdit(card, obj));
  const del = document.createElement("button");
  del.type = "button";
  del.className = "btn-link danger";
  del.textContent = "Delete";
  // Two clicks instead of confirm(): a native dialog would block tools driving the page.
  del.addEventListener("click", async () => {
    if (!del.classList.contains("confirming")) {
      del.classList.add("confirming");
      del.textContent = "Click again to delete";
      del.setAttribute("aria-label", `Click again to confirm deleting objective ${obj.title}`);
      setTimeout(() => {
        del.classList.remove("confirming");
        del.textContent = "Delete";
        del.setAttribute("aria-label", `Delete objective ${obj.title}`);
      }, 4000);
      return;
    }
    try {
      await api(`/api/objectives/${obj.id}`, { method: "DELETE" });
    } catch (err) {
      if (err.status !== 401) {
        del.textContent = `Couldn't delete: ${err.message}`;
        del.classList.remove("confirming");
      }
      return;
    }
    await loadObjectives();
  });
  actions.append(edit, del);
  card.appendChild(actions);
}

function renderObjectiveEdit(card, obj) {
  const isNew = obj.id == null;
  card.innerHTML = "";
  const form = document.createElement("form");
  form.className = "objective-form";
  form.noValidate = true;

  const fields = [
    ["Title", "text", "title", obj.title, "e.g. Montreal half marathon"],
    ["Event date", "date", "event_date", obj.event_date || "", ""],
    ["Target time", "text", "target_time", obj.target_time || "", "e.g. 1:45:00"],
    ["Notes (priority, what matters most)", "text", "notes", obj.notes || "", "e.g. Main race of the year. The bike is my weak leg."],
  ];
  const inputs = {};
  for (const [label, type, key, value, placeholder] of fields) {
    const id = `obj-${isNew ? "new" : obj.id}-${key}`;
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
  cancel.addEventListener("click", () => (isNew ? card.remove() : renderObjectiveView(card, obj)));
  const save = document.createElement("button");
  save.type = "submit";
  save.textContent = isNew ? "Save objective" : "Save";
  actions.append(cancel, save);
  form.append(error, actions);

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!inputs.title.value.trim()) return showStatus(error, "Give the objective a title.", true);
    const body = JSON.stringify({
      title: inputs.title.value.trim(),
      event_date: inputs.event_date.value || null,
      target_time: inputs.target_time.value.trim(),
      notes: inputs.notes.value.trim(),
    });
    try {
      if (isNew) await api("/api/objectives", { method: "POST", body });
      else await api(`/api/objectives/${obj.id}`, { method: "PATCH", body });
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
    empty.className = "subtle objectives-empty";
    empty.textContent = "No objective yet. Add your next event with + Add objective.";
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
  let data;
  try {
    data = await api("/api/objectives");
  } catch (err) {
    if (err.status === 401) return;
    objectivesList.innerHTML = "";
    const failed = document.createElement("p");
    failed.className = "error";
    failed.textContent = `Couldn't load the objectives: ${err.message}`;
    objectivesList.appendChild(failed);
    return;
  }
  renderObjectives(data.objectives);
}

document.getElementById("objective-add").addEventListener("click", () => {
  const existing = objectivesList.querySelector("#obj-new-title");
  if (existing) return existing.focus();
  const empty = objectivesList.querySelector(".objectives-empty");
  if (empty) empty.remove();
  const card = document.createElement("div");
  card.className = "objective-card";
  objectivesList.prepend(card);
  renderObjectiveEdit(card, { id: null, title: "", event_date: null, target_time: null, notes: null });
});

// --- Tabs ---

const TABS = ["calendar", "athlete", "plan", "import", "history"];
let currentTab = "calendar";

function showTab(name, { load = true } = {}) {
  currentTab = name;
  for (const tab of TABS) {
    document.getElementById(`tab-${tab}`).classList.toggle("hidden", tab !== name);
    const btn = document.getElementById(`tab-btn-${tab}`);
    btn.classList.toggle("active", tab === name);
    if (tab === name) btn.setAttribute("aria-current", "page");
    else btn.removeAttribute("aria-current");
  }
  try {
    history.replaceState(null, "", name === "calendar" ? location.pathname : `#${name}`);
  } catch (_) {
    /* not essential */
  }
  if (!load) return;
  if (name === "athlete") loadAthletePage();
  else if (name === "plan") loadPlanPage();
  else if (name === "import") loadImportPage();
  else if (name === "history") loadHistoryPage();
  else loadCalendar();
}

for (const tab of TABS) document.getElementById(`tab-btn-${tab}`).addEventListener("click", () => showTab(tab));

// --- Plan page: preview and confirm, then the draft, then the training log Claude reads ---

const logEl = document.getElementById("training-log");
const logWeeks = document.getElementById("log-weeks");
const logDetail = document.getElementById("log-detail");
const rulesText = document.getElementById("rules-text");
const rulesStatus = document.getElementById("rules-status");
const logStatus = document.getElementById("log-status");
const planStart = document.getElementById("plan-start");
const planWeeks = document.getElementById("plan-weeks");
const draftText = document.getElementById("draft-text");
const draftStatus = document.getElementById("draft-status");
const draftErrors = document.getElementById("draft-errors");
const draftPreview = document.getElementById("draft-preview");
const confirmBtn = document.getElementById("draft-confirm");
const confirmView = document.getElementById("confirm-view");
const confirmStatus = document.getElementById("confirm-status");

let planDraft = null; // the server's last answer: parsed sessions, errors, weekly totals
let draftDirty = false; // typed since the last save

function fmtHM(minutes) {
  const total = Math.round(minutes || 0);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

function fmtSpan(startIso, endIso) {
  const start = new Date(startIso + "T00:00:00");
  const end = new Date(endIso + "T00:00:00");
  const opts = { weekday: "short", month: "short", day: "numeric" };
  return `${start.toLocaleDateString(undefined, opts)} to ${end.toLocaleDateString(undefined, { ...opts, year: "numeric" })}`;
}

function thisMonday() {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return addDays(today, -((today.getDay() + 6) % 7));
}

// Every Monday from this week to about six months out, so any future week can be planned.
function fillPlanStart(selectedIso) {
  const monday = thisMonday();
  const options = [];
  for (let i = 0; i < 27; i++) {
    const day = addDays(monday, i * 7);
    let label = day.toLocaleDateString(undefined, {
      weekday: "short",
      month: "short",
      day: "numeric",
      ...(day.getFullYear() !== monday.getFullYear() ? { year: "numeric" } : {}),
    });
    if (i === 0) label = `This week (${label})`;
    if (i === 1) label = `Next week (${label})`;
    options.push([isoDate(day), label]);
  }
  if (selectedIso && !options.some(([iso]) => iso === selectedIso)) options.unshift([selectedIso, fmtLongDate(selectedIso)]);
  planStart.innerHTML = "";
  for (const [iso, label] of options) {
    const opt = document.createElement("option");
    opt.value = iso;
    opt.textContent = label;
    planStart.appendChild(opt);
  }
  if (selectedIso) planStart.value = selectedIso;
}

async function loadLog() {
  try {
    const data = await api(
      `/api/plan/log?weeks=${logWeeks.value}&detail=${logDetail.value}&today=${isoDate(new Date())}`
    );
    logEl.textContent = data.text;
    logStatus.classList.add("hidden");
  } catch (err) {
    if (err.status !== 401) showStatus(logStatus, `Couldn't load the log: ${err.message}`, true);
  }
}

function renderDraftErrors(errors) {
  draftErrors.innerHTML = "";
  draftErrors.classList.toggle("hidden", errors.length === 0);
  if (!errors.length) return;
  const title = document.createElement("strong");
  title.textContent = `${plural(errors.length, "line")} to fix before this can go on the calendar:`;
  const list = document.createElement("ul");
  for (const e of errors) {
    const item = document.createElement("li");
    const where = document.createElement("span");
    where.className = "error-line";
    where.textContent = `Line ${e.line}: ${e.text}`;
    const why = document.createElement("span");
    why.textContent = e.message;
    item.append(where, why);
    list.appendChild(item);
  }
  draftErrors.append(title, list);
}

// The weekly tables of a plan: the Plan tab's draft, or what the Import tab would bring in.
function renderDraftPreview(draft, container = draftPreview, emptyText = null) {
  container.innerHTML = "";
  if (!draft.sessions_count) {
    const empty = document.createElement("p");
    empty.className = "plan-empty";
    empty.textContent =
      emptyText ||
      (draft.errors.length
        ? "None of the draft's lines can be read yet."
        : "No plan for these weeks yet. Ask Claude in Chrome to write one, or type the sessions in the draft below.");
    container.appendChild(empty);
    return;
  }
  const today = isoDate(new Date());

  draft.weeks_preview.forEach((week, index) => {
    const heading = document.createElement("h3");
    heading.className = "plan-week-title";
    heading.textContent = `Week ${index + 1} · ${fmtSpan(week.start, week.end)} · ${fmtHM(week.total_minutes)}`;
    const table = document.createElement("table");
    table.className = "users-table plan-table";
    const head = table.createTHead().insertRow();
    for (const name of ["Day", "Sessions", "Time"]) {
      const th = document.createElement("th");
      th.scope = "col";
      th.textContent = name;
      head.appendChild(th);
    }
    const body = table.createTBody();
    for (let i = 0; i < 7; i++) {
      const iso = isoDate(addDays(new Date(week.start + "T00:00:00"), i));
      const sessions = week.sessions.filter((s) => s.date === iso);
      const row = body.insertRow();
      const dayCell = row.insertCell();
      dayCell.textContent = fmtDate(iso);
      dayCell.className = "plan-day";
      const sessionsCell = row.insertCell();
      if (!sessions.length) {
        sessionsCell.textContent = iso < today ? "(already past)" : "–";
        sessionsCell.className = "subtle";
      }
      for (const s of sessions) {
        const line = document.createElement("div");
        line.className = "plan-session";
        const name = document.createElement("strong");
        name.textContent = `${activityIcon(s.activity_type)} ${sportLabel(s.activity_type)} ${fmtHM(s.minutes)}`;
        line.appendChild(name);
        if (s.details) line.appendChild(document.createTextNode(` · ${s.details}`));
        sessionsCell.appendChild(line);
      }
      const timeCell = row.insertCell();
      timeCell.className = "plan-time";
      timeCell.textContent = sessions.length ? fmtHM(sessions.reduce((sum, s) => sum + s.minutes, 0)) : "";
    }
    container.append(heading, table);
  });

  // Weekly split, like a block summary. Strength/Other only appear when used.
  const sports = [["swim", "Swim"], ["bike", "Bike"], ["run", "Run"], ["strength", "Strength"], ["other", "Other"]].filter(
    ([key], i) => i < 3 || draft.weeks_preview.some((w) => w.minutes_by_sport[key] > 0)
  );
  const heading = document.createElement("h3");
  heading.className = "plan-week-title";
  heading.textContent = "Weekly split";
  const table = document.createElement("table");
  table.className = "users-table plan-table split-table";
  const head = table.createTHead().insertRow();
  for (const name of ["Week", ...sports.map(([, label]) => label), "Total"]) {
    const th = document.createElement("th");
    th.scope = "col";
    th.textContent = name;
    head.appendChild(th);
  }
  const body = table.createTBody();
  draft.weeks_preview.forEach((week, index) => {
    const row = body.insertRow();
    row.insertCell().textContent = `${index + 1} · ${fmtDate(week.start)}`;
    for (const [key] of sports) row.insertCell().textContent = fmtHM(week.minutes_by_sport[key]);
    const total = row.insertCell();
    total.textContent = fmtHM(week.total_minutes);
    total.className = "plan-time";
  });
  container.append(heading, table);
}

function resetConfirmButton() {
  confirmBtn.classList.remove("confirming");
  confirmBtn.textContent = "Put this plan on the calendar";
}

function renderConfirm(draft) {
  const summary = document.getElementById("confirm-summary");
  resetConfirmButton();
  const ready = draft.sessions_count > 0 && draft.errors.length === 0;
  confirmBtn.disabled = !ready;
  if (draft.errors.length) {
    summary.textContent = `Fix the ${plural(draft.errors.length, "line")} flagged in the draft below first.`;
  } else if (!draft.sessions_count) {
    summary.textContent = "Nothing to put on the calendar yet.";
  } else {
    const replaced = draft.existing_planned
      ? `${plural(draft.existing_planned, "session")} already planned from ${fmtDate(draft.replace_from)} to ${fmtDate(draft.end_date)} will be replaced.`
      : "Nothing is planned on those dates yet.";
    summary.textContent =
      `${plural(draft.sessions_count, "session")}, ${fmtSpan(draft.start_date, draft.end_date)}. ${replaced} ` +
      "Activities you already did are never touched.";
  }
}

function applyDraft(draft, { fill = false } = {}) {
  planDraft = draft;
  if (fill) {
    fillPlanStart(draft.start_date);
    if (![...planWeeks.options].some((o) => o.value === String(draft.weeks))) {
      const opt = document.createElement("option");
      opt.value = String(draft.weeks);
      opt.textContent = `${draft.weeks} weeks`;
      planWeeks.appendChild(opt);
    }
    planWeeks.value = String(draft.weeks);
    draftText.value = draft.text;
    draftDirty = false;
  }
  document.getElementById("plan-range").textContent = fmtSpan(draft.start_date, draft.end_date);
  renderDraftErrors(draft.errors);
  renderDraftPreview(draft);
  renderConfirm(draft);
}

async function saveDraft({ quiet = false } = {}) {
  try {
    const draft = await api(`/api/plan/draft?today=${isoDate(new Date())}`, {
      method: "PUT",
      body: JSON.stringify({
        start_date: planStart.value,
        weeks: Number(planWeeks.value),
        text: draftText.value,
      }),
    });
    draftDirty = false;
    applyDraft(draft);
    confirmView.classList.add("hidden");
    if (!quiet) {
      const problems = draft.errors.length ? `, ${plural(draft.errors.length, "line")} to fix` : "";
      showStatus(draftStatus, `Saved: ${plural(draft.sessions_count, "session")}${problems}. Not on the calendar yet.`);
      if (!draft.errors.length) draftStatus.classList.add("ok");
    }
    return draft;
  } catch (err) {
    if (err.status !== 401) showStatus(draftStatus, err.message, true);
    return null;
  }
}

async function loadPlanPage() {
  for (const el of [draftStatus, confirmStatus, logStatus, rulesStatus, importStatus]) el.classList.add("hidden");
  confirmView.classList.add("hidden");
  try {
    const [draft, rules] = await Promise.all([api(`/api/plan/draft?today=${isoDate(new Date())}`), api("/api/plan/rules")]);
    rulesText.value = rules.rules;
    applyDraft(draft, { fill: true });
  } catch (err) {
    if (err.status !== 401) showStatus(confirmStatus, err.message, true);
  }
}

logWeeks.addEventListener("change", loadLog);
logDetail.addEventListener("change", loadLog);
logSyncBtn.addEventListener("click", () => runSync(logStatus));

// Older activities come as one list from Garmin: enough for the long-term weekly totals.
const importBtn = document.getElementById("import-btn");
const importStatus = document.getElementById("import-status");
importBtn.addEventListener("click", async () => {
  importBtn.disabled = true;
  showStatus(importStatus, "Asking Garmin for your older activities…");
  try {
    const months = Number(document.getElementById("import-months").value);
    const result = await api("/api/garmin/import-history", { method: "POST", body: JSON.stringify({ months }) });
    showStatus(
      importStatus,
      `Imported ${result.activities_imported} older ${result.activities_imported === 1 ? "activity" : "activities"} back to ` +
        `${fmtLongDate(result.since)}. They are in the log under "Earlier weeks".`
    );
    importStatus.classList.add("ok");
    await loadHistoryPage();
  } catch (err) {
    if (err.status !== 401) showStatus(importStatus, err.message, true);
  } finally {
    importBtn.disabled = false;
  }
});

// The rule set is saved on its own and shows up in the history.
async function saveRules() {
  try {
    rulesText.value = (await api("/api/plan/rules", { method: "PUT", body: JSON.stringify({ rules: rulesText.value }) })).rules;
    showStatus(rulesStatus, "Saved. It is now part of the History tab.");
    rulesStatus.classList.add("ok");
  } catch (err) {
    if (err.status !== 401) showStatus(rulesStatus, err.message, true);
  }
}
document.getElementById("rules-save").addEventListener("click", saveRules);
rulesText.addEventListener("change", saveRules);
draftText.addEventListener("input", () => {
  draftDirty = true;
});
// Saving when the box loses focus keeps the preview current without an extra click.
draftText.addEventListener("change", () => saveDraft());
for (const el of [planStart, planWeeks]) el.addEventListener("change", () => saveDraft());
document.getElementById("draft-save").addEventListener("click", async () => {
  const draft = await saveDraft();
  // The preview is above the draft: bring it into view, unless there are lines to fix right here.
  if (draft && !draft.errors.length) document.getElementById("plan-panel").scrollIntoView({ behavior: "smooth", block: "start" });
});

// --- History tab: everything Claude in Chrome needs, on one page ---

const logContext = document.getElementById("log-context");

// The weeks being planned, the line format and the current draft.
function planContextText(draft) {
  const parts = [
    "HOW TO WRITE THE PLAN",
    `Weeks to plan: ${fmtSpan(draft.start_date, draft.end_date)} (change them on the Plan tab).`,
    "Give one session per line as: date | sport | duration | details",
    'Date like "Tue Oct 13", sport swim / bike / run / strength, duration like 1:15. Two sessions on one day are two lines; rest days need no line.',
    'In the app: write the lines in the Plan tab\'s "Draft sessions" box and press "Save and preview". The athlete puts it on the calendar.',
    "",
    `CURRENT DRAFT (${draft.sessions_count} session${draft.sessions_count === 1 ? "" : "s"}${draft.errors.length ? `, ${draft.errors.length} line(s) with a problem` : ""})`,
    (draft.text || "").trim() || "- empty",
  ];
  return parts.join("\n");
}

async function loadHistoryPage() {
  for (const el of [logStatus, importStatus]) el.classList.add("hidden");
  try {
    const [draft] = await Promise.all([api(`/api/plan/draft?today=${isoDate(new Date())}`), loadLog()]);
    logContext.textContent = planContextText(draft);
  } catch (err) {
    if (err.status !== 401) showStatus(logStatus, err.message, true);
  }
}

// The same text, ready to paste into a normal Claude conversation.
document.getElementById("log-copy").addEventListener("click", async () => {
  const text = `${logEl.textContent}\n\n${logContext.textContent}`;
  try {
    await navigator.clipboard.writeText(text);
    showStatus(logStatus, "Copied. Paste it into a Claude conversation and ask for the plan.");
    logStatus.classList.add("ok");
  } catch (_) {
    // No clipboard permission: select the log so it can be copied by hand.
    const range = document.createRange();
    range.selectNodeContents(logEl);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    showStatus(logStatus, "Couldn't copy automatically. The log is selected: press Cmd+C.", true);
  }
});

// Two clicks when sessions will be replaced, instead of a confirm() dialog.
confirmBtn.addEventListener("click", async () => {
  if (draftDirty && !(await saveDraft({ quiet: true }))) return;
  if (!planDraft || planDraft.errors.length || !planDraft.sessions_count) return;
  if (planDraft.existing_planned > 0 && !confirmBtn.classList.contains("confirming")) {
    confirmBtn.classList.add("confirming");
    confirmBtn.textContent = `Click again: replaces ${plural(planDraft.existing_planned, "planned session")}`;
    setTimeout(resetConfirmButton, 5000);
    return;
  }
  confirmBtn.disabled = true;
  try {
    const result = await api(`/api/plan/draft/confirm?today=${isoDate(new Date())}`, { method: "POST" });
    const replaced = result.replaced ? `, replacing ${plural(result.replaced, "planned session")}` : "";
    applyDraft(await api(`/api/plan/draft?today=${isoDate(new Date())}`));
    showStatus(confirmStatus, `Done: ${plural(result.added, "session")} are on the calendar${replaced}.`);
    confirmStatus.classList.add("ok");
    confirmView.classList.remove("hidden");
  } catch (err) {
    if (err.status !== 401) showStatus(confirmStatus, err.message, true);
    confirmBtn.disabled = false;
  }
});

confirmView.addEventListener("click", () => {
  // The calendar shows 2 weeks either side of its centre: centre it so the plan starts near the top.
  const weeksAhead = Math.round((new Date(planDraft.start_date + "T00:00:00") - thisMonday()) / (7 * 86400000));
  weekOffset = Math.max(0, weeksAhead + 2);
  showTab("calendar");
});

// --- Init ---

async function loadAll() {
  await Promise.all([loadCalendar(), loadObjectives()]);
}

// --- Athlete tab: objectives, the athlete's own numbers, and what Garmin knows ---

const profileForm = document.getElementById("profile-form");
const profileStatus = document.getElementById("profile-status");
const PROFILE_NUMBERS = ["weight_kg", "height_cm", "max_hr", "threshold_hr", "resting_hr", "ftp_watts"];
const PROFILE_TEXTS = ["ftp_date", "bike_zones", "threshold_pace", "run_zones", "css_pace", "swim_zones", "equipment", "health", "other"];

function fillProfile(data) {
  for (const key of [...PROFILE_NUMBERS, ...PROFILE_TEXTS]) {
    const value = data.profile[key];
    document.getElementById(`pf-${key}`).value = value == null ? "" : String(value);
  }
  document.getElementById("athlete-garmin").textContent = data.garmin.join("\n");
}

async function loadAthletePage() {
  profileStatus.classList.add("hidden");
  try {
    fillProfile(await api(`/api/athlete?today=${isoDate(new Date())}`));
  } catch (err) {
    if (err.status !== 401) showStatus(profileStatus, err.message, true);
  }
}

profileForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = {};
  for (const key of PROFILE_NUMBERS) {
    const value = document.getElementById(`pf-${key}`).value.trim();
    if (value) body[key] = Number(value);
  }
  for (const key of PROFILE_TEXTS) {
    const value = document.getElementById(`pf-${key}`).value.trim();
    if (value) body[key] = value;
  }
  for (const key of ["threshold_pace", "css_pace"]) {
    if (body[key] && !/^\d{1,2}:\d{2}$/.test(body[key])) {
      showStatus(profileStatus, `Write the ${key === "css_pace" ? "CSS" : "threshold pace"} as min:sec, like 4:30.`, true);
      return document.getElementById(`pf-${key}`).focus();
    }
  }
  try {
    fillProfile(await api(`/api/athlete?today=${isoDate(new Date())}`, { method: "PUT", body: JSON.stringify(body) }));
    showStatus(profileStatus, "Saved. It is now part of the History tab.");
    profileStatus.classList.add("ok");
  } catch (err) {
    if (err.status !== 401) showStatus(profileStatus, err.status === 422 ? "One of the numbers is out of range: check them and save again." : err.message, true);
  }
});

// --- Import tab: your share code, and copying someone's planned sessions ---

const shareCode = document.getElementById("share-code");
const shareStatus = document.getElementById("share-status");
const shareNew = document.getElementById("share-new");
const friendForm = document.getElementById("friend-form");
const friendSource = document.getElementById("friend-source");
const friendWeeks = document.getElementById("friend-weeks");
const friendTarget = document.getElementById("friend-target");
const friendFormStatus = document.getElementById("friend-form-status");
const friendResult = document.getElementById("friend-result");
const friendSummary = document.getElementById("friend-summary");
const friendStatus = document.getElementById("friend-status");
const friendToPlan = document.getElementById("friend-to-plan");
const friendToCalendar = document.getElementById("friend-to-calendar");
const friendView = document.getElementById("friend-view");

let friendPreview = null; // the server's last preview, for the buttons
let targetTouched = false; // "onto my weeks" follows "their weeks" until changed by hand

// Mondays from `firstWeek` to `lastWeek` weeks away from this week.
function fillMondays(select, firstWeek, lastWeek) {
  const monday = thisMonday();
  select.innerHTML = "";
  for (let i = firstWeek; i <= lastWeek; i++) {
    const day = addDays(monday, i * 7);
    let label = day.toLocaleDateString(undefined, {
      weekday: "short",
      month: "short",
      day: "numeric",
      ...(day.getFullYear() !== monday.getFullYear() ? { year: "numeric" } : {}),
    });
    if (i === -1) label = `Last week (${label})`;
    if (i === 0) label = `This week (${label})`;
    if (i === 1) label = `Next week (${label})`;
    const opt = document.createElement("option");
    opt.value = isoDate(day);
    opt.textContent = label;
    select.appendChild(opt);
  }
}

function followSource() {
  if (targetTouched) return;
  const same = [...friendTarget.options].some((o) => o.value === friendSource.value);
  friendTarget.value = same ? friendSource.value : friendTarget.options[0].value;
}

async function loadImportPage() {
  for (const el of [shareStatus, friendFormStatus]) el.classList.add("hidden");
  if (!friendSource.options.length) {
    fillMondays(friendSource, -26, 26); // their past blocks can be copied too
    fillMondays(friendTarget, 0, 26); // but only onto weeks still ahead
    friendSource.value = isoDate(addDays(thisMonday(), 7));
    followSource();
  }
  try {
    const info = await api("/api/share");
    document.getElementById("share-username").textContent = info.username;
    shareCode.textContent = info.share_code;
  } catch (err) {
    if (err.status !== 401) showStatus(shareStatus, err.message, true);
  }
}

document.getElementById("share-copy").addEventListener("click", async () => {
  const text = `Name: ${document.getElementById("share-username").textContent} · Share code: ${shareCode.textContent}`;
  try {
    await navigator.clipboard.writeText(text);
    showStatus(shareStatus, "Copied. Send it to the person who wants to copy your plan.");
    shareStatus.classList.add("ok");
  } catch (_) {
    const range = document.createRange();
    range.selectNodeContents(shareCode);
    window.getSelection().removeAllRanges();
    window.getSelection().addRange(range);
    showStatus(shareStatus, "Couldn't copy automatically. The code is selected: press Cmd+C.", true);
  }
});

// Two clicks: whoever has the old code can't import from you any more.
shareNew.addEventListener("click", async () => {
  if (!shareNew.classList.contains("confirming")) {
    shareNew.classList.add("confirming");
    shareNew.textContent = "Click again: the old code stops working";
    setTimeout(() => {
      shareNew.classList.remove("confirming");
      shareNew.textContent = "New code";
    }, 5000);
    return;
  }
  shareNew.classList.remove("confirming");
  shareNew.textContent = "New code";
  try {
    shareCode.textContent = (await api("/api/share/code", { method: "POST" })).share_code;
    showStatus(shareStatus, "New code ready. The old one no longer works.");
    shareStatus.classList.add("ok");
  } catch (err) {
    if (err.status !== 401) showStatus(shareStatus, err.message, true);
  }
});

function friendRequest() {
  return {
    username: document.getElementById("friend-username").value.trim(),
    share_code: document.getElementById("friend-code").value.trim(),
    source_start: friendSource.value,
    weeks: Number(friendWeeks.value),
    target_start: friendTarget.value,
  };
}

function resetFriendButtons() {
  friendToPlan.classList.remove("confirming");
  friendToPlan.textContent = "Import to plan";
  friendToCalendar.classList.remove("confirming");
  friendToCalendar.textContent = "Import to calendar";
}

// What you see is what gets imported: any change hides the old preview.
function clearFriendPreview() {
  friendPreview = null;
  friendResult.classList.add("hidden");
}
for (const el of friendForm.querySelectorAll("input, select")) el.addEventListener("input", clearFriendPreview);
friendSource.addEventListener("change", followSource);
friendTarget.addEventListener("change", () => {
  targetTouched = true;
});

friendForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = friendRequest();
  if (!body.username || !body.share_code) return showStatus(friendFormStatus, "Enter their name and share code.", true);
  friendFormStatus.classList.add("hidden");
  try {
    const preview = await api(`/api/share/import/preview?today=${isoDate(new Date())}`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    friendPreview = preview;
    renderDraftPreview(preview, document.getElementById("friend-preview"), `${preview.from_username} has no planned sessions in those weeks.`);
    let summary = "";
    if (preview.sessions_count) {
      const weeks =
        preview.source_start === preview.start_date
          ? `the weeks of ${fmtSpan(preview.start_date, preview.end_date)}`
          : `their weeks of ${fmtSpan(preview.source_start, preview.source_end)}, onto your weeks of ${fmtSpan(preview.start_date, preview.end_date)}`;
      summary = `${plural(preview.sessions_count, "session")} from ${preview.from_username}, ${weeks}.`;
      if (preview.skipped_past)
        summary += ` ${plural(preview.skipped_past, "session")} on days already past ${preview.skipped_past === 1 ? "is" : "are"} left out.`;
      summary += preview.existing_planned
        ? ` Import to calendar replaces the ${plural(preview.existing_planned, "session")} already planned from ${fmtDate(preview.replace_from)} to ${fmtDate(preview.end_date)}.`
        : " Nothing is planned on your calendar on those dates yet.";
    }
    friendSummary.textContent = summary;
    friendSummary.classList.toggle("hidden", !summary);
    friendToPlan.disabled = friendToCalendar.disabled = !preview.sessions_count;
    resetFriendButtons();
    friendStatus.classList.add("hidden");
    friendView.classList.add("hidden");
    friendResult.classList.remove("hidden");
    friendResult.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (err) {
    clearFriendPreview();
    if (err.status !== 401) showStatus(friendFormStatus, err.message, true);
  }
});

// A second click when something of yours would be replaced, instead of a confirm() dialog.
function armed(btn, count, noun, where = "") {
  if (!count || btn.classList.contains("confirming")) return false;
  btn.classList.add("confirming");
  btn.textContent = `Click again: replaces ${plural(count, noun)}${where}`;
  setTimeout(resetFriendButtons, 5000);
  return true;
}

friendToPlan.addEventListener("click", async () => {
  if (!friendPreview || armed(friendToPlan, friendPreview.draft_sessions, "session", " in your draft")) return;
  friendToPlan.disabled = true;
  try {
    await api(`/api/share/import/plan?today=${isoDate(new Date())}`, { method: "POST", body: JSON.stringify(friendRequest()) });
    clearFriendPreview();
    showTab("plan"); // the draft is there, ready to adjust
  } catch (err) {
    if (err.status !== 401) showStatus(friendStatus, err.message, true);
  } finally {
    friendToPlan.disabled = false;
    resetFriendButtons();
  }
});

friendToCalendar.addEventListener("click", async () => {
  if (!friendPreview || armed(friendToCalendar, friendPreview.existing_planned, "planned session")) return;
  friendToCalendar.disabled = friendToPlan.disabled = true;
  try {
    const result = await api(`/api/share/import/calendar?today=${isoDate(new Date())}`, {
      method: "POST",
      body: JSON.stringify(friendRequest()),
    });
    const replaced = result.replaced ? `, replacing ${plural(result.replaced, "planned session")}` : "";
    showStatus(friendStatus, `Done: ${plural(result.added, "session")} are on your calendar${replaced}.`);
    friendStatus.classList.add("ok");
    friendView.classList.remove("hidden");
    friendPreview = { ...friendPreview, existing_planned: result.added }; // importing again would replace these
  } catch (err) {
    if (err.status !== 401) showStatus(friendStatus, err.message, true);
  } finally {
    friendToCalendar.disabled = friendToPlan.disabled = false;
    resetFriendButtons();
  }
});

friendView.addEventListener("click", () => {
  // Like the Plan tab: centre the calendar so the imported weeks start near the top.
  const weeksAhead = Math.round((new Date(friendTarget.value + "T00:00:00") - thisMonday()) / (7 * 86400000));
  weekOffset = Math.max(0, weeksAhead + 2);
  showTab("calendar");
});

// --- Admin dashboard: connections of the last week + account management ---

const DASH_DAYS = 7;
const usersBody = document.getElementById("users-body");
const userStatus = document.getElementById("user-status");
const createStatus = document.getElementById("create-status");
const dashError = document.getElementById("dash-error");

function randomPassword() {
  const alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789";
  const bytes = crypto.getRandomValues(new Uint8Array(14));
  return Array.from(bytes, (b) => alphabet[b % alphabet.length]).join("");
}

function plural(n, word) {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

function fmtDateTime(iso) {
  return new Date(iso).toLocaleString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// "5 min ago", "3 h ago", "Yesterday", "4 days ago", then the date.
function fmtAgo(iso) {
  const then = new Date(iso);
  const minutes = Math.floor((Date.now() - then.getTime()) / 60000);
  if (minutes < 1) return "Just now";
  if (minutes < 60) return `${minutes} min ago`;
  if (minutes < 24 * 60) return `${Math.floor(minutes / 60)} h ago`;
  const startOfToday = new Date();
  startOfToday.setHours(0, 0, 0, 0);
  const days = Math.ceil((startOfToday.getTime() - then.getTime()) / 86400000);
  if (days <= 1) return "Yesterday";
  if (days < 30) return `${days} days ago`;
  return then.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

function timeCell(iso, emptyText) {
  const cell = document.createElement("td");
  if (!iso) {
    cell.textContent = emptyText;
    cell.className = "subtle";
  } else {
    const time = document.createElement("time");
    time.dateTime = iso;
    time.textContent = fmtAgo(iso);
    time.title = fmtDateTime(iso);
    cell.appendChild(time);
  }
  return cell;
}

// Top of the axis: the smallest "clean" number at or above the data.
function axisMax(value) {
  for (const step of [2, 4, 6, 8, 10, 20, 30, 40, 50, 100, 200, 500, 1000]) if (value <= step) return step;
  return Math.ceil(value / 1000) * 1000;
}

function renderLoginsChart(days) {
  const chart = document.getElementById("logins-chart");
  chart.innerHTML = "";
  const peak = Math.max(...days.map((d) => d.logins));
  if (peak === 0) {
    const empty = document.createElement("p");
    empty.className = "subtle chart-empty";
    empty.textContent = "No logins in the last 7 days.";
    chart.appendChild(empty);
    return;
  }
  const top = axisMax(peak);
  const todayIso = days[days.length - 1].date;

  const axis = document.createElement("div");
  axis.className = "bar-axis";
  axis.setAttribute("aria-hidden", "true");
  for (const tick of [top, top / 2, 0]) {
    const label = document.createElement("span");
    label.textContent = tick;
    axis.appendChild(label);
  }

  const plot = document.createElement("div");
  plot.className = "bar-plot";
  plot.setAttribute("role", "list");
  for (const day of days) {
    const date = new Date(day.date + "T00:00:00");
    const summary =
      `${plural(day.logins, "login")}` + (day.failed ? ` · ${plural(day.failed, "failed attempt")}` : "");
    const col = document.createElement("div");
    col.className = "bar-col" + (day.date === todayIso ? " today" : "");
    col.setAttribute("role", "listitem");
    col.tabIndex = 0;
    col.setAttribute(
      "aria-label",
      `${date.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" })}: ${summary}`
    );

    const track = document.createElement("div");
    track.className = "bar-track";
    const bar = document.createElement("div");
    bar.className = "bar";
    bar.style.height = `${(day.logins / top) * 100}%`;
    // Label sparingly: the busiest day and today; the rest is in the tooltip and the table below.
    if (day.logins > 0 && (day.logins === peak || day.date === todayIso)) {
      const value = document.createElement("span");
      value.className = "bar-value";
      value.textContent = day.logins;
      bar.appendChild(value);
    }
    track.appendChild(bar);

    const tip = document.createElement("div");
    tip.className = "bar-tip";
    const tipValue = document.createElement("strong");
    tipValue.textContent = summary;
    const tipLabel = document.createElement("span");
    tipLabel.textContent = date.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
    tip.append(tipValue, tipLabel);

    const label = document.createElement("span");
    label.className = "bar-label";
    label.textContent = day.date === todayIso ? "Today" : date.toLocaleDateString(undefined, { weekday: "short" });

    col.append(track, tip, label);
    plot.appendChild(col);
  }
  chart.append(axis, plot);
}

function renderActivity(activity) {
  const t = activity.totals;
  document.getElementById("stat-accounts").textContent = t.accounts;
  document.getElementById("stat-active").textContent = t.active_accounts;
  document.getElementById("stat-logins").textContent = t.logins;
  document.getElementById("stat-failed").textContent = t.failed;
  renderLoginsChart(activity.days);

  const body = document.getElementById("events-body");
  body.innerHTML = "";
  for (const event of activity.recent) {
    const row = document.createElement("tr");
    const when = document.createElement("td");
    const time = document.createElement("time");
    time.dateTime = event.at;
    time.textContent = fmtDateTime(event.at);
    when.appendChild(time);
    const account = document.createElement("td");
    account.textContent = event.username;
    const result = document.createElement("td");
    const badge = document.createElement("span");
    badge.className = `result-badge ${event.success ? "ok" : "failed"}`;
    badge.textContent = event.success ? "✓ Logged in" : "✕ Wrong password";
    result.appendChild(badge);
    row.append(when, account, result);
    body.appendChild(row);
  }
  document.getElementById("events-empty").classList.toggle("hidden", activity.recent.length > 0);
  body.closest(".table-scroll").classList.toggle("hidden", activity.recent.length === 0);
}

// Two clicks instead of confirm(): a native dialog would block tools driving the page.
function confirmButton(label, armedLabel, ariaName, action) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "btn-link danger";
  btn.textContent = label;
  btn.setAttribute("aria-label", `${label} ${ariaName}`);
  btn.addEventListener("click", async () => {
    if (!btn.classList.contains("confirming")) {
      btn.classList.add("confirming");
      btn.textContent = armedLabel;
      setTimeout(() => {
        btn.classList.remove("confirming");
        btn.textContent = label;
      }, 4000);
      return;
    }
    try {
      await action();
    } catch (err) {
      showStatus(userStatus, err.message, true);
    }
  });
  return btn;
}

function renderResetForm(row, u) {
  const existing = usersBody.querySelector("tr.reset-row");
  if (existing) existing.remove();

  const formRow = document.createElement("tr");
  formRow.className = "reset-row";
  const cell = document.createElement("td");
  cell.colSpan = 6;
  const form = document.createElement("form");
  form.className = "reset-form";
  form.noValidate = true;
  const label = document.createElement("label");
  label.htmlFor = `reset-pw-${u.id}`;
  label.textContent = `New password for ${u.username} (8+ characters)`;
  const input = document.createElement("input");
  input.id = `reset-pw-${u.id}`;
  input.type = "text";
  input.autocomplete = "off";
  input.spellcheck = false;
  input.value = randomPassword();
  const save = document.createElement("button");
  save.type = "submit";
  save.textContent = "Set password";
  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.className = "btn-ghost";
  cancel.textContent = "Cancel";
  cancel.addEventListener("click", () => formRow.remove());
  form.append(label, input, save, cancel);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const pw = input.value;
    if (pw.length < 8) return showStatus(userStatus, "The password needs 8+ characters.", true);
    try {
      await api(`/api/admin/users/${u.id}`, { method: "PATCH", body: JSON.stringify({ password: pw }) });
      if (u.id === me.id) {
        // Resetting your own password logs every session out, this one included.
        clearSession();
        showAuthView();
        return;
      }
      await loadDashboard();
      showStatus(userStatus, `Password for ${u.username} is now: ${pw}  (they are logged out everywhere)`);
      userStatus.classList.add("ok");
    } catch (err) {
      showStatus(userStatus, err.message, true);
    }
  });
  cell.appendChild(form);
  formRow.appendChild(cell);
  row.after(formRow);
  input.focus();
  input.select();
}

// What an account holds, in words: "45 activities, 42 nights of sleep and 25 planned sessions".
function describeAccountData(data) {
  const parts = [
    [data.activities, "activity", "activities"],
    [data.sleep_nights, "night of sleep", "nights of sleep"],
    [data.planned_sessions, "planned session", "planned sessions"],
    [data.objectives, "objective", "objectives"],
  ]
    .filter(([n]) => n > 0)
    .map(([n, one, many]) => `${n} ${n === 1 ? one : many}`);
  if (!parts.length) return "";
  return parts.length === 1 ? parts[0] : `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
}

// Deleting can't be undone, so it says what will be lost and needs the username typed.
function renderDeleteForm(row, u) {
  const existing = usersBody.querySelector("tr.reset-row");
  if (existing) existing.remove();

  const formRow = document.createElement("tr");
  formRow.className = "reset-row delete-row";
  const cell = document.createElement("td");
  cell.colSpan = 6;
  const form = document.createElement("form");
  form.className = "reset-form";
  form.noValidate = true;

  const held = describeAccountData(u.data);
  const warning = document.createElement("p");
  warning.className = "delete-warning";
  warning.textContent = held
    ? `Deleting ${u.username} also deletes its ${held}. This can't be undone.`
    : `${u.username} holds no training data. Deleting it can't be undone.`;

  const label = document.createElement("label");
  label.htmlFor = `delete-confirm-${u.id}`;
  label.textContent = `Type ${u.username} to confirm`;
  const input = document.createElement("input");
  input.id = `delete-confirm-${u.id}`;
  input.type = "text";
  input.autocomplete = "off";
  input.autocapitalize = "none";
  input.spellcheck = false;
  const submit = document.createElement("button");
  submit.type = "submit";
  submit.className = "btn-danger";
  submit.textContent = "Delete account";
  submit.disabled = true;
  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.className = "btn-ghost";
  cancel.textContent = "Cancel";
  cancel.addEventListener("click", () => formRow.remove());
  input.addEventListener("input", () => {
    submit.disabled = input.value.trim().toLowerCase() !== u.username;
  });
  form.append(warning, label, input, submit, cancel);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (submit.disabled) return;
    try {
      await api(`/api/admin/users/${u.id}?confirm=${encodeURIComponent(input.value.trim())}`, { method: "DELETE" });
      await loadDashboard();
      showStatus(userStatus, `Deleted ${u.username}${held ? ` and its ${held}` : ""}.`);
    } catch (err) {
      showStatus(userStatus, err.message, true);
    }
  });
  cell.appendChild(form);
  formRow.appendChild(cell);
  row.after(formRow);
  input.focus();
}

function renderUsers(users) {
  usersBody.innerHTML = "";
  const admins = users.filter((u) => u.is_admin).length;
  document.getElementById("stat-accounts-note").textContent =
    `${plural(users.length - admins, "user")} · ${plural(admins, "admin")}`;

  for (const u of users) {
    const isMe = u.id === me.id;
    const row = document.createElement("tr");
    const name = document.createElement("td");
    name.textContent = u.username + (isMe ? " (you)" : "");
    const role = document.createElement("td");
    role.textContent = u.is_admin ? "Admin" : "User";
    if (!u.has_password) role.textContent += " · no password";
    const lastLogin = timeCell(u.last_login_at, "Never");
    const garmin = document.createElement("td");
    garmin.textContent = u.is_admin ? "–" : u.garmin_connected ? "Connected" : "Not connected";
    const created = document.createElement("td");
    created.textContent = u.created_at
      ? new Date(u.created_at).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" })
      : "–";
    const actions = document.createElement("td");
    actions.className = "row-actions";

    const reset = document.createElement("button");
    reset.type = "button";
    reset.className = "btn-link";
    reset.textContent = "Reset password";
    reset.setAttribute("aria-label", `Reset password for ${u.username}`);
    reset.addEventListener("click", () => renderResetForm(row, u));
    actions.appendChild(reset);

    if (!isMe) {
      const makeAdmin = !u.is_admin;
      actions.appendChild(
        confirmButton(
          makeAdmin ? "Make admin" : "Make user",
          makeAdmin ? "Click again: they lose the calendar view" : "Click again to remove admin rights",
          u.username,
          async () => {
            await api(`/api/admin/users/${u.id}`, { method: "PATCH", body: JSON.stringify({ is_admin: makeAdmin }) });
            await loadDashboard();
            showStatus(userStatus, `${u.username} is now ${makeAdmin ? "an admin" : "a regular user"}.`);
            userStatus.classList.add("ok");
          }
        )
      );
      const del = document.createElement("button");
      del.type = "button";
      del.className = "btn-link danger";
      del.textContent = "Delete";
      del.setAttribute("aria-label", `Delete ${u.username}`);
      del.addEventListener("click", () => renderDeleteForm(row, u));
      actions.appendChild(del);
    }

    row.append(name, role, lastLogin, garmin, created, actions);
    usersBody.appendChild(row);
  }
}

async function loadDashboard() {
  dashError.classList.add("hidden");
  try {
    const tz = new Date().getTimezoneOffset();
    const [activity, users] = await Promise.all([
      api(`/api/admin/activity?days=${DASH_DAYS}&tz_offset=${tz}`),
      api("/api/admin/users"),
    ]);
    renderActivity(activity);
    renderUsers(users.users);
  } catch (err) {
    if (err.status !== 401) showStatus(dashError, `Couldn't load the dashboard: ${err.message}`, true);
  }
}

document.getElementById("dash-refresh").addEventListener("click", loadDashboard);
document.getElementById("gen-password").addEventListener("click", () => {
  document.getElementById("new-password").value = randomPassword();
});

document.getElementById("user-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const username = document.getElementById("new-username").value.trim().toLowerCase();
  const password = document.getElementById("new-password").value;
  if (!/^[a-z0-9._@-]{3,64}$/.test(username))
    return showStatus(createStatus, "Usernames are 3–64 characters: letters, digits, . _ - @", true);
  if (password.length < 8) return showStatus(createStatus, "The password needs 8+ characters.", true);
  try {
    await api("/api/admin/users", {
      method: "POST",
      body: JSON.stringify({ username, password, is_admin: document.getElementById("new-admin").checked }),
    });
    document.getElementById("new-username").value = "";
    document.getElementById("new-admin").checked = false;
    document.getElementById("new-password").value = randomPassword();
    await loadDashboard();
    showStatus(createStatus, `Created ${username}. Send them: username ${username} · password ${password}`);
    createStatus.classList.add("ok");
  } catch (err) {
    showStatus(createStatus, err.message, true);
  }
});

// --- Init ---

memoryToken = getToken();
if (memoryToken) {
  showAppView();
} else {
  showAuthView();
}
