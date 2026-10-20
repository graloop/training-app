// Objectives (races, events) on the Athlete tab: one horizontal card each
// (title, date, target, notes), added and edited in place.

import { api } from "../core/api.js";
import { fmtLongDate } from "../core/format.js";
import { t } from "../core/i18n.js";
import { showStatus } from "../core/ui.js";

const objectivesList = document.getElementById("objectives-list");

function daysUntil(iso) {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const days = Math.round((new Date(iso + "T00:00:00") - today) / 86400000);
  if (days === 0) return t("today");
  if (days === 1) return t("tomorrow");
  if (days > 1) return t("in {n} days", { n: days });
  return days === -1 ? t("yesterday") : t("{n} days ago", { n: -days });
}

function objectiveField(label, value) {
  const row = document.createElement("div");
  row.className = "objective-field";
  const name = document.createElement("span");
  name.className = "objective-label";
  name.textContent = label;
  row.append(name, value);
  return row;
}

function renderObjectiveView(card, obj) {
  card.className = "objective-card";
  card.innerHTML = "";
  const title = document.createElement("h3");
  title.textContent = obj.title;
  card.appendChild(title);

  const date = document.createElement("span");
  if (obj.event_date) {
    date.textContent = fmtLongDate(obj.event_date);
    const countdown = document.createElement("span");
    countdown.className = "objective-countdown";
    countdown.textContent = daysUntil(obj.event_date);
    date.appendChild(countdown);
  } else {
    date.textContent = t("Not set");
    date.className = "subtle";
  }
  card.appendChild(objectiveField(t("Event date"), date));

  const target = document.createElement("span");
  target.textContent = obj.target_time || t("Not set");
  if (!obj.target_time) target.className = "subtle";
  card.appendChild(objectiveField(t("Target time"), target));
  const notes = document.createElement("p");
  notes.className = obj.notes ? "objective-notes" : "objective-notes subtle";
  notes.textContent = obj.notes || "–";
  card.appendChild(objectiveField(t("Notes"), notes));

  const actions = document.createElement("div");
  actions.className = "objective-actions";
  const edit = document.createElement("button");
  edit.type = "button";
  edit.className = "btn-link";
  edit.textContent = t("Edit");
  edit.setAttribute("aria-label", t("Edit objective {title}", { title: obj.title }));
  edit.addEventListener("click", () => renderObjectiveEdit(card, obj));
  const del = document.createElement("button");
  del.type = "button";
  del.className = "btn-link danger";
  del.textContent = t("Delete");
  // Two clicks instead of confirm(): a native dialog would block tools driving the page.
  del.addEventListener("click", async () => {
    if (!del.classList.contains("confirming")) {
      del.classList.add("confirming");
      del.textContent = t("Click again to delete");
      del.setAttribute("aria-label", t("Click again to confirm deleting objective {title}", { title: obj.title }));
      setTimeout(() => {
        del.classList.remove("confirming");
        del.textContent = t("Delete");
        del.setAttribute("aria-label", t("Delete objective {title}", { title: obj.title }));
      }, 4000);
      return;
    }
    try {
      await api(`/api/objectives/${obj.id}`, { method: "DELETE" });
    } catch (err) {
      if (err.status !== 401) {
        del.textContent = t("Couldn't delete: {error}", { error: err.message });
        del.classList.remove("confirming");
      }
      return;
    }
    await loadObjectives();
  });
  actions.append(edit, del);
  card.appendChild(actions);
}

function renderObjectiveEdit(card, obj) {
  const isNew = obj.id == null;
  card.className = "objective-card editing";
  card.innerHTML = "";
  const form = document.createElement("form");
  form.className = "objective-form";
  form.noValidate = true;

  const fields = [
    [t("Title"), "text", "title", obj.title, t("e.g. Montreal half marathon")],
    [t("Event date"), "date", "event_date", obj.event_date || "", ""],
    [t("Target time"), "text", "target_time", obj.target_time || "", t("e.g. 1:45:00")],
    [
      t("Notes (priority, what matters most)"),
      "text",
      "notes",
      obj.notes || "",
      t("e.g. Main race of the year. The bike is my weak leg."),
    ],
  ];
  const fieldRow = document.createElement("div");
  fieldRow.className = "objective-inputs";
  const inputs = {};
  for (const [label, type, key, value, placeholder] of fields) {
    const id = `obj-${isNew ? "new" : obj.id}-${key}`;
    const l = document.createElement("label");
    l.htmlFor = id;
    l.textContent = label;
    const input = document.createElement("input");
    input.id = id;
    input.type = type;
    input.value = value;
    input.placeholder = placeholder;
    inputs[key] = input;
    const field = document.createElement("div");
    field.append(l, input);
    fieldRow.appendChild(field);
  }
  form.appendChild(fieldRow);
  inputs.title.required = true;

  const error = document.createElement("p");
  error.className = "error hidden";
  error.setAttribute("role", "alert");
  const actions = document.createElement("div");
  actions.className = "objective-actions";
  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.className = "btn-ghost";
  cancel.textContent = t("Cancel");
  cancel.addEventListener("click", () => (isNew ? card.remove() : renderObjectiveView(card, obj)));
  const save = document.createElement("button");
  save.type = "submit";
  save.textContent = isNew ? t("Save objective") : t("Save");
  actions.append(cancel, save);
  form.append(error, actions);

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!inputs.title.value.trim()) return showStatus(error, t("Give the objective a title."), true);
    const body = JSON.stringify({
      title: inputs.title.value.trim(),
      event_date: inputs.event_date.value || null,
      target_time: inputs.target_time.value.trim(),
      notes: inputs.notes.value.trim(),
    });
    try {
      if (isNew) await api("/api/objectives", { method: "POST", body });
      else await api(`/api/objectives/${obj.id}`, { method: "PATCH", body });
      await loadObjectives();
    } catch (err) {
      showStatus(error, err.message, true);
    }
  });

  card.appendChild(form);
  inputs.title.focus();
}

function renderObjectives(objectives) {
  objectivesList.innerHTML = "";
  if (!objectives.length) {
    const empty = document.createElement("p");
    empty.className = "subtle objectives-empty";
    empty.textContent = t("No objective yet. Add your next event with + Add objective.");
    objectivesList.appendChild(empty);
    return;
  }
  for (const obj of objectives) {
    const card = document.createElement("div");
    renderObjectiveView(card, obj);
    objectivesList.appendChild(card);
  }
}

export async function loadObjectives() {
  let data;
  try {
    data = await api("/api/objectives");
  } catch (err) {
    if (err.status === 401) return;
    objectivesList.innerHTML = "";
    const failed = document.createElement("p");
    failed.className = "error";
    failed.textContent = t("Couldn't load the objectives: {error}", { error: err.message });
    objectivesList.appendChild(failed);
    return;
  }
  renderObjectives(data.objectives);
}

document.getElementById("objective-add").addEventListener("click", () => {
  const existing = objectivesList.querySelector("#obj-new-title");
  if (existing) return existing.focus();
  const empty = objectivesList.querySelector(".objectives-empty");
  if (empty) empty.remove();
  const card = document.createElement("div");
  objectivesList.prepend(card);
  renderObjectiveEdit(card, { id: null, title: "", event_date: null, target_time: null, notes: null });
});
