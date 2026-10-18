# Waypoint

Waypoint is a local-first dashboard for finding jobs and keeping an application search organized. It combines a FastAPI web app, a SQLite database, configurable job sources, match scoring, application tracking, and optional email digests.

## Features

- Browse, filter, search, and track job listings
- Organize applications by stage, from saved through offer or archived outcomes
- Add recruiter-sourced jobs, contacts, notes, and interaction history
- Configure job sources and search queries in the dashboard
- Calculate match scores from a locally provided resume profile
- Optionally send a digest through the [gog Gmail CLI](https://github.com/steipete/gogcli)
- Choose from Light, Dark, Trailblazer, and Stargazer themes

## Requirements

- Python 3.10 or newer
- `pip`
- Optional: `gog` configured for Gmail if you want to send email digests

## Quick start

```bash
git clone https://github.com/aaronnewcomb/waypoint-simple-job-search.git
cd waypoint
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
./run.sh
```

Open <http://127.0.0.1:8000>. `run.sh` binds to localhost by default. To choose another local port or host, set `HOST` and `PORT` before starting the app.

On Windows, after installing the dependencies, run:

```powershell
$env:HOST = "127.0.0.1"
$env:PORT = "8000"
python -m uvicorn app.main:app --host $env:HOST --port $env:PORT
```

## Personalization

Add a `resume.yaml` in the project root to customize match scoring. The file is intentionally ignored by Git because it may contain personal information. Start with this shape:

```yaml
match_keywords:
  primary:
    - product marketing
    - go-to-market
  secondary:
    - customer research
skills:
  marketing:
    - positioning
    - messaging
```

Without `resume.yaml`, Waypoint still works, but match scores use general title/company bonuses and learned feedback only. Adjust the default role queries in `app/scraper.py` for your search. Set `JOB_SEARCH_LOCATION` to change the default search region (it defaults to `United States`).

## Optional email digests

Configure `gog` for the Gmail account that will send digests, then set:

```bash
export GOG_ACCOUNT="you@example.com"
export GOG_KEYRING_PASSWORD="your-gog-keyring-password"
```

Set the destination address in the dashboard's Settings. If email is not configured, the dashboard and job tracking continue to work without it.

## Data and privacy

Waypoint stores jobs, contacts, notes, settings, and application activity in a local SQLite database. The database and personal resume profile are excluded from Git. Back them up privately if needed.

The app has no authentication. Keep it bound to localhost unless you have placed it behind suitable authentication and understand the privacy implications. Do not expose it directly to the public internet or include real job-search data, contact details, resumes, or credentials in bug reports.

Job-board pages and scraping behavior can change. Respect each source's terms and applicable laws, and use the source controls and scraping frequency responsibly.

## Project layout

```text
app/          FastAPI routes, database models, matching, scraping, email
static/       Styles and image assets
templates/    Jinja dashboard templates
run.sh        Local development launcher
```

## License

Waypoint is licensed under the MIT License. See [LICENSE](LICENSE).
