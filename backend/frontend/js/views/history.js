// History tab: everything Claude in Chrome needs, on one page. The training
// log is written by the server (backend/app/planning.py, build_log); below it,
// how to write the plan and the current draft.

import { api } from "../core/api.js";
import { fmtLongDate, fmtSpan, isoDate, plural } from "../core/format.js";
import { t } from "../core/i18n.js";
import { selectContents, showStatus } from "../core/ui.js";
import { runSync } from "./garmin.js";

const logEl = document.getElementById("training-log");
const logWeeks = document.getElementById("log-weeks");
const logDetail = document.getElementById("log-detail");
const logStatus = document.getElementById("log-status");
const logContext = document.getElementById("log-context");
const importBtn = document.getElementById("import-btn");
const importStatus = document.getElementById("import-status");

async function loadLog() {
  try {
    const data = await api(
      `/api/plan/log?weeks=${logWeeks.value}&detail=${logDetail.value}&today=${isoDate(new Date())}`,
    );
    logEl.textContent = data.text;
    logStatus.classList.add("hidden");
  } catch (err) {
    if (err.status !== 401) showStatus(logStatus, t("Couldn't load the log: {error}", { error: err.message }), true);
  }
}

// The weeks being planned, the line format and the current draft. In English
// whatever the page's language: it is read by Claude with the English log above.
function planContextText(draft) {
  const parts = [
    "HOW TO WRITE THE PLAN",
    `Weeks to plan: ${fmtSpan(draft.start_date, draft.end_date)} (change them on the Plan tab).`,
    "Give one session per line as: date | sport | duration | details",
    'Date like "Tue Oct 13", sport swim / bike / run / strength, duration like 1:15. Two sessions on one day are two lines; rest days need no line.',
    'In the app: write the lines in the Plan tab\'s "Draft sessions" box and press "Save and preview". The athlete puts it on the calendar.',
    "",
    `CURRENT DRAFT (${draft.sessions_count} session${draft.sessions_count === 1 ? "" : "s"}${draft.errors.length ? `, ${draft.errors.length} line(s) with a problem` : ""})`,
    (draft.text || "").trim() || "- empty",
  ];
  return parts.join("\n");
}

export async function loadHistoryPage() {
  for (const el of [logStatus, importStatus]) el.classList.add("hidden");
  try {
    const [draft] = await Promise.all([api(`/api/plan/draft?today=${isoDate(new Date())}`), loadLog()]);
    logContext.textContent = planContextText(draft);
  } catch (err) {
    if (err.status !== 401) showStatus(logStatus, err.message, true);
  }
}

logWeeks.addEventListener("change", loadLog);
logDetail.addEventListener("change", loadLog);
document.getElementById("log-sync").addEventListener("click", () => runSync(logStatus));

// The same text, ready to paste into a normal Claude conversation.
document.getElementById("log-copy").addEventListener("click", async () => {
  const text = `${logEl.textContent}\n\n${logContext.textContent}`;
  try {
    await navigator.clipboard.writeText(text);
    showStatus(logStatus, t("Copied. Paste it into a Claude conversation and ask for the plan."));
    logStatus.classList.add("ok");
  } catch (_) {
    selectContents(logEl);
    showStatus(logStatus, t("Couldn't copy automatically. The log is selected: press Cmd+C."), true);
  }
});

// Older activities come as one list from Garmin: enough for the long-term weekly totals.
importBtn.addEventListener("click", async () => {
  importBtn.disabled = true;
  showStatus(importStatus, t("Asking Garmin for your older activities…"));
  try {
    const months = Number(document.getElementById("import-months").value);
    const result = await api("/api/garmin/import-history", { method: "POST", body: JSON.stringify({ months }) });
    await loadHistoryPage(); // first: it clears the status lines
    showStatus(
      importStatus,
      t('Imported {activities} back to {date}. They are in the log under "Earlier weeks".', {
        activities: plural(result.activities_imported, "older activity", "older activities"),
        date: fmtLongDate(result.since),
      }),
    );
    importStatus.classList.add("ok");
  } catch (err) {
    if (err.status !== 401) showStatus(importStatus, err.message, true);
  } finally {
    importBtn.disabled = false;
  }
});
