// Settings: one dialog, opened by the ⚙️ and by the badge with your name in
// the top bar. Profile photo, username, password, your coaches, language,
// switch account and log out are here; the Garmin section is wired in garmin.js.
// API: PUT/DELETE /api/auth/avatar, POST /api/auth/username, POST
// /api/auth/password (returns a fresh token: the others stop working),
// GET /api/coaches, DELETE /api/coaches/{link_id}.
// Markup: #settings-dialog. Styles: css/settings.css.

import { api } from "../core/api.js";
import { showAvatar } from "../core/avatars.js";
import { getLang, setLang, t } from "../core/i18n.js";
import { clearSession, getUser, setSession, setUser } from "../core/session.js";
import { showLogin, showStatus, showView } from "../core/ui.js";

const dialog = document.getElementById("settings-dialog");

// The top bar's badge and the dialog's preview, from the logged-in account.
export function renderAccount() {
  const me = getUser();
  document.getElementById("user-name").textContent = me.username + (me.is_admin ? ` · ${t("admin")}` : "");
  showAvatar(document.getElementById("user-avatar"), me.avatar_url);
  showAvatar(document.getElementById("avatar-preview"), me.avatar_url);
  document.getElementById("avatar-remove").classList.toggle("hidden", !me.avatar_url);
  document.getElementById("garmin-settings").classList.toggle("hidden", Boolean(me.is_admin));
}

export function openSettings() {
  for (const el of dialog.querySelectorAll(".status, .error")) el.classList.add("hidden");
  document.getElementById("username-new").value = getUser().username;
  for (const id of ["username-password", "pw-current", "pw-new"]) document.getElementById(id).value = "";
  dialog.showModal();
  document.getElementById("username-new").focus();
  loadCoaches();
}

// --- My coaches: who sees your calendar and plans for you (hidden when nobody does) ---

const coachesSection = document.getElementById("coaches-settings");
const coachesStatus = document.getElementById("coaches-status");

async function loadCoaches() {
  coachesSection.classList.add("hidden");
  if (getUser().is_admin) return;
  let coaches;
  try {
    ({ coaches } = await api("/api/coaches"));
  } catch (_) {
    return; // not essential: the rest of Settings works without it
  }
  const list = document.getElementById("coaches-list");
  list.innerHTML = "";
  for (const coach of coaches) {
    const item = document.createElement("li");
    item.className = "person";
    const face = document.createElement("span");
    face.className = "avatar";
    showAvatar(face, coach.avatar_url);
    const name = document.createElement("strong");
    name.className = "person-text";
    name.textContent = coach.username;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "btn-link danger";
    remove.textContent = t("Remove");
    remove.setAttribute("aria-label", t("Remove {name} as your coach", { name: coach.username }));
    remove.addEventListener("click", async () => {
      if (!remove.classList.contains("confirming")) {
        remove.classList.add("confirming");
        remove.textContent = t("Click again to remove");
        setTimeout(() => {
          remove.classList.remove("confirming");
          remove.textContent = t("Remove");
        }, 4000);
        return;
      }
      try {
        await api(`/api/coaches/${coach.link_id}`, { method: "DELETE" });
        await loadCoaches();
        showStatus(coachesStatus, t("{name} is no longer your coach.", { name: coach.username }));
        coachesStatus.classList.add("ok");
        coachesSection.classList.remove("hidden"); // the message shows even after the last one
      } catch (err) {
        if (err.status !== 401) showStatus(coachesStatus, err.message, true);
      }
    });
    item.append(face, name, remove);
    list.appendChild(item);
  }
  coachesSection.classList.toggle("hidden", coaches.length === 0);
}

for (const id of ["settings-btn", "account-btn"]) document.getElementById(id).addEventListener("click", openSettings);
document.getElementById("settings-close").addEventListener("click", () => dialog.close());
dialog.addEventListener("click", (e) => {
  if (e.target === dialog) dialog.close(); // a click on the backdrop
});

// --- Profile photo: cropped to a square and shrunk here, so a few KB are sent ---

const AVATAR_SIZE = 256;
const avatarStatus = document.getElementById("avatar-status");

function squareJpeg(file) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    const src = URL.createObjectURL(file);
    img.onload = () => {
      const side = Math.min(img.naturalWidth, img.naturalHeight);
      const canvas = document.createElement("canvas");
      canvas.width = canvas.height = AVATAR_SIZE;
      canvas
        .getContext("2d")
        .drawImage(
          img,
          (img.naturalWidth - side) / 2,
          (img.naturalHeight - side) / 2,
          side,
          side,
          0,
          0,
          AVATAR_SIZE,
          AVATAR_SIZE,
        );
      URL.revokeObjectURL(src);
      canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error())), "image/jpeg", 0.85);
    };
    img.onerror = () => {
      URL.revokeObjectURL(src);
      reject(new Error());
    };
    img.src = src;
  });
}

document.getElementById("avatar-file").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  e.target.value = "";
  if (!file) return;
  let photo;
  try {
    photo = await squareJpeg(file);
  } catch (_) {
    return showStatus(avatarStatus, t("This file isn't an image the browser can read."), true);
  }
  try {
    setUser(await api("/api/auth/avatar", { method: "PUT", body: photo }));
    renderAccount();
    avatarStatus.classList.add("hidden");
  } catch (err) {
    if (err.status !== 401) showStatus(avatarStatus, err.message, true);
  }
});

document.getElementById("avatar-remove").addEventListener("click", async () => {
  try {
    setUser(await api("/api/auth/avatar", { method: "DELETE" }));
    renderAccount();
  } catch (err) {
    if (err.status !== 401) showStatus(avatarStatus, err.message, true);
  }
});

// --- Username (the password is asked again; you stay logged in) ---

document.getElementById("username-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const status = document.getElementById("username-status");
  const username = document.getElementById("username-new").value.trim().toLowerCase();
  const password = document.getElementById("username-password");
  if (!/^[a-z0-9._@-]{3,64}$/.test(username))
    return showStatus(status, t("Usernames are 3–64 characters: letters, digits, . _ - @"), true);
  try {
    setUser(
      await api("/api/auth/username", {
        method: "POST",
        body: JSON.stringify({ new_username: username, current_password: password.value }),
      }),
    );
    password.value = "";
    renderAccount();
    showStatus(status, t("Saved. Log in with {name} from now on.", { name: username }));
    status.classList.add("ok");
  } catch (err) {
    if (err.status !== 401) showStatus(status, err.message, true);
  }
});

// --- Password ---

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

// --- Language (kept in this browser) ---

const langSelect = document.getElementById("lang-select");
langSelect.value = getLang();
langSelect.addEventListener("change", () => {
  if (setLang(langSelect.value)) return; // the page reloads
  langSelect.value = getLang();
  showStatus(document.getElementById("lang-status"), t("This browser won't save the language."), true);
});

// --- Leaving ---

document.getElementById("logout-btn").addEventListener("click", () => {
  dialog.close();
  clearSession();
  showView("auth-view");
  window.scrollTo(0, 0);
});

// Log out, straight into the Log in window.
document.getElementById("switch-account-btn").addEventListener("click", () => {
  dialog.close();
  clearSession();
  showLogin();
  window.scrollTo(0, 0);
});
