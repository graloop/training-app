"""Everything that talks to Garmin Connect.

- client.py    the only file that calls Garmin (login, session, one function per request)
- sync.py      one sync of one account: what to fetch and how it is stored
- activity_file.py  an activity's original file -> heart rate, pace, power every few seconds
- autosync.py  when syncs run: every few hours in the background, and the jobs the Sync button starts

The flow: routers/garmin.py connects an account (client.login_and_export_session)
and starts jobs; autosync.py runs sync.sync_user in a thread, which calls
client.fetch_* and stores rows in models.py's tables. Nothing here builds text
for the page: that is planning/, reading the same tables.
"""
