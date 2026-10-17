// The member's tabs (Calendar, Athlete, Plan, Import, History). main.js tells
// this file how each tab loads (registerTab), so it needs none of the views and
// any view can switch tabs without a circular import.

export const TABS = ["calendar", "athlete", "plan", "import", "history"];

const loaders = {};
let currentTab = "calendar";

export function registerTab(name, load) {
  loaders[name] = load;
}

export function getCurrentTab() {
  return currentTab;
}

// Reloads a tab's data from the server (returns its promise).
export function loadTab(name) {
  return loaders[name]();
}

export function showTab(name, { load = true } = {}) {
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
  if (load) loadTab(name);
}

for (const tab of TABS) document.getElementById(`tab-btn-${tab}`).addEventListener("click", () => showTab(tab));
