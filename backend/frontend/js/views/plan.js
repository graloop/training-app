// Plan tab: the draft as a calendar, with the same look as the Calendar tab
// (grid pieces in core/week-grid.js). The + of a day adds a session, clicking
// a session edits, moves or deletes it (the session dialog, saving to the
// draft); Clear empties the draft; Push puts it on the main calendar. The
// draft empties on its own 24 h after its last change: the page says when.
// The draft stays text on the server (one line per session, which Claude's
// connector writes too): each change rewrites one line, found by its number,
// and the answer is the whole draft parsed again, which this file redraws.
// It never parses a line itself (backend/app/planning/draft_parser.py, drafts.py).
// API: GET/DELETE /api/plan/draft, POST /api/plan/draft/sessions, PATCH/DELETE
// /api/plan/draft/sessions/{line}, POST /api/plan/draft/confirm.
// Markup: #tab-plan. Styles: css/plan.css (and css/calendar.css for the grid).

import { api } from "../core/api.js";
import { addDays, fmtDate, fmtHours, fmtSpan, isoDate, plural } from "../core/format.js";
import { locale, t } from "../core/i18n.js";
import { sportLabel } from "../core/sports.js";
import { showStatus } from "../core/ui.js";
import { addButton, addNotes, dayCell, gridHead, makeEditable, makeEntry } from "../core/week-grid.js";
import { showCalendarAround } from "./calendar.js";
import { openSessionDialog } from "./session-dialog.js";

const grid = document.getElementById("plan-grid");
const draftErrors = document.getElementById("draft-errors");
const clearBtn = document.getElementById("plan-clear");
const confirmBtn = document.getElementById("draft-confirm");
const confirmView = document.getElementById("confirm-view");
const confirmStatus = document.getElementById("confirm-status");

let planDraft = null; // the server's last answer: parsed sessions, errors, weekly totals

const today = () => isoDate(new Date());
const draftUrl = (path = "") => `/api/plan/draft${path}?today=${today()}`;

// The session dialog saves here instead of on the calendar. A session of the
// draft is { line, activity_type, planned_duration_minutes, notes }.
function draftBody(body) {
  return JSON.stringify({
    date: body.date,
    activity_type: body.activity_type,
    minutes: body.planned_duration_minutes,
    details: body.notes,
  });
}
const DRAFT_STORE = {
  add: (body) => api(draftUrl("/sessions"), { method: "POST", body: draftBody(body) }),
  update: (session, body) => api(draftUrl(`/sessions/${session.line}`), { method: "PATCH", body: draftBody(body) }),
  remove: (session) => api(draftUrl(`/sessions/${session.line}`), { method: "DELETE" }),
};

// A 409 means the draft changed elsewhere (another tab, Claude): show the new one.
async function afterSave(draft) {
  if (draft) applyDraft(draft);
}

// --- The grid ---

// The week column: planned time, then each sport's share.
function weekSummary(week) {
  const cell = document.createElement("div");
  cell.className = "week-summary";
  const title = document.createElement("div");
  title.className = "week-title";
  title.textContent = t("Week of {date}", {
    date: new Date(week.start + "T00:00:00").toLocaleDateString(locale(), { month: "short", day: "numeric" }),
  });
  cell.appendChild(title);
  const rows = [["Planned", week.total_minutes, "planned"]];
  for (const [sport, minutes] of Object.entries(week.minutes_by_sport)) {
    if (minutes > 0)
      rows.push([{ swim: "Swim", bike: "Bike", run: "Run", strength: "Strength" }[sport] || "Other", minutes, ""]);
  }
  for (const [label, minutes, cls] of rows) {
    const row = document.createElement("div");
    row.className = `week-row ${cls}`;
    const name = document.createElement("span");
    name.textContent = t(label);
    const value = document.createElement("strong");
    value.textContent = fmtHours(minutes);
    row.append(name, value);
    cell.appendChild(row);
  }
  return cell;
}

function renderGrid(draft) {
  grid.innerHTML = "";
  gridHead(grid);
  const now = today();
  for (const week of draft.weeks_preview) {
    for (let i = 0; i < 7; i++) {
      const date = isoDate(addDays(new Date(week.start + "T00:00:00"), i));
      const { cell } = dayCell(date, now);
      for (const s of week.sessions.filter((x) => x.date === date)) {
        const time = fmtHours(s.minutes);
        const entry = makeEntry("planned", s.activity_type, time);
        entry.title =
          t("Draft {sport}, {time}", { sport: sportLabel(s.activity_type), time }) +
          `${s.details ? `: ${s.details}` : ""}${t(". Click to edit.")}`;
        addNotes(entry, s.details);
        const session = {
          line: s.line,
          activity_type: s.activity_type,
          planned_duration_minutes: s.minutes,
          notes: s.details,
        };
        makeEditable(entry, () => openSessionDialog(date, session, afterSave, DRAFT_STORE));
        cell.appendChild(entry);
      }
      if (date >= now) cell.appendChild(addButton(date, () => openSessionDialog(date, null, afterSave, DRAFT_STORE)));
      grid.appendChild(cell);
    }
    grid.appendChild(weekSummary(week));
  }
}

// --- Lines the server couldn't read: no box to fix them in, so each can be deleted ---

function renderDraftErrors(errors) {
  draftErrors.innerHTML = "";
  draftErrors.classList.toggle("hidden", errors.length === 0);
  if (!errors.length) return;
  const title = document.createElement("strong");
  title.textContent = t("{lines} to fix before this can go on the calendar:", { lines: plural(errors.length, "line") });
  const list = document.createElement("ul");
  for (const e of errors) {
    const item = document.createElement("li");
    const where = document.createElement("span");
    where.className = "error-line";
    where.textContent = t("Line {n}: {text}", { n: e.line, text: e.text });
    const why = document.createElement("span");
    why.textContent = e.message;
    const del = document.createElement("button");
    del.type = "button";
    del.className = "btn-link danger";
    del.textContent = t("Delete");
    del.setAttribute("aria-label", t("Delete line {n}", { n: e.line }));
    del.addEventListener("click", async () => {
      try {
        applyDraft(await api(draftUrl(`/sessions/${e.line}`), { method: "DELETE" }));
      } catch (err) {
        if (err.status === 409) loadPlanPage();
        else if (err.status !== 401) showStatus(confirmStatus, err.message, true);
      }
    });
    item.append(where, why, del);
    list.appendChild(item);
  }
  draftErrors.append(title, list);
}

// --- Clear and Push: two clicks when something would be lost, instead of a confirm() popup ---

function resetButtons() {
  clearBtn.classList.remove("confirming");
  clearBtn.textContent = t("Clear");
  confirmBtn.classList.remove("confirming");
  confirmBtn.textContent = t("Push on main calendar");
}

function armed(button, text) {
  if (button.classList.contains("confirming")) return true;
  resetButtons();
  button.classList.add("confirming");
  button.textContent = text;
  setTimeout(resetButtons, 5000);
  return false;
}

function renderSummary(draft) {
  const summary = document.getElementById("confirm-summary");
  resetButtons();
  confirmBtn.disabled = !(draft.sessions_count > 0 && draft.errors.length === 0);
  clearBtn.disabled = !draft.text.trim();
  if (draft.errors.length) {
    summary.textContent = t("Fix or delete the {lines} listed below first.", {
      lines: plural(draft.errors.length, "line"),
    });
  } else if (!draft.sessions_count) {
    summary.textContent = t("Nothing to put on the calendar yet.");
  } else {
    const replaced = draft.existing_planned
      ? t("{sessions} already planned from {from} to {to} will be replaced.", {
          sessions: plural(draft.existing_planned, "session"),
          from: fmtDate(draft.replace_from),
          to: fmtDate(draft.end_date),
        })
      : t("Nothing is planned on those dates yet.");
    summary.textContent =
      `${plural(draft.sessions_count, "session")}, ${fmtSpan(draft.start_date, draft.end_date)}. ${replaced} ` +
      t("Activities you already did are never touched.");
  }
}

// A draft empties 24 h after its last change (backend/app/planning/drafts.py, expire_if_old).
function renderExpiry(draft) {
  const el = document.getElementById("plan-expiry");
  el.classList.toggle("hidden", !draft.expires_at);
  if (!draft.expires_at) return;
  const when = new Date(draft.expires_at).toLocaleString(locale(), {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
  el.textContent = t("This draft will be cleared on {date}, unless it changes before.", { date: when });
}

function applyDraft(draft) {
  planDraft = draft;
  renderExpiry(draft);
  document.getElementById("plan-range").textContent = fmtSpan(draft.start_date, draft.end_date);
  renderDraftErrors(draft.errors);
  renderGrid(draft);
  renderSummary(draft);
}

export async function loadPlanPage() {
  confirmStatus.classList.add("hidden");
  confirmView.classList.add("hidden");
  try {
    applyDraft(await api(draftUrl()));
  } catch (err) {
    if (err.status !== 401) showStatus(confirmStatus, err.message, true);
  }
}

clearBtn.addEventListener("click", async () => {
  if (!armed(clearBtn, t("Click again to clear the draft"))) return;
  try {
    applyDraft(await api(draftUrl(), { method: "DELETE" }));
    confirmView.classList.add("hidden");
    confirmStatus.classList.add("hidden");
  } catch (err) {
    if (err.status !== 401) showStatus(confirmStatus, err.message, true);
  }
});

confirmBtn.addEventListener("click", async () => {
  if (!planDraft || planDraft.errors.length || !planDraft.sessions_count) return;
  if (
    planDraft.existing_planned > 0 &&
    !armed(
      confirmBtn,
      t("Click again: replaces {sessions}", { sessions: plural(planDraft.existing_planned, "planned session") }),
    )
  )
    return;
  confirmBtn.disabled = true;
  try {
    const result = await api(draftUrl("/confirm"), { method: "POST" });
    const replaced = result.replaced
      ? t(", replacing {sessions}", { sessions: plural(result.replaced, "planned session") })
      : "";
    applyDraft(await api(draftUrl()));
    showStatus(
      confirmStatus,
      t("Done: {sessions} on the calendar{replaced}.", { sessions: plural(result.added, "session"), replaced }),
    );
    confirmStatus.classList.add("ok");
    confirmView.classList.remove("hidden");
  } catch (err) {
    if (err.status !== 401) showStatus(confirmStatus, err.message, true);
    confirmBtn.disabled = false;
  }
});

confirmView.addEventListener("click", () => showCalendarAround(planDraft.start_date));
