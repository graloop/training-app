// The ⚙️ Settings menu in the top bar: Garmin connection buttons (wired in
// garmin.js), changing your own password, log out (wired in main.js).

import { api } from "../core/api.js";
import { setSession } from "../core/session.js";
import { showStatus } from "../core/ui.js";

const settingsBtn = document.getElementById("settings-btn");
const settingsMenu = document.getElementById("settings-menu");

export function setMenuOpen(open) {
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
