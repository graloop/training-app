// The page's entry point (loaded by index.html). It logs in, then opens the
// right screen: the landing page for visitors, the dashboard for an admin, the
// tabs for everyone else. Each screen's code is in views/; what they share is in core/.

import { api } from "./core/api.js";
import { t, translatePage } from "./core/i18n.js";
import { closeMenus } from "./core/menus.js";
import { getToken, getUser, setSession, setUser } from "./core/session.js";
import { TABS, getCurrentTab, loadTab, registerTab, showTab } from "./core/tabs.js";
import { showLogin, showView } from "./core/ui.js";
import { showDashboard } from "./views/admin.js";
import { loadAthletePage } from "./views/athlete.js";
import { loadCalendar, resetCalendar } from "./views/calendar.js";
import { renderGarminState, runSync } from "./views/garmin.js";
import { loadHistoryPage } from "./views/history.js";
import { loadImportPage } from "./views/import.js";
import { loadObjectives } from "./views/objectives.js";
import { loadPlanPage } from "./views/plan.js";
import "./views/account.js";
import "./views/landing.js";
import "./views/settings.js";

translatePage();

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
    if (err.status !== 401) showLogin(err.message);
    return;
  }
  const me = getUser();
  // Nothing from a previous session should linger (status lines, half-typed forms).
  for (const el of document.querySelectorAll(
    "#app-view .status, #app-view .error, #admin-view .status, #admin-view .error, .menu-panel .status",
  ))
    el.classList.add("hidden");
  document.getElementById("garmin-password").value = "";
  document.getElementById("user-name").textContent = me.username + (me.is_admin ? ` · ${t("admin")}` : "");
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
  submitBtn.textContent = t("Logging in…");

  try {
    const data = await api("/api/auth/login", { method: "POST", body: JSON.stringify({ username, password }) });
    document.getElementById("auth-password").value = "";
    setSession(data.access_token);
    closeMenus();
    window.scrollTo(0, 0);
    await showAppView({ sync: true });
  } catch (err) {
    errorEl.textContent = err.message;
    errorEl.classList.remove("hidden");
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = t("Log in");
  }
});

// --- Start: a token saved by an earlier visit opens the app directly; otherwise the landing page ---

if (getToken()) {
  showAppView();
} else {
  showView("auth-view");
}
