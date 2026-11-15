// Admin dashboard (admin accounts see this instead of the calendar): the
// connections of the last week, and creating / managing accounts.
// API: GET /api/admin/activity, GET/POST /api/admin/users, PATCH/DELETE
// /api/admin/users/{id} (backend/app/routers/admin.py). Markup: #admin-view.
// Styles: css/admin.css. Opened by main.js through showDashboard().
//
// Everything is redrawn from the server's answer after each change
// (loadDashboard), so the page never keeps its own copy of the accounts.

import { api } from "../core/api.js";
import { showAvatar } from "../core/avatars.js";
import { plural } from "../core/format.js";
import { locale, t } from "../core/i18n.js";
import { clearSession, getUser, setUser } from "../core/session.js";
import { showStatus, showView } from "../core/ui.js";
import { renderAccount } from "./settings.js";

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

function fmtDateTime(iso) {
  return new Date(iso).toLocaleString(locale(), {
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
  if (minutes < 1) return t("Just now");
  if (minutes < 60) return t("{n} min ago", { n: minutes });
  if (minutes < 24 * 60) return t("{n} h ago", { n: Math.floor(minutes / 60) });
  const startOfToday = new Date();
  startOfToday.setHours(0, 0, 0, 0);
  const days = Math.ceil((startOfToday.getTime() - then.getTime()) / 86400000);
  if (days <= 1) return t("Yesterday");
  if (days < 30) return t("{n} days ago", { n: days });
  return then.toLocaleDateString(locale(), { year: "numeric", month: "short", day: "numeric" });
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

// --- Connections of the last week ---

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
    empty.textContent = t("No logins in the last 7 days.");
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
    const summary = plural(day.logins, "login") + (day.failed ? ` · ${plural(day.failed, "failed attempt")}` : "");
    const col = document.createElement("div");
    col.className = "bar-col" + (day.date === todayIso ? " today" : "");
    col.setAttribute("role", "listitem");
    col.tabIndex = 0;
    col.setAttribute(
      "aria-label",
      `${date.toLocaleDateString(locale(), { weekday: "long", month: "long", day: "numeric" })}: ${summary}`,
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
    tipLabel.textContent = date.toLocaleDateString(locale(), { weekday: "short", month: "short", day: "numeric" });
    tip.append(tipValue, tipLabel);

    const label = document.createElement("span");
    label.className = "bar-label";
    label.textContent = day.date === todayIso ? t("Today") : date.toLocaleDateString(locale(), { weekday: "short" });

    col.append(track, tip, label);
    plot.appendChild(col);
  }
  chart.append(axis, plot);
}

// The stat tiles, the chart and the table of recent attempts, from GET /api/admin/activity.
function renderActivity(activity) {
  const totals = activity.totals;
  document.getElementById("stat-accounts").textContent = totals.accounts;
  document.getElementById("stat-active").textContent = totals.active_accounts;
  document.getElementById("stat-logins").textContent = totals.logins;
  document.getElementById("stat-failed").textContent = totals.failed;
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
    badge.textContent = event.success ? t("✓ Logged in") : t("✕ Wrong password");
    result.appendChild(badge);
    row.append(when, account, result);
    body.appendChild(row);
  }
  document.getElementById("events-empty").classList.toggle("hidden", activity.recent.length > 0);
  body.closest(".table-scroll").classList.toggle("hidden", activity.recent.length === 0);
}

// --- Accounts ---

// Opens a row under the account with a generated password to give them (PATCH resets it and logs them out).
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
  label.textContent = t("New password for {name} (8+ characters)", { name: u.username });
  const input = document.createElement("input");
  input.id = `reset-pw-${u.id}`;
  input.type = "text";
  input.autocomplete = "off";
  input.spellcheck = false;
  input.value = randomPassword();
  const save = document.createElement("button");
  save.type = "submit";
  save.textContent = t("Set password");
  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.className = "btn-ghost";
  cancel.textContent = t("Cancel");
  cancel.addEventListener("click", () => formRow.remove());
  form.append(label, input, save, cancel);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const pw = input.value;
    if (pw.length < 8) return showStatus(userStatus, t("The password needs 8+ characters."), true);
    try {
      await api(`/api/admin/users/${u.id}`, { method: "PATCH", body: JSON.stringify({ password: pw }) });
      if (u.id === getUser().id) {
        // Resetting your own password logs every session out, this one included.
        clearSession();
        showView("auth-view");
        return;
      }
      await loadDashboard();
      showStatus(
        userStatus,
        t("Password for {name} is now: {password}  (they are logged out everywhere)", {
          name: u.username,
          password: pw,
        }),
      );
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
    .map(([n, one, many]) => plural(n, one, many));
  if (!parts.length) return "";
  return parts.length === 1
    ? parts[0]
    : t("{list} and {last}", { list: parts.slice(0, -1).join(", "), last: parts[parts.length - 1] });
}

// Renaming: the new login name, checked like at creation. The account stays logged in.
function renderRenameForm(row, u) {
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
  label.htmlFor = `rename-${u.id}`;
  label.textContent = t("New username for {name}", { name: u.username });
  const input = document.createElement("input");
  input.id = `rename-${u.id}`;
  input.type = "text";
  input.autocomplete = "off";
  input.autocapitalize = "none";
  input.spellcheck = false;
  input.value = u.username;
  const save = document.createElement("button");
  save.type = "submit";
  save.textContent = t("Rename");
  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.className = "btn-ghost";
  cancel.textContent = t("Cancel");
  cancel.addEventListener("click", () => formRow.remove());
  form.append(label, input, save, cancel);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const username = input.value.trim().toLowerCase();
    if (!/^[a-z0-9._@-]{3,64}$/.test(username))
      return showStatus(userStatus, t("Usernames are 3–64 characters: letters, digits, . _ - @"), true);
    if (username === u.username) return formRow.remove();
    try {
      await api(`/api/admin/users/${u.id}`, { method: "PATCH", body: JSON.stringify({ username }) });
      if (u.id === getUser().id) {
        setUser({ ...getUser(), username });
        renderAccount();
      }
      await loadDashboard();
      showStatus(
        userStatus,
        t("{old} is now called {new}. Tell them their new login name.", { old: u.username, new: username }),
      );
      userStatus.classList.add("ok");
    } catch (err) {
      showStatus(userStatus, err.message, true);
    }
  });
  cell.appendChild(form);
  formRow.appendChild(cell);
  row.after(formRow);
  input.select();
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
    ? t("Deleting {name} also deletes its {held}. This can't be undone.", { name: u.username, held })
    : t("{name} holds no training data. Deleting it can't be undone.", { name: u.username });

  const label = document.createElement("label");
  label.htmlFor = `delete-confirm-${u.id}`;
  label.textContent = t("Type {name} to confirm", { name: u.username });
  const input = document.createElement("input");
  input.id = `delete-confirm-${u.id}`;
  input.type = "text";
  input.autocomplete = "off";
  input.autocapitalize = "none";
  input.spellcheck = false;
  const submit = document.createElement("button");
  submit.type = "submit";
  submit.className = "btn-danger";
  submit.textContent = t("Delete account");
  submit.disabled = true;
  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.className = "btn-ghost";
  cancel.textContent = t("Cancel");
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
      showStatus(
        userStatus,
        held
          ? t("Deleted {name} and its {held}.", { name: u.username, held })
          : t("Deleted {name}.", { name: u.username }),
      );
    } catch (err) {
      showStatus(userStatus, err.message, true);
    }
  });
  cell.appendChild(form);
  formRow.appendChild(cell);
  row.after(formRow);
  input.focus();
}

const ROLES = [
  ["athlete", "Athlete"],
  ["coach", "Coach"],
  ["admin", "Admin"],
];
const ROLE_DONE = {
  athlete: "{name} is now an athlete.",
  coach: "{name} is now a coach: they add their athletes on their Athlete tab.",
  admin: "{name} is now an admin: they see this dashboard instead of a calendar.",
};

// Athlete, coach or admin. Your own role can't change (you'd lose this dashboard).
function roleSelect(u, isMe) {
  const select = document.createElement("select");
  select.className = "role-select";
  select.setAttribute("aria-label", t("Role of {name}", { name: u.username }));
  for (const [value, label] of ROLES) select.appendChild(new Option(t(label), value));
  select.value = u.role;
  select.disabled = isMe;
  select.addEventListener("change", async () => {
    try {
      await api(`/api/admin/users/${u.id}`, { method: "PATCH", body: JSON.stringify({ role: select.value }) });
      await loadDashboard();
      showStatus(userStatus, t(ROLE_DONE[select.value], { name: u.username }));
      userStatus.classList.add("ok");
    } catch (err) {
      select.value = u.role;
      showStatus(userStatus, err.message, true);
    }
  });
  return select;
}

// The accounts table: one row each with its role, last login, Garmin, created date and actions.
function renderUsers(users) {
  usersBody.innerHTML = "";
  const count = (role) => users.filter((u) => u.role === role).length;
  document.getElementById("stat-accounts-note").textContent =
    `${plural(count("athlete"), "athlete")} · ${plural(count("coach"), "coach", "coaches")} · ${plural(count("admin"), "admin")}`;

  for (const u of users) {
    const isMe = u.id === getUser().id;
    const row = document.createElement("tr");
    const name = document.createElement("td");
    const face = document.createElement("span");
    face.className = "avatar avatar-small";
    face.setAttribute("aria-hidden", "true");
    showAvatar(face, u.avatar_url);
    const label = document.createElement("span");
    label.textContent = u.username + (isMe ? t(" (you)") : "");
    name.className = "account-name";
    name.append(face, label);
    const role = document.createElement("td");
    role.appendChild(roleSelect(u, isMe));
    if (!u.has_password) role.append(t(" · no password"));
    const lastLogin = timeCell(u.last_login_at, t("Never"));
    const garmin = document.createElement("td");
    garmin.textContent = u.is_admin ? "–" : u.garmin_connected ? t("Connected") : t("Not connected");
    const created = document.createElement("td");
    created.textContent = u.created_at
      ? new Date(u.created_at).toLocaleDateString(locale(), { year: "numeric", month: "short", day: "numeric" })
      : "–";
    const actions = document.createElement("td");
    actions.className = "row-actions";

    const reset = document.createElement("button");
    reset.type = "button";
    reset.className = "btn-link";
    reset.textContent = t("Reset password");
    reset.setAttribute("aria-label", t("Reset password for {name}", { name: u.username }));
    reset.addEventListener("click", () => renderResetForm(row, u));
    actions.appendChild(reset);

    const rename = document.createElement("button");
    rename.type = "button";
    rename.className = "btn-link";
    rename.textContent = t("Rename");
    rename.setAttribute("aria-label", t("Rename {name}", { name: u.username }));
    rename.addEventListener("click", () => renderRenameForm(row, u));
    actions.appendChild(rename);

    if (!isMe) {
      const del = document.createElement("button");
      del.type = "button";
      del.className = "btn-link danger";
      del.textContent = t("Delete");
      del.setAttribute("aria-label", t("Delete {name}", { name: u.username }));
      del.addEventListener("click", () => renderDeleteForm(row, u));
      actions.appendChild(del);
    }

    row.append(name, role, lastLogin, garmin, created, actions);
    usersBody.appendChild(row);
  }
}

// Fetches both parts at once and redraws them. The timezone offset makes the server count days in the admin's own time.
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
    if (err.status !== 401)
      showStatus(dashError, t("Couldn't load the dashboard: {error}", { error: err.message }), true);
  }
}

// Opens the dashboard (after an admin logs in).
export async function showDashboard() {
  showView("admin-view");
  document.getElementById("new-password").value = randomPassword();
  await loadDashboard();
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
    return showStatus(createStatus, t("Usernames are 3–64 characters: letters, digits, . _ - @"), true);
  if (password.length < 8) return showStatus(createStatus, t("The password needs 8+ characters."), true);
  try {
    await api("/api/admin/users", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
    document.getElementById("new-username").value = "";
    document.getElementById("new-password").value = randomPassword();
    await loadDashboard();
    showStatus(
      createStatus,
      t("Created {name}. Send them: username {name} · password {password}", { name: username, password }),
    );
    createStatus.classList.add("ok");
  } catch (err) {
    showStatus(createStatus, err.message, true);
  }
});
