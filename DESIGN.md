# Waypoint design notes

## Overview

Waypoint is a local-first job discovery and application tracking dashboard. A single-user installation stores its data in SQLite and provides a browser interface on the local machine.

## Components

- **Web application:** FastAPI routes and Jinja templates
- **Database:** SQLite via SQLAlchemy
- **Sources:** Best-effort public job search pages and user-configured sources
- **Matching:** Keyword-based scoring from an optional, user-provided `resume.yaml`
- **Email:** Optional digest delivery through a separately configured `gog` Gmail CLI

## Data model

The database stores job listings, application stages, contacts, interactions, activity history, source settings, digest history, and user preferences. The database is local application data and must not be committed or shared without reviewing its contents.

## Runtime and privacy

The app currently has no authentication and is intended for local use. The development launcher binds to `127.0.0.1` by default. A deployment exposed to a LAN or the internet needs an authentication and access-control layer first.

Job descriptions, recruiter messages, notes, contact records, and resume information may be sensitive. Keep the database, resume profile, and backups private. Email delivery is opt-in and requires explicit local configuration.

## Extension points

- Add or tune search queries in `app/scraper.py`.
- Add a custom source from the dashboard's Settings.
- Supply a local `resume.yaml` to customize match keywords and skills.
- Extend application stages and categories in `app/database.py`.

## Known limitations

- Job board markup, access rules, and rate limits change. Scraping is best-effort, and users should respect each site's terms and applicable law.
- Search and match behavior is currently tuned for a small personal workflow and may need configuration for different roles or regions.
- There is no multi-user isolation, authentication, or hosted deployment configuration.
