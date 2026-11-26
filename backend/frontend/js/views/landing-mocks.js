// The landing page's mock-ups: small copies of the app with made-up data, drawn
// with the app's own styles: the Calendar, Plan and Athlete tabs, a chat with
// Claude through the connector, and a coach's athlete list. Each plays a short
// animation the first time it is scrolled to, or shows its end state at once
// when the visitor prefers less motion.

import {
  addDays,
  fmtDate,
  fmtHM,
  fmtHours,
  fmtLongDate,
  fmtMoment,
  isoDate,
  plural,
  thisMonday,
} from "../core/format.js";
import { locale, t } from "../core/i18n.js";
import { activityIcon } from "../core/sports.js";
import { sleepLevel, statIcon } from "./calendar.js";

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

function entry(className, sport, minutes, notes) {
  const node = el("div", `entry ${className}`);
  node.append(el("span", "entry-icon", activityIcon(sport)), el("span", "entry-detail", fmtHours(minutes)));
  if (notes) node.appendChild(el("span", "entry-notes", t(notes)));
  return node;
}

// A day of a mock week: its date, and the sleep score and resting heart rate when given.
function mockDay(date, sleep = null, rhr = null) {
  const cell = el("div", "mock-day");
  const head = el("div", "mock-day-head");
  head.appendChild(el("span", "day-date", fmtDate(isoDate(date))));
  const stats = el("div", "mock-day-stats");
  if (sleep) {
    const badge = el("span", `sleep-badge ${sleepLevel(sleep)}`, String(sleep));
    badge.prepend(statIcon("sleep"));
    stats.appendChild(badge);
  }
  if (rhr) {
    const badge = el("span", "rhr-badge", String(rhr));
    badge.prepend(statIcon("heart"));
    stats.appendChild(badge);
  }
  if (stats.children.length) head.appendChild(stats);
  cell.appendChild(head);
  return cell;
}

// Bars of minutes per sport (Plan mock-up), filled in as sessions are added.
const SPLIT = [
  ["swim", "Swim", "swimming"],
  ["bike", "Bike", "cycling"],
  ["run", "Run", "running"],
];

function splitBars() {
  const root = el("div", "mock-split");
  const rows = {};
  for (const [key, label] of SPLIT) {
    const row = el("div", `mock-split-row ${key}`);
    const track = el("div", "mock-split-track");
    const fill = el("div", "mock-split-fill");
    const value = el("strong", "", fmtHM(0));
    track.appendChild(fill);
    row.append(el("span", "", t(label)), track, value);
    root.appendChild(row);
    rows[key] = { fill, value };
  }
  const total = el("div", "mock-split-total");
  root.appendChild(total);
  return {
    root,
    show(minutesBySport, scale) {
      for (const [key] of SPLIT) {
        rows[key].fill.style.width = `${Math.min(100, (minutesBySport[key] / scale) * 100)}%`;
        rows[key].value.textContent = fmtHM(minutesBySport[key]);
      }
      const sum = Object.values(minutesBySport).reduce((a, b) => a + b, 0);
      total.textContent = `${t("Total")} ${fmtHM(sum)}`;
    },
  };
}

// --- Calendar: this week, its planned sessions turning green as Garmin records them ---

const WEEK = [
  { sleep: 84, rhr: 46, sessions: [{ sport: "strength_training", planned: 40, done: 40 }] },
  { sleep: 79, rhr: 47, sessions: [{ sport: "running", planned: 60, done: 62, notes: "Easy, Z2" }] },
  { sleep: 86, rhr: 45, sessions: [{ sport: "swimming", planned: 45, done: 46, notes: "8×100 m steady" }] },
  { sleep: 63, rhr: 51, sessions: [{ sport: "cycling", planned: 90, done: 65, notes: "3×10 min tempo" }] },
  { sleep: 81, rhr: 48, sessions: [] },
  { sleep: 77, rhr: 47, sessions: [{ sport: "cycling", planned: 180, done: null, notes: "Long ride, Z2" }] },
  { sleep: null, rhr: null, sessions: [{ sport: "running", planned: 105, done: null, notes: "Long run" }] },
];
const AS_PLANNED = 0.15; // within 15% of the planned time, like the real calendar

async function playCalendar(root, animate) {
  const monday = thisMonday();
  const grid = el("div", "mock-week");
  const toRecord = []; // [entry, session] in day order
  WEEK.forEach((day, i) => {
    const cell = mockDay(addDays(monday, i), day.sleep, day.rhr);
    for (const session of day.sessions) {
      const node = entry("planned", session.sport, session.planned, session.notes);
      cell.appendChild(node);
      if (session.done) toRecord.push([node, session]);
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
  for (const [node, session] of toRecord) {
    if (animate) await wait(550);
    const asPlanned = Math.abs(session.done - session.planned) / session.planned <= AS_PLANNED;
    node.className = `entry ${asPlanned ? "completed" : "completed-off"}`;
    node.querySelector(".entry-detail").textContent = `✓ ${fmtHours(session.done)}`;
    done += session.done;
    doneValue.textContent = fmtHours(done);
    fill.style.width = `${Math.round((done / plannedTotal) * 100)}%`;
  }
}

// --- Plan: next week's draft fills in, Claude's banner, then Push ---

const DRAFT = [
  [0, "swimming", 60, "6×200 m at CSS, 30 s rest"],
  [1, "running", 70, "5×1 km at threshold"],
  [3, "cycling", 90, "2×20 min sweet spot"],
  [4, "strength_training", 40, "Core and hips"],
  [5, "cycling", 180, "Long ride, Z2"],
  [6, "running", 105, "Long run, last 20 min at race pace"],
];

async function playPlan(root, animate) {
  const monday = addDays(thisMonday(), 7);
  const banner = el("div", "mock-claude", t("Claude is writing the plan…"));
  const grid = el("div", "mock-week mock-week-plan");
  const days = [];
  for (let i = 0; i < 7; i++) {
    const cell = mockDay(addDays(monday, i));
    days.push(cell);
    grid.appendChild(cell);
  }
  const split = splitBars();
  const push = el("span", "mock-push", t("Push on main calendar"));
  const status = el("span", "mock-push-status");
  const footer = el("div", "mock-plan-footer");
  footer.append(push, status);
  root.replaceChildren(banner, grid, split.root, footer);

  const minutes = { swim: 0, bike: 0, run: 0 };
  const scale = 300;
  split.show(minutes, scale);
  const entries = [];
  for (const [day, sport, length, details] of DRAFT) {
    if (animate) await wait(380);
    const node = entry("planned mock-pop", sport, length, details);
    days[day].appendChild(node);
    entries.push(node);
    const key = SPLIT.find(([, , type]) => type === sport);
    if (key) minutes[key[0]] += length;
    split.show(minutes, scale);
  }
  banner.textContent = t("Proposed by Claude · {sessions}", { sessions: plural(DRAFT.length, "session") });
  banner.classList.add("done");
  if (animate) await wait(900);
  push.classList.add("pressed");
  if (animate) await wait(350);
  for (const node of entries) node.classList.add("pushed");
  status.textContent = t("Done: {sessions} on the calendar{replaced}.", {
    sessions: plural(DRAFT.length, "session"),
    replaced: "",
  });
}

// --- Athlete: objectives counting down, the athlete's numbers and rule set ---

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
const RULES = [
  "Weekdays: 1 h 15 at most, before 7:30",
  "No running the day after a long ride",
  "Exams Dec 8 to 19: lighter weeks",
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
  const rules = el("div", "mock-rules");
  rules.appendChild(el("span", "mock-rules-title", t("Rule set")));
  for (const rule of RULES) rules.appendChild(el("span", "mock-rule", t(rule)));
  root.replaceChildren(list, numbers, rules);
  await Promise.all(counters.map(([node, target]) => countUp(node, target, String, animate)));
}

// --- Claude: a chat in claude.ai, its tool calls to the app, then its answer ---

const TOOLS = [
  ["get_overview", "Reading your objectives, rules and last two weeks"],
  ["get_training_log", "Reading 6 weeks of sessions, sleep and recovery"],
  ["save_draft", "Saving 3 weeks in your Plan tab"],
];
const WEEKS = [390, 435, 300];

async function playChat(root, animate) {
  const thread = el("div", "mock-chat");
  root.replaceChildren(thread);
  thread.appendChild(el("div", "chat-bubble user", t("Plan my next 3 weeks for the half marathon.")));
  for (const [name, label] of TOOLS) {
    if (animate) await wait(650);
    const tool = el("div", "chat-tool");
    tool.append(el("code", "", name), el("span", "", t(label)));
    thread.appendChild(tool);
    if (animate) await wait(450);
    tool.classList.add("done");
  }
  if (animate) await wait(500);
  const reply = el("div", "chat-bubble claude");
  reply.appendChild(el("p", "", t("Done, it's in your Plan tab, not on your calendar yet:")));
  const weeks = el("ul");
  WEEKS.forEach((minutes, i) =>
    weeks.appendChild(el("li", "", t("Week {n} · {hours}", { n: i + 1, hours: fmtHours(minutes) }))),
  );
  reply.append(
    weeks,
    el(
      "p",
      "",
      t(
        "Week 3 is lighter for your exams, and Tuesday's run moved to Wednesday: no running the day after a long ride.",
      ),
    ),
  );
  thread.appendChild(reply);
}

// --- Coaches: the athlete list, and one draft Claude prepared being sent ---

const ATHLETES = [
  {
    name: "ana",
    objective: "Montreal half marathon",
    done: 250,
    planned: 300,
    garmin: "ok",
    shared: true,
    draft: "claude",
    sessions: 14,
  },
  {
    name: "marc",
    objective: "Ironman Lake Placid",
    done: 450,
    planned: 540,
    garmin: "stale",
    shared: false,
    draft: "sent",
    sessions: 18,
  },
  { name: "lea", objective: null, done: 120, planned: 180, garmin: "ok", shared: true, draft: null, sessions: 0 },
];

function draftText(state, sessions, when) {
  if (!state) return t("No draft");
  const count = plural(sessions, "session");
  return state === "sent"
    ? t("Sent {when} · {sessions}", { when, sessions: count })
    : t("Proposed by Claude · {when} · {sessions}", { when, sessions: count });
}

async function playCoach(root, animate) {
  const morning = new Date();
  morning.setHours(7, 12, 0, 0);
  const yesterday = addDays(morning, -1);
  const synced = (days) => addDays(new Date(), -days).toLocaleDateString(locale(), { month: "short", day: "numeric" });
  const list = el("div", "mock-athletes");
  root.replaceChildren(el("div", "mock-title", t("My athletes")), list);
  let anaDraft = null;
  for (const a of ATHLETES) {
    if (animate) await wait(250);
    const row = el("div", "mock-athlete mock-pop");
    const person = el("div", "mock-person");
    const text = el("div", "mock-person-text");
    const detail = el("span", "subtle");
    detail.append(
      [
        a.objective ? t(a.objective) : t("no objective"),
        t("this week {done} of {planned}", { done: fmtHours(a.done), planned: fmtHours(a.planned) }),
        "",
      ].join(" · "),
    );
    const garmin =
      a.garmin === "stale"
        ? el("span", "warn", t("{synced}: stale data", { synced: t("synced {date}", { date: synced(5) }) }))
        : el("span", "", t("synced {date}", { date: synced(0) }));
    detail.appendChild(garmin);
    if (a.shared) detail.append(" · ", el("span", "shared-badge", t("recovery shared")));
    text.append(el("strong", "", a.name), detail);
    person.append(el("span", "mock-avatar", a.name[0].toUpperCase()), text);
    const when = fmtMoment((a.draft === "sent" ? yesterday : morning).toISOString());
    const draft = el(
      "span",
      `mock-draft-state${{ claude: " new", sent: " was-sent" }[a.draft] || ""}`,
      draftText(a.draft, a.sessions, when),
    );
    row.append(person, draft);
    list.appendChild(row);
    if (a.draft === "claude") anaDraft = [draft, a];
  }
  if (animate) await wait(1300);
  const [draft, a] = anaDraft;
  draft.classList.remove("new");
  draft.classList.add("sent");
  draft.textContent = draftText("sent", a.sessions, fmtMoment(new Date().toISOString()));
}

const MOCKS = { calendar: playCalendar, plan: playPlan, athlete: playAthlete, chat: playChat, coach: playCoach };

// Called by landing.js when a mock-up scrolls into view. `root` is an element
// of index.html with data-mock="calendar" | "plan" | "athlete" | "chat" | "coach":
// its content is replaced. `animate: false` (reduced motion) draws the end state at once.
// A new mock-up = a play function above + its name in MOCKS + a data-mock element.
export function startMock(root, { animate }) {
  MOCKS[root.dataset.mock](root, animate);
}
