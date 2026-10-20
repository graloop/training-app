// The landing page's mock-ups: small copies of the Calendar, Plan and Athlete
// tabs with made-up data, drawn with the app's own styles. Each plays a short
// animation the first time it is scrolled to, or shows its end state at once
// when the visitor prefers less motion.

import { addDays, fmtDate, fmtHM, fmtHours, fmtLongDate, isoDate, plural, thisMonday } from "../core/format.js";
import { locale, t } from "../core/i18n.js";
import { activityIcon, sportLabel } from "../core/sports.js";
import { sleepLevel } from "./calendar.js";

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function el(tag, className = "", text = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

// Counts a number up from 0 in `node` (`format` turns it into text).
async function countUp(node, target, format, animate, steps = 24) {
  if (!animate) return (node.textContent = format(target));
  for (let i = 1; i <= steps; i++) {
    node.textContent = format(Math.round((target * i) / steps));
    await wait(30);
  }
}

// --- Calendar: this week, its planned sessions turning green as Garmin records them ---

const WEEK = [
  { sleep: 84, sessions: [{ sport: "strength_training", planned: 40, done: 40 }] },
  { sleep: 79, sessions: [{ sport: "running", planned: 60, done: 62, notes: "Easy, Z2" }] },
  { sleep: 86, sessions: [{ sport: "swimming", planned: 45, done: 46, notes: "8×100 m steady" }] },
  { sleep: 63, sessions: [{ sport: "cycling", planned: 90, done: 65, notes: "3×10 min tempo" }] },
  { sleep: 81, sessions: [] },
  { sleep: 77, sessions: [{ sport: "cycling", planned: 180, done: null, notes: "Long ride, Z2" }] },
  { sleep: null, sessions: [{ sport: "running", planned: 105, done: null, notes: "Long run" }] },
];
const AS_PLANNED = 0.15; // within 15% of the planned time, like the real calendar

function calendarEntry(session) {
  const entry = el("div", "entry planned");
  entry.append(
    el("span", "entry-icon", activityIcon(session.sport)),
    el("span", "entry-detail", fmtHours(session.planned)),
  );
  if (session.notes) entry.appendChild(el("span", "entry-notes", t(session.notes)));
  return entry;
}

async function playCalendar(root, animate) {
  const monday = thisMonday();
  const grid = el("div", "mock-week");
  const toRecord = []; // [entry, session] in day order
  WEEK.forEach((day, i) => {
    const cell = el("div", "mock-day");
    const head = el("div", "mock-day-head");
    head.appendChild(el("span", "day-date", fmtDate(isoDate(addDays(monday, i)))));
    if (day.sleep) head.appendChild(el("span", `sleep-badge ${sleepLevel(day.sleep)}`, `😴 ${day.sleep}`));
    cell.appendChild(head);
    for (const session of day.sessions) {
      const entry = calendarEntry(session);
      cell.appendChild(entry);
      if (session.done) toRecord.push([entry, session]);
    }
    grid.appendChild(cell);
  });

  const plannedTotal = WEEK.flatMap((d) => d.sessions).reduce((sum, s) => sum + s.planned, 0);
  const summary = el("div", "mock-week-summary");
  const doneValue = el("strong", "done-value", fmtHours(0));
  const bar = el("div", "week-bar");
  const fill = el("div");
  bar.appendChild(fill);
  summary.append(
    el("span", "", `${t("Planned")} `),
    el("strong", "planned-value", fmtHours(plannedTotal)),
    el("span", "", ` · ${t("Done")} `),
    doneValue,
    bar,
  );
  const weekOf = monday.toLocaleDateString(locale(), { month: "short", day: "numeric" });
  root.replaceChildren(el("div", "mock-title", t("Week of {date}", { date: weekOf })), grid, summary);

  let done = 0;
  for (const [entry, session] of toRecord) {
    if (animate) await wait(550);
    const asPlanned = Math.abs(session.done - session.planned) / session.planned <= AS_PLANNED;
    entry.className = `entry ${asPlanned ? "completed" : "completed-off"}`;
    entry.querySelector(".entry-detail").textContent = `✓ ${fmtHours(session.done)}`;
    done += session.done;
    doneValue.textContent = fmtHours(done);
    fill.style.width = `${Math.round((done / plannedTotal) * 100)}%`;
  }
}

// --- Plan: Claude types the draft, the preview fills in line by line ---

const DRAFT = [
  ["Mon Oct 26", "Mon", "swimming", "swim", 60, "6×200 m at CSS, 30 s rest"],
  ["Tue Oct 27", "Tue", "running", "run", 70, "5×1 km at threshold"],
  ["Thu Oct 29", "Thu", "cycling", "bike", 90, "2×20 min sweet spot"],
  ["Sat Oct 31", "Sat", "cycling", "bike", 180, "Long ride, Z2"],
  ["Sun Nov 1", "Sun", "running", "run", 105, "Long run, last 20 min at race pace"],
];
const SPLIT = [
  ["swim", "Swim"],
  ["bike", "Bike"],
  ["run", "Run"],
];

async function typeLine(node, text, animate) {
  if (!animate) return (node.textContent = text);
  for (let i = 1; i <= text.length; i++) {
    node.textContent = text.slice(0, i);
    await wait(12);
  }
}

async function playPlan(root, animate) {
  const status = el("div", "mock-claude", t("Claude is writing the plan…"));
  const draft = el("pre", "mock-draft");
  const preview = el("div", "mock-preview");
  const split = el("div", "mock-split");
  root.replaceChildren(status, draft, preview, split);

  const minutesBySport = { swim: 0, bike: 0, run: 0 };
  for (const [date, day, sport, word, minutes, details] of DRAFT) {
    const line = el("span", "mock-draft-line");
    draft.appendChild(line);
    await typeLine(line, `${date} | ${word} | ${fmtHM(minutes)} | ${t(details)}`, animate);

    const row = el("div", "mock-preview-row");
    row.append(
      el("span", "mock-preview-day", t(day)),
      el("strong", "", `${activityIcon(sport)} ${sportLabel(sport)} ${fmtHM(minutes)}`),
      el("span", "mock-preview-details", t(details)),
    );
    preview.appendChild(row);
    minutesBySport[word] += minutes;
  }

  const total = Object.values(minutesBySport).reduce((a, b) => a + b, 0);
  const longest = Math.max(...Object.values(minutesBySport));
  for (const [key, label] of SPLIT) {
    const row = el("div", `mock-split-row ${key}`);
    const track = el("div", "mock-split-track");
    const fill = el("div", "mock-split-fill");
    track.appendChild(fill);
    row.append(el("span", "", t(label)), track, el("strong", "", fmtHM(minutesBySport[key])));
    split.appendChild(row);
    requestAnimationFrame(() => (fill.style.width = `${(minutesBySport[key] / longest) * 100}%`));
  }
  split.appendChild(el("div", "mock-split-total", `${t("Total")} ${fmtHM(total)}`));
  status.textContent = t("Saved: {sessions}{problems}. Not on the calendar yet.", {
    sessions: plural(DRAFT.length, "session"),
    problems: "",
  });
  status.classList.add("done");
}

// --- Athlete: objectives counting down, and the athlete's numbers ---

const OBJECTIVES = [
  { title: "Montreal half marathon", days: 187, target: "1:25:00" },
  { title: "Ironman Lake Placid", days: 278, target: "10:30:00" },
];
const NUMBERS = [
  ["FTP", "265 W"],
  ["Threshold pace", "4:15 /km"],
  ["CSS", "1:38 /100 m"],
  ["Max HR", "192 bpm"],
];

async function playAthlete(root, animate) {
  const list = el("div", "mock-objectives");
  const counters = [];
  for (const objective of OBJECTIVES) {
    const card = el("div", "mock-objective");
    const days = el("strong", "mock-countdown-value", "0");
    const countdown = el("div", "mock-countdown");
    countdown.append(days, el("span", "", t("days to go")));
    const info = el("div", "mock-objective-info");
    info.append(
      el("h4", "", t(objective.title)),
      el("span", "subtle", fmtLongDate(isoDate(addDays(new Date(), objective.days)))),
      el("span", "mock-target", `${t("Target time")} ${objective.target}`),
    );
    card.append(countdown, info);
    list.appendChild(card);
    counters.push([days, objective.days]);
  }
  const numbers = el("div", "mock-numbers");
  for (const [label, value] of NUMBERS) {
    const chip = el("div", "mock-number");
    chip.append(el("span", "", t(label)), el("strong", "", value));
    numbers.appendChild(chip);
  }
  root.replaceChildren(list, numbers);
  await Promise.all(counters.map(([node, target]) => countUp(node, target, String, animate)));
}

const MOCKS = { calendar: playCalendar, plan: playPlan, athlete: playAthlete };

export function startMock(root, { animate }) {
  MOCKS[root.dataset.mock](root, animate);
}
