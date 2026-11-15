// The page's entry point (loaded by index.html). It logs in, then opens the
// right screen: the landing page for visitors, the dashboard for an admin, the
// tabs for everyone else. Each screen's code is in views/; what they share is in core/.
//
// Native ES modules, no build step: the server serves these files as they are
// (backend/app/main.py adds ?v=<mtime> to this file's URL so browsers reload it).
// Importing a view runs its top-level code, which wires its buttons; views
// imported only for that effect are the bare `import "./views/..."` lines.

import { api } from "./core/api.js";
import { t, translatePage } from "./core/i18n.js";
import { closeMenus } from "./core/menus.js";
import { getToken, getUser, setSession, setUser } from "./core/session.js";
import { TABS, getCurrentTab, loadTab, registerTab, showTab } from "./core/tabs.js";
import { showLogin, showView } from "./core/ui.js";
import { showDashboard } from "./views/admin.js";
import { loadAthletePage } from "./views/athlete.js";
import { loadCalendar, resetCalendar } from "./views/calendar.js";
import { loadMyAthletes } from "./views/coaching.js";
import { renderGarminState, runSync } from "./views/garmin.js";
import { loadImportPage } from "./views/import.js";
import { loadObjectives } from "./views/objectives.js";
import { loadPlanPage } from "./views/plan.js";
import { renderAccount } from "./views/settings.js";
import "./views/landing.js";
import "./views/night-mode.js";

translatePage();

registerTab("calendar", loadCalendar);
registerTab("athlete", () => Promise.all([loadAthletePage(), loadMyAthletes()])); // objectives load with the calendar
registerTab("plan", loadPlanPage);
registerTab("import", loadImportPage);

// After login (or on load with a saved token): ask the server who this is,
// clear what a previous session left on the page, then open the dashboard
// (admin) or the tabs (member). `sync` starts a Garmin sync right after login.
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
    "#app-view .status, #app-view .error, #admin-view .status, #admin-view .error",
  ))
    el.classList.add("hidden");
  document.getElementById("garmin-password").value = "";
  renderAccount();

  if (me.is_admin) {
    await showDashboard();
    return;
  }
  resetCalendar();
  showView("app-view");
  renderGarminState();
  // Old #history and #logs bookmarks (the History tab, removed) open the calendar.
  showTab(TABS.find((tab) => `#${tab}` === location.hash) || "calendar", { load: false });
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
