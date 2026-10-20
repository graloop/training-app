// The account menu, opened from the badge with your name in the top bar:
// changing your own password, switching to another account, logging out.

import { api } from "../core/api.js";
import { t } from "../core/i18n.js";
import { registerMenu } from "../core/menus.js";
import { clearSession, setSession } from "../core/session.js";
import { showLogin, showStatus, showView } from "../core/ui.js";

registerMenu("account-btn", "account-menu", () => {
  document.getElementById("pw-status").classList.add("hidden");
  document.getElementById("pw-current").focus();
});

// --- Change own password ---

document.getElementById("password-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const status = document.getElementById("pw-status");
  const current = document.getElementById("pw-current");
  const next = document.getElementById("pw-new");
  if (next.value.length < 8) return showStatus(status, t("The new password needs 8+ characters."), true);
  try {
    const data = await api("/api/auth/password", {
      method: "POST",
      body: JSON.stringify({ current_password: current.value, new_password: next.value }),
    });
    setSession(data.access_token);
    current.value = next.value = "";
    showStatus(status, t("Password changed. Your other devices are logged out."));
    status.classList.add("ok");
  } catch (err) {
    showStatus(status, err.message, true);
  }
});

// --- Leaving ---

document.getElementById("logout-btn").addEventListener("click", () => {
  clearSession();
  showView("auth-view");
  window.scrollTo(0, 0);
});

// Log out, straight into the Log in window.
document.getElementById("switch-account-btn").addEventListener("click", () => {
  clearSession();
  showLogin();
  window.scrollTo(0, 0);
});
