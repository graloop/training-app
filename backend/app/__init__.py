"""Training App backend: a FastAPI app that syncs friends' Garmin data, shows it
on a calendar, and turns it into a text log Claude plans from.

Where things are (docs/ARCHITECTURE.md has the full tour):

    main.py        the app: startup, security middleware, routers, the page
    config.py      settings from the environment
    database.py    engine, sessions, the startup column migration
    models.py      tables            schemas.py   what the API sends and accepts
    deps.py        who is calling    security.py  passwords, tokens, encryption
    accounts.py    first admin, lockout, connection log
    sharing.py     share codes (Import tab)
    visitor.py     what the proxy says about the visitor
    routers/       HTTP only, one file per /api prefix
    planning/      the training log and the draft plan (no HTTP, no Garmin)
    garmin/        everything that talks to Garmin
    cli.py         account rescue from the command line
"""
