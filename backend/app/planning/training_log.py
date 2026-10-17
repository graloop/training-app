"""The training log of the History tab: the text Claude reads to coach the
athlete and write the plan. build_log() puts it together, top to bottom:

    intro, OBJECTIVES, ATHLETE (entered, then from Garmin), TRAINING STATUS,
    RULE SET, EARLIER HISTORY, one block per week (each day: sleep, sessions
    done, sessions planned), ALREADY ON THE CALENDAR AFTER TODAY

One activity's text (pace, laps, weather...) is in activity_text.py."""

import datetime
from dataclasses import dataclass

from sqlalchemy.orm import Session, defer

from .. import models
from .activity_text import detail_lines, fmt_activity
from .basics import (
    DAY_NAMES,
    MATCH_TOLERANCE,
    MONTH_NAMES,
    SPORTS,
    fmt_day,
    fmt_minutes,
    fmt_seconds,
    monday_of,
    sport_minutes,
    sport_of,
)

# --- Day labels and Garmin's training status ------------------------------------


def _fmt_night(night: models.SleepRecord | None, full: bool) -> str:
    """' · sleep 80 (7:30, deep 1:30, REM 1:40) · RHR 50 · HRV 60 balanced · body battery +70 overnight'"""
    if night is None:
        return ""
    parts = []
    if night.sleep_score is not None:
        text = f"sleep {round(night.sleep_score)}"
        if full and night.total_sleep_seconds:
            stages = [fmt_minutes(night.total_sleep_seconds / 60)]
            if night.deep_sleep_seconds:
                stages.append(f"deep {fmt_minutes(night.deep_sleep_seconds / 60)}")
            if night.rem_sleep_seconds:
                stages.append(f"REM {fmt_minutes(night.rem_sleep_seconds / 60)}")
            text += f" ({', '.join(stages)})"
        parts.append(text)
    if night.resting_heart_rate is not None:
        parts.append(f"RHR {round(night.resting_heart_rate)}")
    if full:
        raw = night.raw_json or {}
        if raw.get("avgOvernightHrv"):
            status = (raw.get("hrvStatus") or "").lower().replace("_", " ")
            parts.append(f"HRV {round(raw['avgOvernightHrv'])}{' ' + status if status and status != 'none' else ''}")
        if raw.get("bodyBatteryChange"):
            parts.append(f"body battery {round(raw['bodyBatteryChange']):+d} overnight")
    return "".join(f" · {p}" for p in parts)


def _fmt_day_label(day: datetime.date, night: models.SleepRecord | None, metrics: "models.DailyMetrics | None", full: bool) -> str:
    label = fmt_day(day)
    if metrics is not None and metrics.readiness is not None:
        label += f" · readiness {metrics.readiness}" + (f" {metrics.readiness_level}" if full and metrics.readiness_level else "")
    label += _fmt_night(night, full)
    if full and metrics is not None:
        if metrics.stress_avg is not None:
            label += f" · stress {metrics.stress_avg}"
        if metrics.body_battery_low is not None and metrics.body_battery_high is not None:
            label += f" · day battery {metrics.body_battery_low}–{metrics.body_battery_high}"
    return label


def _fmt_load(m: "models.DailyMetrics") -> str:
    text = f"acute load {round(m.acute_load)} (7 days)"
    if m.chronic_load:
        text += f" vs chronic {round(m.chronic_load)} (4 weeks)"
    if m.load_ratio is not None:
        text += f": ratio {m.load_ratio:.1f}" + (f", {m.load_ratio_status}" if m.load_ratio_status else "")
    return text


def _status_lines(latest: "models.DailyMetrics | None") -> list[str]:
    """Garmin's own verdict on the training: status, acute vs chronic load, load focus."""
    if latest is None:
        return ["- nothing from Garmin yet: press Sync with Garmin"]
    facts = []
    if latest.training_status:
        facts.append(latest.training_status)
    if latest.acute_load is not None:
        facts.append(_fmt_load(latest))
    if latest.vo2max:
        facts.append(f"VO2max {latest.vo2max:.1f}")
    lines = [f"- as of {fmt_day(latest.date)}: " + " · ".join(facts)] if facts else []
    focus = latest.load_focus or {}
    parts = []
    for key, name in (("low", "low aerobic"), ("high", "high aerobic"), ("anaerobic", "anaerobic")):
        if key in focus:
            target = focus.get(f"{key}_target")
            parts.append(f"{name} {focus[key]}" + (f" (target {target[0]}–{target[1]})" if target else ""))
    if parts:
        lines.append("- load by intensity over 4 weeks: " + " · ".join(parts) + (f" → {focus['phrase']}" if focus.get("phrase") else ""))
    return lines or ["- nothing from Garmin yet: press Sync with Garmin"]


# --- Earlier history: the long-term picture ---------------------------------------

# Before the detailed weeks, from Garmin's activity list (no effort, laps or
# recovery): every session for this many weeks, then weekly totals back to two
# years ago, then monthly totals for whatever older history was imported.
SESSION_WEEKS = 52
WEEKLY_YEARS = 2


def _totals(acts: list[models.Activity]) -> str:
    sports = {s: 0.0 for s in SPORTS}
    load = 0.0
    for a in acts:
        for sport, minutes in sport_minutes(a.activity_type, a.duration_seconds, a.extras):
            sports[sport] += minutes
        load += a.training_load or 0
    split = _split(sports)
    return (f"{fmt_minutes(sum(sports.values()))}{f' ({split})' if split else ''} · {len(acts)} session{'s' if len(acts) != 1 else ''}"
            + (f" · load {round(load)}" if load else ""))


def _earlier_weeks(db: Session, user: models.User, start: datetime.date, today: datetime.date | None = None) -> list[str]:
    """Everything stored before the detailed part, oldest first: the long-term picture."""
    today = today or datetime.date.today()
    acts = (
        db.query(models.Activity)
        .options(defer(models.Activity.raw_json), defer(models.Activity.laps))
        .filter(models.Activity.user_id == user.id,
                models.Activity.start_time < datetime.datetime.combine(start, datetime.time.min))
        .order_by(models.Activity.start_time)
        .all()
    )
    if not acts:
        return ['- no earlier activities stored ("Import older activities" adds up to 3 years)']
    sessions_from = start - datetime.timedelta(weeks=SESSION_WEEKS)
    weekly_from = min(monday_of(today - datetime.timedelta(days=365 * WEEKLY_YEARS)), sessions_from)
    lines = []

    older = [a for a in acts if a.start_time.date() < weekly_from]
    if older:
        lines.append(f"By month, before {fmt_day(weekly_from)}, {weekly_from.year} (totals only):")
        months: dict[tuple[int, int], list[models.Activity]] = {}
        for a in older:
            months.setdefault((a.start_time.year, a.start_time.month), []).append(a)
        year, month = min(months)
        while (year, month) <= (weekly_from.year, weekly_from.month):
            label = f"Month of {MONTH_NAMES[month - 1]} {year}"
            if (year, month) == (weekly_from.year, weekly_from.month):
                if (year, month) not in months:
                    break
                label += f" (to {fmt_day(weekly_from - datetime.timedelta(days=1))})"
            month_acts = months.get((year, month))
            lines.append(f"{label} · {_totals(month_acts)}" if month_acts else f"{label} · no training recorded")
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)

    by_week: dict[datetime.date, list[models.Activity]] = {}
    for a in acts:
        if a.start_time.date() >= weekly_from:
            by_week.setdefault(monday_of(a.start_time.date()), []).append(a)
    if by_week:
        if lines:
            lines.append("")
        lines.append(f"By week, every session listed from {fmt_day(sessions_from)}, {sessions_from.year}:")
        week_start = min(by_week) if not older else weekly_from
        while week_start < start:
            week = by_week.get(week_start, [])
            label = f"Week of {MONTH_NAMES[week_start.month - 1]} {week_start.day}, {week_start.year}"
            lines.append(f"{label} · {_totals(week)}" if week else f"{label} · no training recorded")
            if week_start >= sessions_from:
                lines += [f"  {fmt_day(a.start_time.date())} · {fmt_activity(a, effort=False)}" for a in week]
            week_start += datetime.timedelta(days=7)
    return lines


# --- About the athlete -------------------------------------------------------------


def rule_set_text(user: models.User) -> str:
    """The rule set. Until it is first saved, the older "About you" text and
    block notes stand in for it, so nothing written before is lost."""
    if user.rule_set is not None:
        return user.rule_set
    draft = user.plan_draft
    older = [(user.athlete_notes or "").strip(), ((draft.notes if draft else "") or "").strip()]
    return "\n\n".join(text for text in older if text)


def profile_lines(profile: dict | None) -> list[str]:
    """What the athlete entered on the Athlete tab, as log lines."""
    p = profile or {}
    lines = []

    def line(label: str, parts: list[str | None]) -> None:
        parts = [x for x in parts if x]
        if parts:
            lines.append(f"- {label}: " + " · ".join(parts))

    line("Body", [f"weight {p['weight_kg']:g} kg" if p.get("weight_kg") else None,
                  f"height {p['height_cm']:g} cm" if p.get("height_cm") else None])
    line("Heart rate", [f"max {p['max_hr']}" if p.get("max_hr") else None,
                        f"threshold {p['threshold_hr']}" if p.get("threshold_hr") else None,
                        f"resting {p['resting_hr']}" if p.get("resting_hr") else None])
    line("Bike", [f"FTP {p['ftp_watts']} W" + (f" (tested {p['ftp_date']})" if p.get("ftp_date") else "") if p.get("ftp_watts") else None,
                  f"power zones {p['bike_zones']}" if p.get("bike_zones") else None])
    line("Run", [f"threshold pace {p['threshold_pace']} /km" if p.get("threshold_pace") else None,
                 f"pace zones {p['run_zones']}" if p.get("run_zones") else None])
    line("Swim", [f"CSS {p['css_pace']} /100 m" if p.get("css_pace") else None,
                  f"swim zones {p['swim_zones']}" if p.get("swim_zones") else None])
    for key, label in (("equipment", "Equipment"), ("health", "Health and injuries"), ("other", "Other")):
        if p.get(key):
            lines.append(f"- {label}: " + " ".join(p[key].split()))
    return lines


def latest_status(db: Session, user: models.User, today: datetime.date) -> "models.DailyMetrics | None":
    return (
        db.query(models.DailyMetrics)
        .filter(models.DailyMetrics.user_id == user.id, models.DailyMetrics.date <= today,
                models.DailyMetrics.acute_load.is_not(None))
        .order_by(models.DailyMetrics.date.desc())
        .first()
    )


def garmin_lines(db: Session, user: models.User, today: datetime.date) -> list[str]:
    """Garmin's side of the athlete (zones, records) and training status, as text."""
    return _athlete_lines(user) + ["", "Training status:"] + _status_lines(latest_status(db, user, today))


def _athlete_lines(user: models.User) -> list[str]:
    """Zones, thresholds, records and predictions as Garmin has them."""
    profile = user.garmin_profile or {}
    if not profile:
        return ["- nothing from Garmin yet: press Sync with Garmin"]
    lines = []
    facts = []
    for key, text in (("max_hr", "max HR {}"), ("lactate_threshold_hr", "threshold HR {}"), ("vo2max_running", "VO2max (run) {}"),
                      ("vo2max_cycling", "VO2max (bike) {}"), ("ftp_watts", "FTP {} W"), ("weight_kg", "weight {} kg"),
                      ("height_cm", "height {} cm")):
        if profile.get(key):
            facts.append(text.format(profile[key]))
    if profile.get("threshold_pace_s_per_km"):
        facts.insert(2, f"threshold pace {fmt_seconds(profile['threshold_pace_s_per_km'])} /km")
    if facts:
        lines.append("- " + " · ".join(facts))
    floors = profile.get("hr_zone_floors")
    if floors:
        zones = [f"Z{i} {low}–{floors[i] - 1}" for i, low in enumerate(floors[:-1], start=1)] + [f"Z5 {floors[-1]}+"]
        lines.append("- HR zones: " + " · ".join(zones))
    if profile.get("race_predictions"):
        lines.append("- Garmin race predictions: " + " · ".join(f"{k} {fmt_seconds(v)}" for k, v in profile["race_predictions"].items()))
    records = []
    for r in profile.get("records") or []:
        if r["kind"] == "time":
            value = fmt_seconds(r["value"])
        elif r["kind"] == "power":
            value = f"{round(r['value'])} W"
        else:
            value = f"{r['value'] / 1000:.1f} km"
        records.append(f"{r['label']} {value}" + (f" ({r['date'][:7]})" if r.get("date") else ""))
    if records:
        lines.append("- Personal records: " + " · ".join(records))
    if profile.get("synced_on"):
        lines.append(f"- (from Garmin, as of {profile['synced_on']})")
    return lines


# --- Planned vs. done ---------------------------------------------------------------


def _fmt_planned(p: models.PlannedTraining, done: models.Activity | None = None) -> str:
    sport = sport_of(p.activity_type)
    text = f"{sport if sport != 'other' else p.activity_type} {fmt_minutes(p.planned_duration_minutes)}"
    if p.notes:
        text += f" — {' '.join(p.notes.split())}"
    if done is None:
        return text
    did = (done.duration_seconds or 0) / 60
    plan = p.planned_duration_minutes or 0
    if plan and abs(did - plan) / plan <= MATCH_TOLERANCE:
        return text + " (done as planned)"
    return text + f" (done, {fmt_minutes(did)} instead of {fmt_minutes(plan)})"


def matched_plans(planned: list[models.PlannedTraining], activities: list[models.Activity]) -> dict[int, models.Activity]:
    """Planned session id -> the activity that did it, for one day. First the
    same sport within 15% of the planned time ("done as planned"), then the
    same sport at any duration. One activity covers one planned session:
    split sessions are not added up."""
    left = [a for a in activities if a.duration_seconds]
    matched: dict[int, models.Activity] = {}
    plans = [p for p in planned if (p.planned_duration_minutes or 0) > 0 and sport_of(p.activity_type) != "other"]
    for strict in (True, False):
        for p in plans:
            if p.id in matched:
                continue
            plan_min = p.planned_duration_minutes
            best = None
            for a in left:
                if sport_of(a.activity_type) != sport_of(p.activity_type):
                    continue
                diff = abs(a.duration_seconds / 60 - plan_min) / plan_min
                if (not strict or diff <= MATCH_TOLERANCE) and (best is None or diff < best[0]):
                    best = (diff, a)
            if best:
                matched[p.id] = best[1]
                left.remove(best[1])
    return matched


def _split(minutes_by_sport: dict[str, float]) -> str:
    return " · ".join(f"{s} {fmt_minutes(m)}" for s, m in minutes_by_sport.items() if m > 0)


# --- The whole log ----------------------------------------------------------------


@dataclass
class _Stored:
    """What the detailed weeks show, from the database, grouped by day."""

    activities: list[models.Activity]
    acts_by_day: dict[datetime.date, list[models.Activity]]
    planned: list[models.PlannedTraining]  # from the first day covered, ahead included
    plans_by_day: dict[datetime.date, list[models.PlannedTraining]]
    sleep: dict[datetime.date, models.SleepRecord]
    daily: dict[datetime.date, models.DailyMetrics]
    data_start: datetime.date | None  # where this account's Garmin data begins


def _load(db: Session, user: models.User, start: datetime.date, today: datetime.date) -> _Stored:
    activities = (
        db.query(models.Activity)
        .filter(
            models.Activity.user_id == user.id,
            models.Activity.start_time >= datetime.datetime.combine(start, datetime.time.min),
            models.Activity.start_time <= datetime.datetime.combine(today, datetime.time.max),
        )
        .order_by(models.Activity.start_time)
        .all()
    )
    sleep = {
        s.date: s
        for s in db.query(models.SleepRecord).filter(
            models.SleepRecord.user_id == user.id, models.SleepRecord.date >= start, models.SleepRecord.date <= today
        )
    }
    planned = (
        db.query(models.PlannedTraining)
        .filter(models.PlannedTraining.user_id == user.id, models.PlannedTraining.date >= start)
        .order_by(models.PlannedTraining.date, models.PlannedTraining.id)
        .all()
    )
    daily = {
        m.date: m
        for m in db.query(models.DailyMetrics).filter(
            models.DailyMetrics.user_id == user.id, models.DailyMetrics.date >= start, models.DailyMetrics.date <= today
        )
    }
    # Where this account's Garmin data begins: earlier days are "not synced", not "no training".
    first_activity = db.query(models.Activity.start_time).filter(models.Activity.user_id == user.id).order_by(models.Activity.start_time).first()
    first_night = db.query(models.SleepRecord.date).filter(models.SleepRecord.user_id == user.id).order_by(models.SleepRecord.date).first()
    starts = [d for d in (first_activity[0].date() if first_activity else None, first_night[0] if first_night else None) if d]

    acts_by_day: dict[datetime.date, list[models.Activity]] = {}
    for a in activities:
        acts_by_day.setdefault(a.start_time.date(), []).append(a)
    plans_by_day: dict[datetime.date, list[models.PlannedTraining]] = {}
    for p in planned:
        plans_by_day.setdefault(p.date, []).append(p)
    return _Stored(activities, acts_by_day, planned, plans_by_day, sleep, daily, min(starts) if starts else None)


def _intro_lines(user: models.User, today: datetime.date, start: datetime.date, weeks: int, stored: _Stored) -> list[str]:
    lines = [
        f"TRAINING LOG · {user.username}",
        f"Today is {DAY_NAMES[today.weekday()]} {MONTH_NAMES[today.month - 1]} {today.day}, {today.year}. "
        f"Covers {fmt_day(start)} to {fmt_day(today)} ({weeks} week{'s' if weeks != 1 else ''}), from Garmin.",
        "Times are h:mm. \"plan\" lines are what was planned for that day. Effort (1-10) and \"felt\" are what "
        "the athlete answered on the watch after the session; \"effort not answered\" means the prompt was skipped.",
    ]
    unsynced = sum(1 for a in stored.activities if a.details_synced_at is None)
    if unsynced:
        lines.append(f"NOTE: {unsynced} session{'s' if unsynced != 1 else ''} below still wait{'s' if unsynced == 1 else ''} "
                     "for effort, feel, laps and weather from Garmin (\"effort not synced yet\"): press Sync with Garmin.")
    return lines


def _objective_lines(db: Session, user: models.User, today: datetime.date) -> list[str]:
    objectives = (
        db.query(models.Objective)
        .filter(models.Objective.user_id == user.id)
        .order_by(models.Objective.event_date.is_(None), models.Objective.event_date)
        .all()
    )
    lines = ["", "OBJECTIVES"]
    if not objectives:
        return lines + ["- none yet (add them on the Athlete tab)"]
    for o in objectives:
        when = f"{o.event_date.isoformat()} ({(o.event_date - today).days} days away)" if o.event_date else "no date"
        lines.append(f"- {o.title} · {when} · target {o.target_time or 'not set'}")
        if o.notes:
            lines.append(f"  {' '.join(o.notes.split())}")
    return lines


def _athlete_section(db: Session, user: models.User, today: datetime.date) -> list[str]:
    """The athlete's numbers, Garmin's, Garmin's training status and the rule set."""
    lines = ["", "ATHLETE (entered by the athlete: these override Garmin's estimates)"]
    lines += profile_lines(user.athlete_profile) or ["- nothing entered yet (Athlete tab)"]
    lines += ["", "ATHLETE (from Garmin)"] + _athlete_lines(user)
    lines += ["", "TRAINING STATUS (Garmin)"] + _status_lines(latest_status(db, user, today))
    rules = rule_set_text(user).strip()
    lines += ["", "RULE SET (the athlete's availability, constraints and rules: the plan must follow them)"]
    lines += [line.rstrip() for line in rules.splitlines()] if rules else ["- nothing written yet"]
    return lines


def _week_lines(week_start: datetime.date, today: datetime.date, stored: _Stored, full: bool) -> list[str]:
    """One week: a header with the totals, then each day up to today."""
    days = [week_start + datetime.timedelta(days=i) for i in range(7)]
    if stored.data_start is None or days[-1] < stored.data_start:
        return ["", f"WEEK OF {fmt_day(week_start).upper()} · not synced"
                + (f" (this account's Garmin data starts {fmt_day(stored.data_start)})" if stored.data_start else " (no Garmin data yet)")]

    done = {s: 0.0 for s in SPORTS}
    planned_min = 0.0
    load = 0.0
    scores, rhrs = [], []
    for day in days:
        for a in stored.acts_by_day.get(day, []):
            for sport, minutes in sport_minutes(a.activity_type, a.duration_seconds, a.extras):
                done[sport] += minutes
            load += a.training_load or 0
        planned_min += sum(p.planned_duration_minutes or 0 for p in stored.plans_by_day.get(day, []))
        night = stored.sleep.get(day)
        if night and night.sleep_score is not None:
            scores.append(night.sleep_score)
        if night and night.resting_heart_rate is not None:
            rhrs.append(night.resting_heart_rate)

    header = f"WEEK OF {fmt_day(week_start).upper()} · done {fmt_minutes(sum(done.values()))}"
    if sum(done.values()) > 0:
        header += f" ({_split(done)})"
    header += f" · planned {fmt_minutes(planned_min)}"
    if load:
        header += f" · load {round(load)}"
    if scores:
        header += f" · sleep avg {round(sum(scores) / len(scores))}"
    if rhrs:
        header += f" · resting HR avg {round(sum(rhrs) / len(rhrs))}"
    week_metrics = [stored.daily[d] for d in days if d in stored.daily]
    ready = [m.readiness for m in week_metrics if m.readiness is not None]
    if ready:
        header += f" · readiness avg {round(sum(ready) / len(ready))}"
    if week_start == monday_of(today):
        header += " · in progress"
    lines = ["", header]
    if full:
        recovery = []
        stress = [m.stress_avg for m in week_metrics if m.stress_avg is not None]
        if stress:
            recovery.append(f"stress avg {round(sum(stress) / len(stress))}")
        loaded = [m for m in week_metrics if m.acute_load is not None]
        if loaded:
            recovery.append(f"at week end {_fmt_load(loaded[-1])}")
            if loaded[-1].training_status:
                recovery.append(f"Garmin status {loaded[-1].training_status}")
        if recovery:
            lines.append("  " + " · ".join(recovery))

    for day in days:
        if day > today:
            break
        if day < stored.data_start:
            continue  # before this account's Garmin data starts
        day_acts = stored.acts_by_day.get(day, [])
        day_plans = stored.plans_by_day.get(day, [])
        label = _fmt_day_label(day, stored.sleep.get(day), stored.daily.get(day), full)
        if not day_acts and not day_plans:
            lines.append(f"{label} · no training")
            continue
        lines.append(label)
        matched = matched_plans(day_plans, day_acts)
        for a in day_acts:
            lines.append(f"  done  {fmt_activity(a)}")
            if full:
                lines += [f"        {extra}" for extra in detail_lines(a)]
        lines += [f"  plan  {_fmt_planned(p, matched.get(p.id))}" for p in day_plans]
    return lines


def _ahead_lines(planned: list[models.PlannedTraining], today: datetime.date) -> list[str]:
    """What is already planned after today, week by week."""
    week_end = monday_of(today) + datetime.timedelta(days=6)
    ahead = [p for p in planned if p.date > today]
    lines = ["", "ALREADY ON THE CALENDAR AFTER TODAY"]
    if ahead:
        current = None
        for p in ahead:
            if monday_of(p.date) != current:
                current = monday_of(p.date)
                week_total = sum(q.planned_duration_minutes or 0 for q in ahead if monday_of(q.date) == current)
                lines.append(f"Week of {fmt_day(current)} · planned {fmt_minutes(week_total)}")
            lines.append(f"  {fmt_day(p.date)} · {_fmt_planned(p)}")
    else:
        lines.append("- nothing planned yet")
    if week_end > today and not any(p.date <= week_end for p in ahead):
        lines.append(f"(Nothing planned for the rest of this week, through {fmt_day(week_end)}.)")
    return lines


def build_log(db: Session, user: models.User, weeks: int, today: datetime.date, detail: str = "full") -> tuple[str, datetime.date]:
    """Plain-text log of the last `weeks` weeks (this week included) plus what
    is already planned ahead. `detail` "full" adds per-session zones, laps and
    recovery numbers; "summary" keeps one line per session.
    Returns (text, first day covered)."""
    full = detail == "full"
    start = monday_of(today) - datetime.timedelta(weeks=weeks - 1)
    stored = _load(db, user, start, today)

    lines = _intro_lines(user, today, start, weeks, stored)
    lines += _objective_lines(db, user, today)
    lines += _athlete_section(db, user, today)
    lines += ["", "EARLIER HISTORY (from Garmin's activity list: duration, distance, pace, avg HR and load; "
              "no effort, laps or recovery)"] + _earlier_weeks(db, user, start, today)
    week_start = start
    while week_start <= today:
        lines += _week_lines(week_start, today, stored, full)
        week_start += datetime.timedelta(days=7)
    lines += _ahead_lines(stored.planned, today)
    return "\n".join(lines), start
