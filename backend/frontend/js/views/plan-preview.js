// The weekly tables of a plan: one table per week (day, sessions, time) and a
// "Weekly split" by sport. Used by the Plan tab (the draft) and the Import tab
// (what would be imported). `draft` is the server's answer: sessions_count,
// errors and weeks_preview.

import { addDays, fmtDate, fmtHM, fmtSpan, isoDate } from "../core/format.js";
import { activityIcon, sportLabel } from "../core/sports.js";

export function renderDraftPreview(draft, container, emptyText = null) {
  container.innerHTML = "";
  if (!draft.sessions_count) {
    const empty = document.createElement("p");
    empty.className = "plan-empty";
    empty.textContent =
      emptyText ||
      (draft.errors.length
        ? "None of the draft's lines can be read yet."
        : "No plan for these weeks yet. Ask Claude in Chrome to write one, or type the sessions in the draft below.");
    container.appendChild(empty);
    return;
  }
  const today = isoDate(new Date());

  draft.weeks_preview.forEach((week, index) => {
    const heading = document.createElement("h3");
    heading.className = "plan-week-title";
    heading.textContent = `Week ${index + 1} · ${fmtSpan(week.start, week.end)} · ${fmtHM(week.total_minutes)}`;
    const table = document.createElement("table");
    table.className = "users-table plan-table";
    const head = table.createTHead().insertRow();
    for (const name of ["Day", "Sessions", "Time"]) {
      const th = document.createElement("th");
      th.scope = "col";
      th.textContent = name;
      head.appendChild(th);
    }
    const body = table.createTBody();
    for (let i = 0; i < 7; i++) {
      const iso = isoDate(addDays(new Date(week.start + "T00:00:00"), i));
      const sessions = week.sessions.filter((s) => s.date === iso);
      const row = body.insertRow();
      const dayCell = row.insertCell();
      dayCell.textContent = fmtDate(iso);
      dayCell.className = "plan-day";
      const sessionsCell = row.insertCell();
      if (!sessions.length) {
        sessionsCell.textContent = iso < today ? "(already past)" : "–";
        sessionsCell.className = "subtle";
      }
      for (const s of sessions) {
        const line = document.createElement("div");
        line.className = "plan-session";
        const name = document.createElement("strong");
        name.textContent = `${activityIcon(s.activity_type)} ${sportLabel(s.activity_type)} ${fmtHM(s.minutes)}`;
        line.appendChild(name);
        if (s.details) line.appendChild(document.createTextNode(` · ${s.details}`));
        sessionsCell.appendChild(line);
      }
      const timeCell = row.insertCell();
      timeCell.className = "plan-time";
      timeCell.textContent = sessions.length ? fmtHM(sessions.reduce((sum, s) => sum + s.minutes, 0)) : "";
    }
    container.append(heading, table);
  });

  // Weekly split, like a block summary. Strength/Other only appear when used.
  const sports = [
    ["swim", "Swim"],
    ["bike", "Bike"],
    ["run", "Run"],
    ["strength", "Strength"],
    ["other", "Other"],
  ].filter(([key], i) => i < 3 || draft.weeks_preview.some((w) => w.minutes_by_sport[key] > 0));
  const heading = document.createElement("h3");
  heading.className = "plan-week-title";
  heading.textContent = "Weekly split";
  const table = document.createElement("table");
  table.className = "users-table plan-table split-table";
  const head = table.createTHead().insertRow();
  for (const name of ["Week", ...sports.map(([, label]) => label), "Total"]) {
    const th = document.createElement("th");
    th.scope = "col";
    th.textContent = name;
    head.appendChild(th);
  }
  const body = table.createTBody();
  draft.weeks_preview.forEach((week, index) => {
    const row = body.insertRow();
    row.insertCell().textContent = `${index + 1} · ${fmtDate(week.start)}`;
    for (const [key] of sports) row.insertCell().textContent = fmtHM(week.minutes_by_sport[key]);
    const total = row.insertCell();
    total.textContent = fmtHM(week.total_minutes);
    total.className = "plan-time";
  });
  container.append(heading, table);
}
