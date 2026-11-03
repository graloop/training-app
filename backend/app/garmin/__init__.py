"""Everything that talks to Garmin Connect.

- client.py    the only file that calls Garmin (login, session, one function per request)
- sync.py      one sync of one account: what to fetch and how it is stored
- activity_file.py  an activity's original file -> heart rate, pace, power every few seconds
- autosync.py  when syncs run: every few hours in the background, and the jobs the Sync button starts
"""
