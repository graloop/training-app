// The page's entry point (loaded by index.html). It logs in and out, then
// opens the right screen: the dashboard for an admin, the tabs for everyone
// else. Each tab's code is in views/; what they share is in core/.

import { api } from "./core/api.js";
import { clearSession, getToken, getUser, setSession, setUser } from "./core/session.js";
import { TABS, getCurrentTab, loadTab, registerTab, showTab } from "./core/tabs.js";
import { showStatus, showView } from "./core/ui.js";
import { showDashboard } from "./views/admin.js";
import { loadAthletePage } from "./views/athlete.js";
import { loadCalendar, resetCalendar } from "./views/calendar.js";
import { renderGarminState, runSync } from "./views/garmin.js";
import { loadHistoryPage } from "./views/history.js";
import { loadImportPage } from "./views/import.js";
import { loadObjectives } from "./views/objectives.js";
import { loadPlanPage } from "./views/plan.js";
import { setMenuOpen } from "./views/settings.js";

registerTab("calendar", loadCalendar);
registerTab("athlete", loadAthletePage); // its objectives are loaded with the calendar, at login
registerTab("plan", loadPlanPage);
registerTab("import", loadImportPage);
registerTab("history", loadHistoryPage);

// Regular accounts get the calendar; admin accounts get the dashboard instead.
async function showAppView({ sync = false } = {}) {
  try {
    setUser(await api("/api/auth/me"));
  } catch (err) {
    if (err.status !== 401) {
      showView("auth-view");
      showStatus(document.getElementById("auth-error"), err.message, true);
    }
    return;
  }
  const me = getUser();
  // Nothing from a previous session should linger (status lines, half-typed forms).
  for (const el of document.querySelectorAll(
    "#app-view .status, #app-view .error, #admin-view .status, #admin-view .error, #settings-menu .status"
  ))
    el.classList.add("hidden");
  document.getElementById("garmin-password").value = "";
  document.getElementById("user-name").textContent = me.username + (me.is_admin ? " · admin" : "");
  document.getElementById("garmin-settings").classList.toggle("hidden", me.is_admin);

  if (me.is_admin) {
    await showDashboard();
    return;
  }
  resetCalendar();
  showView("app-view");
  renderGarminState();
  const hash = location.hash === "#logs" ? "#history" : location.hash; // the History tab used to be "Logs"
  showTab(TABS.find((tab) => `#${tab}` === hash) || "calendar", { load: false });
  await Promise.all([loadCalendar(), loadObjectives()]);
  if (getCurrentTab() !== "calendar") await loadTab(getCurrentTab());
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
  showView("auth-view");
});

// --- Start: a token saved by an earlier visit opens the app directly ---

if (getToken()) {
  showAppView();
} else {
  showView("auth-view");
}
