// Import tab: your share code (so a friend can copy your plan), and copying a
// friend's planned sessions to your draft or straight onto your calendar.

import { api } from "../core/api.js";
import { addDays, fmtDate, fmtSpan, isoDate, plural, thisMonday } from "../core/format.js";
import { isSingular, locale, t } from "../core/i18n.js";
import { showTab } from "../core/tabs.js";
import { selectContents, showStatus } from "../core/ui.js";
import { showCalendarAround } from "./calendar.js";
import { renderDraftPreview } from "./plan-preview.js";

const shareCode = document.getElementById("share-code");
const shareStatus = document.getElementById("share-status");
const shareNew = document.getElementById("share-new");
const friendForm = document.getElementById("friend-form");
const friendSource = document.getElementById("friend-source");
const friendWeeks = document.getElementById("friend-weeks");
const friendTarget = document.getElementById("friend-target");
const friendFormStatus = document.getElementById("friend-form-status");
const friendResult = document.getElementById("friend-result");
const friendSummary = document.getElementById("friend-summary");
const friendStatus = document.getElementById("friend-status");
const friendToPlan = document.getElementById("friend-to-plan");
const friendToCalendar = document.getElementById("friend-to-calendar");
const friendView = document.getElementById("friend-view");

let friendPreview = null; // the server's last preview, for the buttons
let targetTouched = false; // "onto my weeks" follows "their weeks" until changed by hand

// Mondays from `firstWeek` to `lastWeek` weeks away from this week.
function fillMondays(select, firstWeek, lastWeek) {
  const monday = thisMonday();
  select.innerHTML = "";
  for (let i = firstWeek; i <= lastWeek; i++) {
    const day = addDays(monday, i * 7);
    let label = day.toLocaleDateString(locale(), {
      weekday: "short",
      month: "short",
      day: "numeric",
      ...(day.getFullYear() !== monday.getFullYear() ? { year: "numeric" } : {}),
    });
    if (i === -1) label = t("Last week ({date})", { date: label });
    if (i === 0) label = t("This week ({date})", { date: label });
    if (i === 1) label = t("Next week ({date})", { date: label });
    const opt = document.createElement("option");
    opt.value = isoDate(day);
    opt.textContent = label;
    select.appendChild(opt);
  }
}

function followSource() {
  if (targetTouched) return;
  const same = [...friendTarget.options].some((o) => o.value === friendSource.value);
  friendTarget.value = same ? friendSource.value : friendTarget.options[0].value;
}

export async function loadImportPage() {
  for (const el of [shareStatus, friendFormStatus]) el.classList.add("hidden");
  if (!friendSource.options.length) {
    fillMondays(friendSource, -26, 26); // their past blocks can be copied too
    fillMondays(friendTarget, 0, 26); // but only onto weeks still ahead
    friendSource.value = isoDate(addDays(thisMonday(), 7));
    followSource();
  }
  try {
    const info = await api("/api/share");
    document.getElementById("share-username").textContent = info.username;
    shareCode.textContent = info.share_code;
  } catch (err) {
    if (err.status !== 401) showStatus(shareStatus, err.message, true);
  }
}

// --- Your share code ---

document.getElementById("share-copy").addEventListener("click", async () => {
  const text = t("Name: {name} · Share code: {code}", {
    name: document.getElementById("share-username").textContent,
    code: shareCode.textContent,
  });
  try {
    await navigator.clipboard.writeText(text);
    showStatus(shareStatus, t("Copied. Send it to the person who wants to copy your plan."));
    shareStatus.classList.add("ok");
  } catch (_) {
    selectContents(shareCode);
    showStatus(shareStatus, t("Couldn't copy automatically. The code is selected: press Cmd+C."), true);
  }
});

// Two clicks: whoever has the old code can't import from you any more.
shareNew.addEventListener("click", async () => {
  if (!shareNew.classList.contains("confirming")) {
    shareNew.classList.add("confirming");
    shareNew.textContent = t("Click again: the old code stops working");
    setTimeout(() => {
      shareNew.classList.remove("confirming");
      shareNew.textContent = t("New code");
    }, 5000);
    return;
  }
  shareNew.classList.remove("confirming");
  shareNew.textContent = t("New code");
  try {
    shareCode.textContent = (await api("/api/share/code", { method: "POST" })).share_code;
    showStatus(shareStatus, t("New code ready. The old one no longer works."));
    shareStatus.classList.add("ok");
  } catch (err) {
    if (err.status !== 401) showStatus(shareStatus, err.message, true);
  }
});

// --- Copying a friend's sessions ---

function friendRequest() {
  return {
    username: document.getElementById("friend-username").value.trim(),
    share_code: document.getElementById("friend-code").value.trim(),
    source_start: friendSource.value,
    weeks: Number(friendWeeks.value),
    target_start: friendTarget.value,
  };
}

function resetFriendButtons() {
  friendToPlan.classList.remove("confirming");
  friendToPlan.textContent = t("Import to plan");
  friendToCalendar.classList.remove("confirming");
  friendToCalendar.textContent = t("Import to calendar");
}

// What you see is what gets imported: any change hides the old preview.
function clearFriendPreview() {
  friendPreview = null;
  friendResult.classList.add("hidden");
}
for (const el of friendForm.querySelectorAll("input, select")) el.addEventListener("input", clearFriendPreview);
friendSource.addEventListener("change", followSource);
friendTarget.addEventListener("change", () => {
  targetTouched = true;
});

friendForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = friendRequest();
  if (!body.username || !body.share_code)
    return showStatus(friendFormStatus, t("Enter their name and share code."), true);
  friendFormStatus.classList.add("hidden");
  try {
    const preview = await api(`/api/share/import/preview?today=${isoDate(new Date())}`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    friendPreview = preview;
    renderDraftPreview(
      preview,
      document.getElementById("friend-preview"),
      t("{name} has no planned sessions in those weeks.", { name: preview.from_username }),
    );
    let summary = "";
    if (preview.sessions_count) {
      const weeks =
        preview.source_start === preview.start_date
          ? t("the weeks of {span}", { span: fmtSpan(preview.start_date, preview.end_date) })
          : t("their weeks of {theirs}, onto your weeks of {yours}", {
              theirs: fmtSpan(preview.source_start, preview.source_end),
              yours: fmtSpan(preview.start_date, preview.end_date),
            });
      summary = t("{sessions} from {name}, {weeks}.", {
        sessions: plural(preview.sessions_count, "session"),
        name: preview.from_username,
        weeks,
      });
      if (preview.skipped_past)
        summary +=
          " " +
          t(
            isSingular(preview.skipped_past)
              ? "{sessions} on days already past is left out."
              : "{sessions} on days already past are left out.",
            { sessions: plural(preview.skipped_past, "session") },
          );
      summary += preview.existing_planned
        ? " " +
          t("Import to calendar replaces the {sessions} already planned from {from} to {to}.", {
            sessions: plural(preview.existing_planned, "session"),
            from: fmtDate(preview.replace_from),
            to: fmtDate(preview.end_date),
          })
        : " " + t("Nothing is planned on your calendar on those dates yet.");
    }
    friendSummary.textContent = summary;
    friendSummary.classList.toggle("hidden", !summary);
    friendToPlan.disabled = friendToCalendar.disabled = !preview.sessions_count;
    resetFriendButtons();
    friendStatus.classList.add("hidden");
    friendView.classList.add("hidden");
    friendResult.classList.remove("hidden");
    friendResult.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (err) {
    clearFriendPreview();
    if (err.status !== 401) showStatus(friendFormStatus, err.message, true);
  }
});

// A second click when something of yours would be replaced, instead of a confirm() dialog.
function armed(btn, count, noun, where = "") {
  if (!count || btn.classList.contains("confirming")) return false;
  btn.classList.add("confirming");
  btn.textContent = t("Click again: replaces {sessions}", { sessions: plural(count, noun) }) + where;
  setTimeout(resetFriendButtons, 5000);
  return true;
}

friendToPlan.addEventListener("click", async () => {
  if (!friendPreview || armed(friendToPlan, friendPreview.draft_sessions, "session", t(" in your draft"))) return;
  friendToPlan.disabled = true;
  try {
    await api(`/api/share/import/plan?today=${isoDate(new Date())}`, {
      method: "POST",
      body: JSON.stringify(friendRequest()),
    });
    clearFriendPreview();
    showTab("plan"); // the draft is there, ready to adjust
  } catch (err) {
    if (err.status !== 401) showStatus(friendStatus, err.message, true);
  } finally {
    friendToPlan.disabled = false;
    resetFriendButtons();
  }
});

friendToCalendar.addEventListener("click", async () => {
  if (!friendPreview || armed(friendToCalendar, friendPreview.existing_planned, "planned session")) return;
  friendToCalendar.disabled = friendToPlan.disabled = true;
  try {
    const result = await api(`/api/share/import/calendar?today=${isoDate(new Date())}`, {
      method: "POST",
      body: JSON.stringify(friendRequest()),
    });
    const replaced = result.replaced
      ? t(", replacing {sessions}", { sessions: plural(result.replaced, "planned session") })
      : "";
    showStatus(
      friendStatus,
      t("Done: {sessions} on your calendar{replaced}.", { sessions: plural(result.added, "session"), replaced }),
    );
    friendStatus.classList.add("ok");
    friendView.classList.remove("hidden");
    friendPreview = { ...friendPreview, existing_planned: result.added }; // importing again would replace these
  } catch (err) {
    if (err.status !== 401) showStatus(friendStatus, err.message, true);
  } finally {
    friendToCalendar.disabled = friendToPlan.disabled = false;
    resetFriendButtons();
  }
});

// Like the Plan tab: the imported weeks start near the top of the calendar.
friendView.addEventListener("click", () => showCalendarAround(friendTarget.value));
