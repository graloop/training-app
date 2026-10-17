"""Planning: the training log Claude plans from, and the draft plan that only
reaches the calendar when the athlete confirms it.

- basics.py         days, durations and sports, written the same way everywhere
- activity_text.py  one Garmin activity as text (pace, HR, effort, laps, weather)
- training_log.py   the whole log of the History tab (build_log)
- draft_parser.py   reads a draft, one session per line: date | sport | duration | details
- drafts.py         a saved draft as the page sees it, and putting sessions on the calendar
"""
