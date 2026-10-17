// Calendar tab: Monday–Sunday weeks with what was done (Garmin) next to what
// was planned, sleep badges per day and a summary column per week.

import { api } from "../core/api.js";
import { addDays, fmtDate, fmtHM, fmtHours, fmtLongDate, isoDate, thisMonday } from "../core/format.js";
import { activityIcon, sportLabel } from "../core/sports.js";
import { showTab } from "../core/tabs.js";
import { showStatus } from "../core/ui.js";
import { openSessionDialog } from "./session-dialog.js";

// Whole Monday–Sunday weeks covering roughly 2 weeks back and 2 weeks ahead
// of today, shifted by the Earlier/Later buttons (2 weeks per click).
let weekOffset = 0;

function calendarRange() {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const center = addDays(today, weekOffset * 7);
  const from = addDays(center, -14);
  const to = addDays(center, 14);
  const start = addDays(from, -((from.getDay() + 6) % 7)); // back to Monday
  const end = addDays(to, (7 - to.getDay()) % 7); // forward to Sunday
  return { start: isoDate(start), end: isoDate(end) };
}

// Back to the weeks around today (on login).
export function resetCalendar() {
  weekOffset = 0;
}

// Opens the Calendar tab with the week of `mondayIso` near the top (after a
// plan or an import was put on the calendar).
export function showCalendarAround(mondayIso) {
  const weeksAhead = Math.round((new Date(mondayIso + "T00:00:00") - thisMonday()) / (7 * 86400000));
  weekOffset = Math.max(0, weeksAhead + 2);
  showTab("calendar");
}

// --- Day badges and the week summary column ---

function sleepLevel(score) {
  if (score >= 80) return "good";
  if (score >= 60) return "fair";
  return "poor";
}

function makeSleepBadge(sleep) {
  if (!sleep || sleep.sleep_score == null) return null;
  const score = Math.round(sleep.sleep_score);
  const badge = document.createElement("span");
  badge.className = `sleep-badge ${sleepLevel(score)}`;
  badge.textContent = `😴 ${score}`;
  const hours = sleep.total_sleep_seconds ? ` · ${fmtHours(sleep.total_sleep_seconds / 60)} of sleep` : "";
  badge.title = `Sleep score ${score}${hours}`;
  badge.setAttribute("aria-label", badge.title);
  return badge;
}

function makeRestingHrBadge(sleep) {
  if (!sleep || sleep.resting_heart_rate == null) return null;
  const bpm = Math.round(sleep.resting_heart_rate);
  const badge = document.createElement("span");
  badge.className = "rhr-badge";
  badge.textContent = `❤️ ${bpm}`;
  badge.title = `Resting heart rate ${bpm} bpm`;
  badge.setAttribute("aria-label", badge.title);
  return badge;
}

function renderWeekSummary(week) {
  let plannedMin = 0;
  let doneMin = 0;
  const sleepScores = [];
  const restingHrs = [];
  let load = 0;
  let loadCount = 0;
  for (const day of week) {
    for (const p of day.planned) plannedMin += p.planned_duration_minutes || 0;
    for (const a of day.activities) {
      doneMin += (a.duration_seconds || 0) / 60;
      if (a.training_load != null) {
        load += a.training_load;
        loadCount += 1;
      }
    }
    if (day.sleep && day.sleep.sleep_score != null) sleepScores.push(day.sleep.sleep_score);
    if (day.sleep && day.sleep.resting_heart_rate != null) restingHrs.push(day.sleep.resting_heart_rate);
  }

  const cell = document.createElement("div");
  cell.className = "week-summary";

  const title = document.createElement("div");
  title.className = "week-title";
  title.textContent = `Week of ${new Date(week[0].date + "T00:00:00").toLocaleDateString(undefined, { month: "short", day: "numeric" })}`;
  cell.appendChild(title);

  for (const [label, minutes, cls] of [["Planned", plannedMin, "planned"], ["Done", doneMin, "done"]]) {
    const row = document.createElement("div");
    row.className = `week-row ${cls}`;
    const name = document.createElement("span");
    name.textContent = label;
    const value = document.createElement("strong");
    value.textContent = fmtHours(minutes);
    row.append(name, value);
    cell.appendChild(row);
  }

  const loadRow = document.createElement("div");
  loadRow.className = "week-row load";
  const loadName = document.createElement("span");
  loadName.textContent = "Load";
  const loadValue = document.createElement("strong");
  loadValue.textContent = loadCount ? String(Math.round(load)) : "—";
  loadRow.title = loadCount
    ? `Total Garmin training load from ${loadCount} activit${loadCount > 1 ? "ies" : "y"}`
    : "No training load this week";
  loadRow.append(loadName, loadValue);
  cell.appendChild(loadRow);

  const sleepRow = document.createElement("div");
  sleepRow.className = "week-row sleep";
  const sleepName = document.createElement("span");
  sleepName.textContent = "Sleep avg";
  const sleepValue = document.createElement("strong");
  if (sleepScores.length) {
    const avg = Math.round(sleepScores.reduce((a, b) => a + b, 0) / sleepScores.length);
    sleepValue.textContent = `😴 ${avg}`;
    sleepValue.className = sleepLevel(avg);
    sleepRow.title = `Average sleep score over ${sleepScores.length} night${sleepScores.length > 1 ? "s" : ""}`;
  } else {
    sleepValue.textContent = "—";
    sleepRow.title = "No sleep data this week";
  }
  sleepRow.append(sleepName, sleepValue);
  cell.appendChild(sleepRow);

  const rhrRow = document.createElement("div");
  rhrRow.className = "week-row rhr";
  const rhrName = document.createElement("span");
  rhrName.textContent = "Resting HR";
  const rhrValue = document.createElement("strong");
  if (restingHrs.length) {
    const avg = Math.round(restingHrs.reduce((a, b) => a + b, 0) / restingHrs.length);
    rhrValue.textContent = `❤️ ${avg}`;
    rhrRow.title = `Average resting heart rate over ${restingHrs.length} day${restingHrs.length > 1 ? "s" : ""}: ${avg} bpm`;
  } else {
    rhrValue.textContent = "—";
    rhrRow.title = "No resting heart rate this week";
  }
  rhrRow.append(rhrName, rhrValue);
  cell.appendChild(rhrRow);

  if (plannedMin > 0) {
    const pct = Math.round((doneMin / plannedMin) * 100);
    const bar = document.createElement("div");
    bar.className = "week-bar";
    bar.title = `${pct}% of planned time done`;
    const fill = document.createElement("div");
    fill.style.width = `${Math.min(pct, 100)}%`;
    bar.appendChild(fill);
    cell.appendChild(bar);
  }
  return cell;
}

// --- Planned vs. done ---

// Planned vs. done, same day and same sport: within 15% of the planned time is
// "done as planned"; any other duration is still done, just not as planned.
const MATCH_TOLERANCE = 0.15;

function sportCategory(type) {
  const key = (type || "").toLowerCase();
  if (key.includes("swim")) return "swim";
  if (key.includes("run")) return "run";
  if (["cycl", "bik", "ride", "zwift"].some((n) => key.includes(n))) return "bike";
  if (key.includes("strength")) return "strength";
  return null;
}

// Pairs each planned session with the closest same-sport activity of the day:
// first those within tolerance, then any duration. One activity covers one
// planned session (split sessions are not added up). Returns the pairs, with
// `asPlanned`, plus whatever is left unmatched.
function matchDay(day) {
  const activities = day.activities.filter((a) => a.duration_seconds);
  const unmatchable = day.activities.filter((a) => !a.duration_seconds);
  const pairs = [];
  const left = new Set(day.planned);
  for (const strict of [true, false]) {
    for (const p of day.planned) {
      if (!left.has(p)) continue;
      const category = sportCategory(p.activity_type);
      const plannedMin = p.planned_duration_minutes || 0;
      if (!category || plannedMin <= 0) continue;
      let best = null;
      for (const a of activities) {
        if (sportCategory(a.activity_type) !== category) continue;
        const diff = Math.abs(a.duration_seconds / 60 - plannedMin) / plannedMin;
        if ((!strict || diff <= MATCH_TOLERANCE) && (!best || diff < best.diff)) best = { a, diff };
      }
      if (best) {
        pairs.push({ planned: p, activity: best.a, asPlanned: best.diff <= MATCH_TOLERANCE });
        activities.splice(activities.indexOf(best.a), 1);
        left.delete(p);
      }
    }
  }
  return { pairs, activities: [...activities, ...unmatchable], planned: day.planned.filter((p) => left.has(p)) };
}

// --- One entry (a done activity or a planned session) in a day ---

function makeEntry(className, type, detail, extra) {
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

// What was answered on the watch after the session: ", effort 7/10, felt strong".
const FEEL_WORDS = { 0: "very weak", 25: "weak", 50: "normal", 75: "strong", 100: "very strong" };

function effortText(a) {
  const effort = a.perceived_effort ? `, effort ${a.perceived_effort}/10` : "";
  const felt = a.feel != null ? `, felt ${FEEL_WORDS[a.feel] || `${a.feel}/100`}` : "";
  return effort + felt;
}

function makeActivityEntry(className, a) {
  const detail = a.duration_seconds ? fmtHours(a.duration_seconds / 60) : "";
  const load = a.training_load != null ? Math.round(a.training_load) : null;
  const entry = makeEntry(className, a.activity_type, detail, load != null ? ` · load ${load}` : "");
  const te = a.aerobic_training_effect != null ? `, aerobic TE ${a.aerobic_training_effect.toFixed(1)}` : "";
  entry.title = `Done (Garmin): ${a.name || sportLabel(a.activity_type)}, ${detail}${load != null ? `, training load ${load}` : ""}${te}${effortText(a)}${legsText(a, true)}`;
  entry.setAttribute("role", "note");
  entry.setAttribute("aria-label", entry.title);
  if (a.legs && a.legs.length) addNotes(entry, legsText(a));
  return entry;
}

// A multisport race's legs: "🏊 0:42 · 🚴 2:40 · 🏃 1:45" (transitions in the long form only).
function legsText(a, long = false) {
  if (!a.legs || !a.legs.length) return "";
  const parts = [];
  let transition = 0;
  for (const leg of a.legs) {
    const time = fmtHM((leg.duration_seconds || 0) / 60);
    if (leg.sport === "other" && (leg.type || "").includes("transition")) {
      transition += 1;
      if (long) parts.push(`T${transition} ${time}`);
      continue;
    }
    const km = leg.distance_meters >= 100 ? ` ${(leg.distance_meters / 1000).toFixed(1)} km` : "";
    const name = { swim: "Swim", bike: "Bike", run: "Run" }[leg.sport] || sportLabel(leg.type);
    parts.push(long ? `${name} ${time}${km}` : `${activityIcon(leg.type)} ${time}`);
  }
  return long ? `. Legs: ${parts.join(", ")}` : parts.join(" · ");
}

function addNotes(entry, notes) {
  if (!notes) return;
  const el = document.createElement("span");
  el.className = "entry-notes";
  el.textContent = notes;
  entry.appendChild(el);
}

function makeEditable(entry, date, planned) {
  entry.tabIndex = 0;
  entry.setAttribute("role", "button");
  entry.setAttribute("aria-label", entry.title);
  entry.addEventListener("click", () => openSessionDialog(date, planned, loadCalendar));
  entry.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      openSessionDialog(date, planned, loadCalendar);
    }
  });
}

// --- The grid ---

function renderCalendar(data) {
  const grid = document.getElementById("calendar-grid");
  grid.innerHTML = "";
  const today = isoDate(new Date());

  for (const label of ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun", "Week total"]) {
    const head = document.createElement("div");
    head.className = "grid-head";
    head.textContent = label;
    grid.appendChild(head);
  }

  for (let i = 0; i < data.days.length; i += 7) {
    const week = data.days.slice(i, i + 7);
    for (const day of week) {
      const cell = document.createElement("div");
      cell.className = "day-cell" + (day.date === today ? " today" : "") + (day.date < today ? " past" : "");
      cell.dataset.date = day.date;

      const head = document.createElement("div");
      head.className = "day-head";
      const dateEl = document.createElement("span");
      dateEl.className = "day-date";
      dateEl.textContent = fmtDate(day.date);
      dateEl.title = day.date;
      head.appendChild(dateEl);
      const stats = document.createElement("span");
      stats.className = "day-stats";
      for (const badge of [makeSleepBadge(day.sleep), makeRestingHrBadge(day.sleep)]) {
        if (badge) stats.appendChild(badge);
      }
      if (stats.childElementCount) head.appendChild(stats);
      cell.appendChild(head);

      const { pairs, activities, planned } = matchDay(day);

      for (const { planned: p, activity: a, asPlanned } of pairs) {
        const entry = makeActivityEntry(asPlanned ? "completed" : "completed-off", a);
        const load = a.training_load != null ? ` · load ${Math.round(a.training_load)}` : "";
        entry.title =
          `✓ ${asPlanned ? "Done as planned" : "Done, other duration"}: ${sportLabel(p.activity_type)}, planned ${fmtHours(p.planned_duration_minutes)}, ` +
          `did ${fmtHours(a.duration_seconds / 60)}${load}${effortText(a)}${p.notes ? `. Plan: ${p.notes}` : ""}. Click to edit the plan.`;
        addNotes(entry, p.notes);
        makeEditable(entry, day.date, p);
        cell.appendChild(entry);
      }

      for (const a of activities) cell.appendChild(makeActivityEntry("done", a));

      for (const p of planned) {
        const detail = p.planned_duration_minutes ? fmtHours(p.planned_duration_minutes) : "";
        const entry = makeEntry("planned", p.activity_type, detail);
        entry.title =
          `Planned ${sportLabel(p.activity_type)}, ${detail}` +
          `${p.notes ? `: ${p.notes}` : ""}. Click to edit.`;
        addNotes(entry, p.notes);
        makeEditable(entry, day.date, p);
        cell.appendChild(entry);
      }

      const add = document.createElement("button");
      add.className = "add-btn";
      add.type = "button";
      add.title = "Plan a session";
      add.setAttribute("aria-label", `Plan a session on ${fmtLongDate(day.date)} (${day.date})`);
      add.addEventListener("click", () => openSessionDialog(day.date, null, loadCalendar));
      cell.appendChild(add);

      grid.appendChild(cell);
    }
    grid.appendChild(renderWeekSummary(week));
  }
}

export async function loadCalendar() {
  const { start, end } = calendarRange();
  let data;
  try {
    data = await api(`/api/calendar?start=${start}&end=${end}`);
  } catch (err) {
    if (err.status !== 401) showStatus(document.getElementById("sync-status"), `Couldn't load the calendar: ${err.message}`, true);
    return;
  }
  renderCalendar(data);
  document.getElementById("cal-range").textContent = `Showing ${fmtLongDate(start)} to ${fmtLongDate(end)}`;
  document.getElementById("cal-today").disabled = weekOffset === 0;
}

for (const [id, change] of [["cal-prev", -2], ["cal-next", 2], ["cal-today", null]]) {
  document.getElementById(id).addEventListener("click", () => {
    weekOffset = change === null ? 0 : weekOffset + change;
    loadCalendar();
  });
}
