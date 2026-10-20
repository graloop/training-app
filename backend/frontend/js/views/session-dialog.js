// The "Plan a session" / "Edit session" dialog, opened from a day of the
// calendar. An existing session can also be moved to another day. The caller
// says what to refresh once a session is saved, moved or deleted.

import { api } from "../core/api.js";
import { locale, t } from "../core/i18n.js";
import { SPORTS, activityIcon, sportLabel } from "../core/sports.js";
import { showStatus } from "../core/ui.js";

const planDialog = document.getElementById("plan-dialog");
const planForm = document.getElementById("plan-form");
const planSports = document.getElementById("plan-sports");
const planHours = document.getElementById("plan-hours");
const planMinutes = document.getElementById("plan-minutes");
const planError = document.getElementById("plan-error");
const planDelete = document.getElementById("plan-delete");
const planNotes = document.getElementById("plan-notes");
const planMove = document.getElementById("plan-move");
const planMoveField = document.getElementById("plan-move-field");
const planMoveDate = document.getElementById("plan-move-date");
let target = null; // { date, existing, onSaved }

function renderSportOptions(extraType) {
  planSports.innerHTML = "";
  const sports = [...SPORTS];
  if (extraType && !SPORTS.some((s) => s.type === extraType)) sports.push({ type: extraType });
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
    text.textContent = sportLabel(sport.type);
    label.append(input, icon, text);
    planSports.appendChild(label);
  }
}

// `existing` is a planned session to edit, or null to plan a new one.
export function openSessionDialog(date, existing, onSaved) {
  target = { date, existing, onSaved };
  renderSportOptions(existing && existing.activity_type);
  document.getElementById("plan-title").textContent = existing ? t("Edit session") : t("Plan a session");
  document.getElementById("plan-date").textContent = new Date(date + "T00:00:00").toLocaleDateString(locale(), {
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
  planMove.classList.toggle("hidden", !existing);
  planMoveField.classList.add("hidden");
  planMoveDate.value = date;
  planError.classList.add("hidden");
  planDialog.showModal();
  (checked || planSports.querySelector("input")).focus();
}

planForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const sport = planForm.querySelector('input[name="sport"]:checked');
  const minutes = (Number(planHours.value) || 0) * 60 + (Number(planMinutes.value) || 0);
  if (!sport) return showStatus(planError, t("Pick a sport."), true);
  if (minutes <= 0) return showStatus(planError, t("Set how long the session is."), true);

  const body = { activity_type: sport.value, planned_duration_minutes: minutes, notes: planNotes.value.trim() || null };
  const moving = target.existing && !planMoveField.classList.contains("hidden");
  if (moving && !planMoveDate.value) return showStatus(planError, t("Pick the day to move it to."), true);
  if (moving && planMoveDate.value !== target.date) body.date = planMoveDate.value;
  try {
    if (target.existing) {
      await api(`/api/planned/${target.existing.id}`, { method: "PATCH", body: JSON.stringify(body) });
    } else {
      await api("/api/planned", { method: "POST", body: JSON.stringify({ ...body, date: target.date }) });
    }
    planDialog.close();
    await target.onSaved();
  } catch (err) {
    showStatus(planError, err.message, true);
  }
});

planDelete.addEventListener("click", async () => {
  try {
    await api(`/api/planned/${target.existing.id}`, { method: "DELETE" });
    planDialog.close();
    await target.onSaved();
  } catch (err) {
    showStatus(planError, err.message, true);
  }
});

// Shows the day picker; Save moves the session (with any other change made).
planMove.addEventListener("click", () => {
  planMove.classList.add("hidden");
  planMoveField.classList.remove("hidden");
  planMoveDate.focus();
});

document.getElementById("plan-cancel").addEventListener("click", () => planDialog.close());
planDialog.addEventListener("click", (e) => {
  if (e.target === planDialog) planDialog.close(); // click on the backdrop
});
