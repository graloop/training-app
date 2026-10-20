// Plan tab, top to bottom: the preview of the draft and the button that puts
// it on the calendar, the draft itself (one session per line), the rule set.
// The server parses the draft (backend/app/planning.py) and sends back the
// sessions, the lines with a problem and the weekly totals.

import { api } from "../core/api.js";
import { addDays, fmtDate, fmtLongDate, fmtSpan, isoDate, plural, thisMonday } from "../core/format.js";
import { locale, t } from "../core/i18n.js";
import { showStatus } from "../core/ui.js";
import { showCalendarAround } from "./calendar.js";
import { renderDraftPreview } from "./plan-preview.js";

const rulesText = document.getElementById("rules-text");
const rulesStatus = document.getElementById("rules-status");
const planStart = document.getElementById("plan-start");
const planWeeks = document.getElementById("plan-weeks");
const draftText = document.getElementById("draft-text");
const draftStatus = document.getElementById("draft-status");
const draftErrors = document.getElementById("draft-errors");
const draftPreview = document.getElementById("draft-preview");
const confirmBtn = document.getElementById("draft-confirm");
const confirmView = document.getElementById("confirm-view");
const confirmStatus = document.getElementById("confirm-status");

let planDraft = null; // the server's last answer: parsed sessions, errors, weekly totals
let draftDirty = false; // typed since the last save

// Every Monday from this week to about six months out, so any future week can be planned.
function fillPlanStart(selectedIso) {
  const monday = thisMonday();
  const options = [];
  for (let i = 0; i < 27; i++) {
    const day = addDays(monday, i * 7);
    let label = day.toLocaleDateString(locale(), {
      weekday: "short",
      month: "short",
      day: "numeric",
      ...(day.getFullYear() !== monday.getFullYear() ? { year: "numeric" } : {}),
    });
    if (i === 0) label = t("This week ({date})", { date: label });
    if (i === 1) label = t("Next week ({date})", { date: label });
    options.push([isoDate(day), label]);
  }
  if (selectedIso && !options.some(([iso]) => iso === selectedIso))
    options.unshift([selectedIso, fmtLongDate(selectedIso)]);
  planStart.innerHTML = "";
  for (const [iso, label] of options) {
    const opt = document.createElement("option");
    opt.value = iso;
    opt.textContent = label;
    planStart.appendChild(opt);
  }
  if (selectedIso) planStart.value = selectedIso;
}

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
    item.append(where, why);
    list.appendChild(item);
  }
  draftErrors.append(title, list);
}

function resetConfirmButton() {
  confirmBtn.classList.remove("confirming");
  confirmBtn.textContent = t("Put this plan on the calendar");
}

function renderConfirm(draft) {
  const summary = document.getElementById("confirm-summary");
  resetConfirmButton();
  const ready = draft.sessions_count > 0 && draft.errors.length === 0;
  confirmBtn.disabled = !ready;
  if (draft.errors.length) {
    summary.textContent = t("Fix the {lines} flagged in the draft below first.", {
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

function applyDraft(draft, { fill = false } = {}) {
  planDraft = draft;
  if (fill) {
    fillPlanStart(draft.start_date);
    if (![...planWeeks.options].some((o) => o.value === String(draft.weeks))) {
      const opt = document.createElement("option");
      opt.value = String(draft.weeks);
      opt.textContent = plural(draft.weeks, "week");
      planWeeks.appendChild(opt);
    }
    planWeeks.value = String(draft.weeks);
    draftText.value = draft.text;
    draftDirty = false;
  }
  document.getElementById("plan-range").textContent = fmtSpan(draft.start_date, draft.end_date);
  renderDraftErrors(draft.errors);
  renderDraftPreview(draft, draftPreview);
  renderConfirm(draft);
}

async function saveDraft({ quiet = false } = {}) {
  try {
    const draft = await api(`/api/plan/draft?today=${isoDate(new Date())}`, {
      method: "PUT",
      body: JSON.stringify({
        start_date: planStart.value,
        weeks: Number(planWeeks.value),
        text: draftText.value,
      }),
    });
    draftDirty = false;
    applyDraft(draft);
    confirmView.classList.add("hidden");
    if (!quiet) {
      const problems = draft.errors.length ? t(", {lines} to fix", { lines: plural(draft.errors.length, "line") }) : "";
      showStatus(
        draftStatus,
        t("Saved: {sessions}{problems}. Not on the calendar yet.", {
          sessions: plural(draft.sessions_count, "session"),
          problems,
        }),
      );
      if (!draft.errors.length) draftStatus.classList.add("ok");
    }
    return draft;
  } catch (err) {
    if (err.status !== 401) showStatus(draftStatus, err.message, true);
    return null;
  }
}

export async function loadPlanPage() {
  for (const el of [draftStatus, confirmStatus, rulesStatus]) el.classList.add("hidden");
  confirmView.classList.add("hidden");
  try {
    const [draft, rules] = await Promise.all([
      api(`/api/plan/draft?today=${isoDate(new Date())}`),
      api("/api/plan/rules"),
    ]);
    rulesText.value = rules.rules;
    applyDraft(draft, { fill: true });
  } catch (err) {
    if (err.status !== 401) showStatus(confirmStatus, err.message, true);
  }
}

// --- The draft ---

draftText.addEventListener("input", () => {
  draftDirty = true;
});
// Saving when the box loses focus keeps the preview current without an extra click.
draftText.addEventListener("change", () => saveDraft());
for (const el of [planStart, planWeeks]) el.addEventListener("change", () => saveDraft());
document.getElementById("draft-save").addEventListener("click", async () => {
  const draft = await saveDraft();
  // The preview is above the draft: bring it into view, unless there are lines to fix right here.
  if (draft && !draft.errors.length)
    document.getElementById("plan-panel").scrollIntoView({ behavior: "smooth", block: "start" });
});

// --- Putting it on the calendar ---

// Two clicks when sessions will be replaced, instead of a confirm() dialog.
confirmBtn.addEventListener("click", async () => {
  if (draftDirty && !(await saveDraft({ quiet: true }))) return;
  if (!planDraft || planDraft.errors.length || !planDraft.sessions_count) return;
  if (planDraft.existing_planned > 0 && !confirmBtn.classList.contains("confirming")) {
    confirmBtn.classList.add("confirming");
    confirmBtn.textContent = t("Click again: replaces {sessions}", {
      sessions: plural(planDraft.existing_planned, "planned session"),
    });
    setTimeout(resetConfirmButton, 5000);
    return;
  }
  confirmBtn.disabled = true;
  try {
    const result = await api(`/api/plan/draft/confirm?today=${isoDate(new Date())}`, { method: "POST" });
    const replaced = result.replaced
      ? t(", replacing {sessions}", { sessions: plural(result.replaced, "planned session") })
      : "";
    applyDraft(await api(`/api/plan/draft?today=${isoDate(new Date())}`));
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

// --- The rule set (saved on its own; it shows up in the History tab) ---

async function saveRules() {
  try {
    rulesText.value = (
      await api("/api/plan/rules", { method: "PUT", body: JSON.stringify({ rules: rulesText.value }) })
    ).rules;
    showStatus(rulesStatus, t("Saved. It is now part of the History tab."));
    rulesStatus.classList.add("ok");
  } catch (err) {
    if (err.status !== 401) showStatus(rulesStatus, err.message, true);
  }
}
document.getElementById("rules-save").addEventListener("click", saveRules);
rulesText.addEventListener("change", saveRules);
