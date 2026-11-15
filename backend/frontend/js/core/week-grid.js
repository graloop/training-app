// The pieces of a Monday–Sunday grid: the header row, a day cell, one entry
// (a session) and the + button of a day. Shared by the Calendar tab
// (views/calendar.js) and the Plan tab's draft calendar (views/plan.js), so
// both look the same. Styles: css/calendar.css (.calendar-grid, .day-cell, .entry).

import { fmtDate, fmtLongDate } from "./format.js";
import { t } from "./i18n.js";
import { activityIcon } from "./sports.js";

// The header row: Mon … Sun, then the label of the week column.
export function gridHead(grid, lastLabel = "Week total") {
  for (const label of ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun", lastLabel]) {
    const head = document.createElement("div");
    head.className = "grid-head";
    head.textContent = t(label);
    grid.appendChild(head);
  }
}

// An empty day: its date at the top (`head`, where badges can be added), today framed, past days greyed.
export function dayCell(date, today) {
  const cell = document.createElement("div");
  cell.className = "day-cell" + (date === today ? " today" : "") + (date < today ? " past" : "");
  cell.dataset.date = date;
  const head = document.createElement("div");
  head.className = "day-head";
  const dateEl = document.createElement("span");
  dateEl.className = "day-date";
  dateEl.textContent = fmtDate(date);
  dateEl.title = date;
  head.appendChild(dateEl);
  cell.appendChild(head);
  return { cell, head };
}

// One session in a day: its sport icon, the time, and an optional grey extra (" · load 80").
export function makeEntry(className, type, detail, extra) {
  const entry = document.createElement("div");
  entry.className = `entry ${className}`;
  entry.title = type;

  const icon = document.createElement("span");
  icon.className = "entry-icon";
  icon.textContent = activityIcon(type);
  entry.appendChild(icon);

  const detailEl = document.createElement("span");
  detailEl.className = "entry-detail";
  detailEl.textContent = detail;
  if (extra) {
    const extraEl = document.createElement("span");
    extraEl.className = "entry-extra";
    extraEl.textContent = extra;
    detailEl.appendChild(extraEl);
  }
  entry.appendChild(detailEl);
  return entry;
}

// The workout text under an entry.
export function addNotes(entry, notes) {
  if (!notes) return;
  const el = document.createElement("span");
  el.className = "entry-notes";
  el.textContent = notes;
  entry.appendChild(el);
}

// Click or Enter opens it (the session dialog). Its title is what a screen reader says.
export function makeEditable(entry, open) {
  entry.tabIndex = 0;
  entry.setAttribute("role", "button");
  entry.setAttribute("aria-label", entry.title);
  entry.addEventListener("click", open);
  entry.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      open();
    }
  });
}

// The + of a day. Its label names the day, so Claude in Chrome finds the right one.
export function addButton(date, onClick) {
  const add = document.createElement("button");
  add.className = "add-btn";
  add.type = "button";
  add.title = t("Plan a session");
  add.setAttribute("aria-label", t("Plan a session on {date} ({iso})", { date: fmtLongDate(date), iso: date }));
  add.addEventListener("click", onClick);
  return add;
}
