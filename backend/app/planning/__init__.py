"""Planning: the training log Claude plans from, and the draft plan that only
reaches the calendar when the athlete confirms it.

- basics.py         days, durations and sports, written the same way everywhere
- activity_text.py  one Garmin activity as text (pace, HR, effort, laps, weather)
- session_detail.py what a session's 5-second trace adds, and one session in full
- training_log.py   the whole log of the History tab (build_log)
- draft_parser.py   reads a draft, one session per line: date | sport | duration | details
- drafts.py         a saved draft as the page sees it, and putting sessions on the calendar

The loop it serves: the athlete copies the log (History tab) to Claude, Claude
writes draft lines in the Plan tab, the parser checks them, and confirming puts
them on the calendar as PlannedTraining rows. Nothing here calls Garmin: it only
reads what garmin/sync.py stored. Imports go one way: basics <- activity_text <-
session_detail <- training_log, and basics <- draft_parser <- drafts.
"""
