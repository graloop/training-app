const TOKEN_KEY = "at_token";
const EMAIL_KEY = "at_email";

let authMode = "login";

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
  if (res.status === 401) {
    clearSession();
    showAuthView();
    throw new Error("Session expired, please log in again.");
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

function setAuthMode(mode) {
  authMode = mode;
  document.getElementById("tab-login").classList.toggle("active", mode === "login");
  document.getElementById("tab-signup").classList.toggle("active", mode === "signup");
  document.getElementById("auth-submit").textContent = mode === "login" ? "Log in" : "Sign up";
  document.getElementById("auth-error").classList.add("hidden");
}

document.getElementById("tab-login").addEventListener("click", () => setAuthMode("login"));
document.getElementById("tab-signup").addEventListener("click", () => setAuthMode("signup"));

document.getElementById("auth-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const email = document.getElementById("auth-email").value.trim();
  const password = document.getElementById("auth-password").value;
  const errorEl = document.getElementById("auth-error");
  errorEl.classList.add("hidden");

  try {
    const path = authMode === "login" ? "/api/auth/login" : "/api/auth/signup";
    const data = await api(path, { method: "POST", body: JSON.stringify({ email, password }) });
    setSession(data.access_token, email);
    showAppView();
  } catch (err) {
    errorEl.textContent = err.message;
    errorEl.classList.remove("hidden");
  }
});

document.getElementById("logout-btn").addEventListener("click", () => {
  clearSession();
  showAuthView();
});

// --- Calendar ---

function fmtDate(d) {
  return new Date(d + "T00:00:00").toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}

function fmtDuration(seconds) {
  if (!seconds) return "";
  const mins = Math.round(seconds / 60);
  return `${mins} min`;
}

const ACTIVITY_ICONS = [
  ["swim", "🏊"],
  ["run", "🏃"],
  ["walk", "🚶"],
  ["hik", "🥾"],
  ["bik", "🚴"],
  ["cycl", "🚴"],
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

function renderCalendar(data) {
  const grid = document.getElementById("calendar-grid");
  grid.innerHTML = "";
  const today = new Date().toISOString().slice(0, 10);

  for (const day of data.days) {
    const cell = document.createElement("div");
    cell.className = "day-cell" + (day.date === today ? " today" : "");

    const dateEl = document.createElement("div");
    dateEl.className = "day-date";
    dateEl.textContent = fmtDate(day.date);
    cell.appendChild(dateEl);

    for (const a of day.activities) {
      const parts = [fmtDuration(a.duration_seconds)];
      if (a.aerobic_training_effect) parts.push(a.aerobic_training_effect.toFixed(1));
      cell.appendChild(makeEntry("done", a.activity_type, parts.filter(Boolean).join(" · ")));
    }

    for (const p of day.planned) {
      const detail = p.planned_duration_minutes ? `${p.planned_duration_minutes} min` : "";
      cell.appendChild(makeEntry("planned", p.activity_type, detail));
    }

    grid.appendChild(cell);
  }
}

async function loadCalendar() {
  const data = await api("/api/calendar");
  renderCalendar(data);
}

// --- Garmin sync ---

const syncBtn = document.getElementById("sync-btn");
const syncStatus = document.getElementById("sync-status");
const garminModal = document.getElementById("garmin-modal");
const garminError = document.getElementById("garmin-error");

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
    if (err.message && err.message.toLowerCase().includes("not connected")) {
      garminModal.classList.remove("hidden");
      syncStatus.classList.add("hidden");
    } else {
      showStatus(syncStatus, err.message, true);
    }
  } finally {
    syncBtn.disabled = false;
  }
}

syncBtn.addEventListener("click", runSync);

document.getElementById("garmin-cancel").addEventListener("click", () => {
  garminModal.classList.add("hidden");
});

document.getElementById("garmin-connect").addEventListener("click", async () => {
  const garmin_email = document.getElementById("garmin-email").value.trim();
  const garmin_password = document.getElementById("garmin-password").value;
  garminError.classList.add("hidden");

  try {
    await api("/api/garmin/connect", { method: "POST", body: JSON.stringify({ garmin_email, garmin_password }) });
    garminModal.classList.add("hidden");
    document.getElementById("garmin-password").value = "";
    await runSync();
  } catch (err) {
    garminError.textContent = err.message;
    garminError.classList.remove("hidden");
  }
});

// --- Objectives ---

document.getElementById("objectives-save").addEventListener("click", async () => {
  const text = document.getElementById("objectives-text").value;
  const statusEl = document.getElementById("objectives-status");
  try {
    await api("/api/objectives", { method: "POST", body: JSON.stringify({ text }) });
    showStatus(statusEl, "Saved.");
  } catch (err) {
    showStatus(statusEl, err.message, true);
  }
});

async function loadObjectives() {
  const data = await api("/api/objectives");
  document.getElementById("objectives-text").value = data.text || "";
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
    if (data.plan_changes && data.plan_changes.length > 0) {
      await loadCalendar();
    }
  } catch (err) {
    appendChatMessage("assistant", `Error: ${err.message}`);
  }
});

// --- Init ---

async function loadAll() {
  await Promise.all([loadCalendar(), loadObjectives(), loadChatHistory()]);
}

if (getToken()) {
  showAppView();
} else {
  showAuthView();
}
