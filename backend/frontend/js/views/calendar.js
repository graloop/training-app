// Calendar tab: Monday–Sunday weeks with what was done (Garmin) next to what
// was planned, sleep badges per day and a summary column per week.

import { api } from "../core/api.js";
import { addDays, fmtDate, fmtHM, fmtHours, fmtLongDate, isoDate, plural, thisMonday } from "../core/format.js";
import { locale, t } from "../core/i18n.js";
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

// "good", "fair" or "poor": the colour of a sleep score (also on the landing page).
export function sleepLevel(score) {
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
  const hours = sleep.total_sleep_seconds
    ? t(" · {time} of sleep", { time: fmtHours(sleep.total_sleep_seconds / 60) })
    : "";
  badge.title = t("Sleep score {score}", { score }) + hours;
  badge.setAttribute("aria-label", badge.title);
  return badge;
}

function makeRestingHrBadge(sleep) {
  if (!sleep || sleep.resting_heart_rate == null) return null;
  const bpm = Math.round(sleep.resting_heart_rate);
  const badge = document.createElement("span");
  badge.className = "rhr-badge";
  badge.textContent = `❤️ ${bpm}`;
  badge.title = t("Resting heart rate {bpm} bpm", { bpm });
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
  title.textContent = t("Week of {date}", {
    date: new Date(week[0].date + "T00:00:00").toLocaleDateString(locale(), { month: "short", day: "numeric" }),
  });
  cell.appendChild(title);

  for (const [label, minutes, cls] of [
    ["Planned", plannedMin, "planned"],
    ["Done", doneMin, "done"],
  ]) {
    const row = document.createElement("div");
    row.className = `week-row ${cls}`;
    const name = document.createElement("span");
    name.textContent = t(label);
    const value = document.createElement("strong");
    value.textContent = fmtHours(minutes);
    row.append(name, value);
    cell.appendChild(row);
  }

  const loadRow = document.createElement("div");
  loadRow.className = "week-row load";
  const loadName = document.createElement("span");
  loadName.textContent = t("Load");
  const loadValue = document.createElement("strong");
  loadValue.textContent = loadCount ? String(Math.round(load)) : "—";
  loadRow.title = loadCount
    ? t("Total Garmin training load from {activities}", { activities: plural(loadCount, "activity", "activities") })
    : t("No training load this week");
  loadRow.append(loadName, loadValue);
  cell.appendChild(loadRow);

  const sleepRow = document.createElement("div");
  sleepRow.className = "week-row sleep";
  const sleepName = document.createElement("span");
  sleepName.textContent = t("Sleep avg");
  const sleepValue = document.createElement("strong");
  if (sleepScores.length) {
    const avg = Math.round(sleepScores.reduce((a, b) => a + b, 0) / sleepScores.length);
    sleepValue.textContent = `😴 ${avg}`;
    sleepValue.className = sleepLevel(avg);
    sleepRow.title = t("Average sleep score over {nights}", { nights: plural(sleepScores.length, "night") });
  } else {
    sleepValue.textContent = "—";
    sleepRow.title = t("No sleep data this week");
  }
  sleepRow.append(sleepName, sleepValue);
  cell.appendChild(sleepRow);

  const rhrRow = document.createElement("div");
  rhrRow.className = "week-row rhr";
  const rhrName = document.createElement("span");
  rhrName.textContent = t("Resting HR");
  const rhrValue = document.createElement("strong");
  if (restingHrs.length) {
    const avg = Math.round(restingHrs.reduce((a, b) => a + b, 0) / restingHrs.length);
    rhrValue.textContent = `❤️ ${avg}`;
    rhrRow.title = t("Average resting heart rate over {days}: {bpm} bpm", {
      days: plural(restingHrs.length, "day"),
      bpm: avg,
    });
  } else {
    rhrValue.textContent = "—";
    rhrRow.title = t("No resting heart rate this week");
  }
  rhrRow.append(rhrName, rhrValue);
  cell.appendChild(rhrRow);

  if (plannedMin > 0) {
    const pct = Math.round((doneMin / plannedMin) * 100);
    const bar = document.createElement("div");
    bar.className = "week-bar";
    bar.title = t("{pct}% of planned time done", { pct });
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
  const effort = a.perceived_effort ? t(", effort {effort}/10", { effort: a.perceived_effort }) : "";
  const felt =
    a.feel != null ? t(", felt {feel}", { feel: FEEL_WORDS[a.feel] ? t(FEEL_WORDS[a.feel]) : `${a.feel}/100` }) : "";
  return effort + felt;
}

function makeActivityEntry(className, a) {
  const detail = a.duration_seconds ? fmtHours(a.duration_seconds / 60) : "";
  const load = a.training_load != null ? Math.round(a.training_load) : null;
  const entry = makeEntry(className, a.activity_type, detail, load != null ? t(" · load {load}", { load }) : "");
  const te =
    a.aerobic_training_effect != null ? t(", aerobic TE {te}", { te: a.aerobic_training_effect.toFixed(1) }) : "";
  entry.title =
    t("Done (Garmin): {name}, {time}", { name: a.name || sportLabel(a.activity_type), time: detail }) +
    (load != null ? t(", training load {load}", { load }) : "") +
    te +
    effortText(a) +
    legsText(a, true);
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
    const name = { swim: t("Swim"), bike: t("Bike"), run: t("Run") }[leg.sport] || sportLabel(leg.type);
    parts.push(long ? `${name} ${time}${km}` : `${activityIcon(leg.type)} ${time}`);
  }
  return long ? t(". Legs: {legs}", { legs: parts.join(", ") }) : parts.join(" · ");
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
    head.textContent = t(label);
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
        const load = a.training_load != null ? t(" · load {load}", { load: Math.round(a.training_load) }) : "";
        entry.title =
          `✓ ${asPlanned ? t("Done as planned") : t("Done, other duration")}: ` +
          t("{sport}, planned {planned}, did {did}", {
            sport: sportLabel(p.activity_type),
            planned: fmtHours(p.planned_duration_minutes),
            did: fmtHours(a.duration_seconds / 60),
          }) +
          `${load}${effortText(a)}${p.notes ? t(". Plan: {notes}", { notes: p.notes }) : ""}${t(". Click to edit the plan.")}`;
        addNotes(entry, p.notes);
        makeEditable(entry, day.date, p);
        cell.appendChild(entry);
      }

      for (const a of activities) cell.appendChild(makeActivityEntry("done", a));

      for (const p of planned) {
        const detail = p.planned_duration_minutes ? fmtHours(p.planned_duration_minutes) : "";
        const entry = makeEntry("planned", p.activity_type, detail);
        entry.title =
          t("Planned {sport}, {time}", { sport: sportLabel(p.activity_type), time: detail }) +
          `${p.notes ? `: ${p.notes}` : ""}${t(". Click to edit.")}`;
        addNotes(entry, p.notes);
        makeEditable(entry, day.date, p);
        cell.appendChild(entry);
      }

      const add = document.createElement("button");
      add.className = "add-btn";
      add.type = "button";
      add.title = t("Plan a session");
      add.setAttribute(
        "aria-label",
        t("Plan a session on {date} ({iso})", { date: fmtLongDate(day.date), iso: day.date }),
      );
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
    if (err.status !== 401)
      showStatus(
        document.getElementById("sync-status"),
        t("Couldn't load the calendar: {error}", { error: err.message }),
        true,
      );
    return;
  }
  renderCalendar(data);
  document.getElementById("cal-range").textContent = t("Showing {start} to {end}", {
    start: fmtLongDate(start),
    end: fmtLongDate(end),
  });
  document.getElementById("cal-today").disabled = weekOffset === 0;
}

for (const [id, change] of [
  ["cal-prev", -2],
  ["cal-next", 2],
  ["cal-today", null],
]) {
  document.getElementById(id).addEventListener("click", () => {
    weekOffset = change === null ? 0 : weekOffset + change;
    loadCalendar();
  });
}
