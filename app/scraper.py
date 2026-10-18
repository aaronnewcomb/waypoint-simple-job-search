"""
Job scrapers for LinkedIn, Indeed, and company career pages.
"""
import re
import httpx
import os
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
from typing import Optional
from .database import Job, JobCategory, JobLocation, get_db, init_db
from .matcher import score_job, categorize_job

# Common user-agent to avoid blocking
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
}


def parse_relative_date(text: str) -> datetime | None:
    """Parse relative date strings like '3 days ago', '2 weeks ago', 'just now'."""
    text = text.lower().strip() if text else ""
    now = datetime.now(timezone.utc)
    if not text:
        return None
    if any(w in text for w in ["just now", "today", "moments", "moment"]):
        return now
    m = re.search(r'(\d+)\s*hour', text)
    if m:
        return now - timedelta(hours=int(m.group(1)))
    m = re.search(r'(\d+)\s*day', text)
    if m:
        return now - timedelta(days=int(m.group(1)))
    m = re.search(r'(\d+)\s*week', text)
    if m:
        return now - timedelta(weeks=int(m.group(1)))
    m = re.search(r'(\d+)\s*month', text)
    if m:
        return now - timedelta(days=int(m.group(1)) * 30)
    if "yesterday" in text:
        return now - timedelta(days=1)
    return None


def normalize_company(name: str) -> str:
    """Normalize a company name for dedup matching."""
    name = name.strip().lower()
    # Strip common suffixes and variants
    name = re.sub(r'\s+(ai|inc|corp|llc|ltd|tech|technologies|computing|software|solutions|network|networks|systems|group|global|digital|media)$', '', name)
    name = re.sub(r'\.(?!com|io|ai|co|org|net)', '', name)  # remove stray dots
    name = re.sub(r'[,.]+$', '', name)
    return name.strip()


def fetch_job_page_description(url: str) -> str:
    """Best-effort extraction of a job description from a public job URL."""
    if not url or not url.startswith(("http://", "https://")):
        return ""

    try:
        with httpx.Client(headers=HEADERS, timeout=15.0, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
    except Exception as e:
        print(f"Job description fetch error for '{url}': {e}")
        return ""

    try:
        soup = BeautifulSoup(resp.text, "html.parser")

        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()

        meta_candidates = [
            soup.select_one('meta[property="og:description"]'),
            soup.select_one('meta[name="description"]'),
            soup.select_one('meta[name="twitter:description"]'),
        ]
        meta_text = ""
        for candidate in meta_candidates:
            if candidate and candidate.get("content"):
                meta_text = candidate["content"].strip()
                if len(meta_text) >= 160:
                    break

        selectors = [
            '[data-testid*="job"]',
            '[class*="job-description"]',
            '[class*="description"]',
            '[class*="content"]',
            "main",
            "article",
        ]

        best_text = ""
        for selector in selectors:
            for node in soup.select(selector):
                pieces = [
                    el.get_text(" ", strip=True)
                    for el in node.select("h1, h2, h3, p, li")
                ]
                text = "\n".join(piece for piece in pieces if piece)
                text = re.sub(r"\n{3,}", "\n\n", text).strip()
                if len(text) > len(best_text):
                    best_text = text

        if len(best_text) < 200:
            body = soup.body or soup
            pieces = [el.get_text(" ", strip=True) for el in body.select("h1, h2, h3, p, li")]
            fallback_text = "\n".join(piece for piece in pieces if piece)
            fallback_text = re.sub(r"\n{3,}", "\n\n", fallback_text).strip()
            if len(fallback_text) > len(best_text):
                best_text = fallback_text

        description = best_text if len(best_text) >= 120 else meta_text
        description = re.sub(r"[ \t]{2,}", " ", description).strip()
        return description[:8000]
    except Exception as e:
        print(f"Job description parse error for '{url}': {e}")
        return ""

# Job search queries by category
PMM_QUERIES = [
    "product marketing manager",
    "senior product marketing manager",
    "director product marketing",
]

OTHER_QUERIES = [
    "education technology specialist",
    "IT support specialist",
    "nonprofit marketing",
]

DEFAULT_LOCATION = os.environ.get("JOB_SEARCH_LOCATION", "United States")


async def search_indeed(query: str, location: str = DEFAULT_LOCATION, max_results: int = 25) -> list[dict]:
    """Search Indeed for jobs. Returns list of job dicts."""
    jobs = []
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=15.0, follow_redirects=True) as client:
            # Indeed search URL
            url = f"https://www.indeed.com/jobs"
            params = {
                "q": query,
                "l": location,
                "sort": "date",
            }
            resp = await client.get(url, params=params)
            soup = BeautifulSoup(resp.text, "html.parser")

            # Indeed job cards - these selectors may need updates as Indeed changes
            cards = soup.select('[class*="job_seen_beacon"], .jobsearch-ResultsList > div, [data-testid="jobCard"]')
            if not cards:
                cards = soup.select('div[class*="cardOutline"], div[class*="jobCard"]')

            for card in cards[:max_results]:
                title_el = card.select_one('h2 a, [data-testid="jobTitle"], a[class*="jcs-JobTitle"]')
                company_el = card.select_one('[data-testid="company-name"], span[class*="companyName"]')
                location_el = card.select_one('[data-testid="text-location"], div[class*="companyLocation"]')
                snippet_el = card.select_one('[data-testid="jobsnippet_footer"], div[class*="job-snippet"]')
                date_el = card.select_one('[data-testid="jobCardDate"], span[class*="date"], span[class*="posted"]')

                if not title_el:
                    continue

                title = title_el.get_text(strip=True)
                company = company_el.get_text(strip=True) if company_el else ""
                location = location_el.get_text(strip=True) if location_el else ""
                description = snippet_el.get_text(strip=True) if snippet_el else ""
                link = title_el.get("href", "")
                if link and not link.startswith("http"):
                    link = "https://www.indeed.com" + link
                posted_date_str = date_el.get_text(strip=True) if date_el else None

                jobs.append({
                    "title": title,
                    "company": company,
                    "location": location,
                    "url": link,
                    "description": description,
                    "source": "indeed",
                    "posted_date": posted_date_str,
                })

    except Exception as e:
        print(f"Indeed search error for '{query}': {e}")

    return jobs


async def search_linkedin(query: str, location: str = DEFAULT_LOCATION, max_results: int = 25) -> list[dict]:
    """
    Search LinkedIn Jobs via public search.
    Note: LinkedIn heavily rate-limits and blocks scrapers. This is best-effort.
    """
    jobs = []
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=15.0, follow_redirects=True) as client:
            url = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
            params = {
                "keywords": query,
                "location": location,
                "f_TPR": "r86400",  # past 24 hours
                "position": 1,
                "pageNum": 0,
            }
            resp = await client.get(url, params=params)
            soup = BeautifulSoup(resp.text, "html.parser")

            cards = soup.select(".base-card, .job-search-card")
            for card in cards[:max_results]:
                title_el = card.select_one(".base-search-card__title, .job-search-card__title")
                company_el = card.select_one(".base-search-card__subtitle, .job-search-card__subtitle")
                location_el = card.select_one(".job-search-card__location")
                link_el = card.select_one("a.base-card__full-link, a[data-tracking-control-name]")
                date_el = card.select_one("time, .job-search-card__listdate, [datetime]")

                if not title_el:
                    continue

                title = title_el.get_text(strip=True)
                company = company_el.get_text(strip=True) if company_el else ""
                location = location_el.get_text(strip=True) if location_el else ""
                link = link_el.get("href", "") if link_el else ""
                posted_date_str = date_el.get("datetime") or date_el.get_text(strip=True) if date_el else None

                jobs.append({
                    "title": title,
                    "company": company,
                    "location": location,
                    "url": link,
                    "description": "",
                    "source": "linkedin",
                    "posted_date": posted_date_str,
                })

    except Exception as e:
        print(f"LinkedIn search error for '{query}': {e}")

    return jobs


def determine_location_type(location: str, title: str) -> JobLocation:
    """Determine if a job is remote, hybrid, or onsite based on text."""
    text = f"{location} {title}".lower()

    if any(w in text for w in ["remote", "work from home", "wfh", "distributed"]):
        return JobLocation.REMOTE
    elif any(w in text for w in ["hybrid", "flexible"]):
        return JobLocation.HYBRID
    elif any(w in text for w in ["onsite", "on-site", "in office", "in-office", "in person"]):
        return JobLocation.ONSITE

    # Default: if location mentions a specific city without remote/hybrid, assume hybrid
    return JobLocation.HYBRID


async def run_all_scrapers():
    """Run all job scrapers and save new jobs to the database. Called by cron."""
    init_db()
    db = get_db()
    new_jobs = 0

    try:
        # Load all existing jobs for fuzzy dedup matching
        all_jobs = db.query(Job).all()

        # Scrape PMM queries
        for query in PMM_QUERIES:
            # Try LinkedIn first, then Indeed
            linkedin_jobs = await search_linkedin(query)
            indeed_jobs = await search_indeed(query)

            for job_data in linkedin_jobs + indeed_jobs:
                # Check for duplicate by URL or normalized company+title
                norm_co = normalize_company(job_data["company"])
                norm_title = job_data["title"].strip().lower()[:60]
                existing = None
                for j in all_jobs:
                    if j.url and job_data["url"] and j.url == job_data["url"]:
                        existing = j
                        break
                    # Partial URL match for LinkedIn (same job, different tracking params)
                    if j.url and job_data["url"] and j.source == job_data["source"]:
                        j_base = j.url.split("?")[0].split("/view/")[-1][:30] if "/view/" in j.url else ""
                        new_base = job_data["url"].split("?")[0].split("/view/")[-1][:30] if "/view/" in job_data["url"] else ""
                        if j_base and new_base and j_base == new_base:
                            existing = j
                            break
                    if normalize_company(j.company) == norm_co and j.title.strip().lower()[:60] == norm_title:
                        existing = j
                        break

                if existing:
                    # Update last_seen_at
                    existing.last_seen_at = datetime.now(timezone.utc)
                    continue

                # Score and categorize
                score = score_job(job_data["title"], job_data.get("description", ""), job_data["company"])
                # Use auto-categorization even for PMM queries — only assign PMM if title matches
                category_str = categorize_job(job_data["title"], job_data.get("description", ""))
                try:
                    category = JobCategory(category_str)
                except ValueError:
                    category = JobCategory.OTHER

                location_type = determine_location_type(job_data["location"], job_data["title"])

                job = Job(
                    title=job_data["title"],
                    company=job_data["company"],
                    location=job_data["location"],
                    location_type=location_type,
                    url=job_data["url"],
                    description=job_data.get("description", ""),
                    source=job_data["source"],
                    category=category,
                    match_score=score,
                    published_at=parse_relative_date(job_data.get("posted_date", "")),
                )
                db.add(job)
                all_jobs.append(job)  # Prevent intra-run duplicates
                new_jobs += 1

        # Scrape other-interest queries
        for query in OTHER_QUERIES:
            indeed_jobs = await search_indeed(query)

            for job_data in indeed_jobs:
                norm_co = normalize_company(job_data["company"])
                norm_title = job_data["title"].strip().lower()[:60]

                existing = None
                for j in all_jobs:
                    if j.url and job_data["url"] and j.url == job_data["url"]:
                        existing = j
                        break
                    # Partial URL match for LinkedIn (same job, different tracking params)
                    if j.url and job_data["url"] and j.source == job_data["source"]:
                        j_base = j.url.split("?")[0].split("/view/")[-1][:30] if "/view/" in j.url else ""
                        new_base = job_data["url"].split("?")[0].split("/view/")[-1][:30] if "/view/" in job_data["url"] else ""
                        if j_base and new_base and j_base == new_base:
                            existing = j
                            break
                    if normalize_company(j.company) == norm_co and j.title.strip().lower()[:60] == norm_title:
                        existing = j
                        break

                if existing:
                    existing.last_seen_at = datetime.now(timezone.utc)
                    continue

                score = score_job(job_data["title"], job_data.get("description", ""), job_data["company"])
                # Auto-categorize other jobs
                category_str = categorize_job(job_data["title"], job_data.get("description", ""))
                try:
                    category = JobCategory(category_str)
                except ValueError:
                    category = JobCategory.OTHER

                location_type = determine_location_type(job_data["location"], job_data["title"])

                job = Job(
                    title=job_data["title"],
                    company=job_data["company"],
                    location=job_data["location"],
                    location_type=location_type,
                    url=job_data["url"],
                    description=job_data.get("description", ""),
                    source=job_data["source"],
                    category=category,
                    match_score=score,
                    published_at=parse_relative_date(job_data.get("posted_date", "")),
                )
                db.add(job)
                all_jobs.append(job)  # Prevent intra-run duplicates
                new_jobs += 1

        db.commit()
        print(f"Scraping complete. {new_jobs} new jobs found.")

    except Exception as e:
        db.rollback()
        print(f"Scraping error: {e}")
        raise
    finally:
        db.close()

    return new_jobs


# Mark stale jobs (not seen in 30 days)
def mark_stale_jobs():
    """Mark jobs as stale if they haven't been seen in 30 days."""
    from datetime import timedelta
    init_db()
    db = get_db()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        stale_count = db.query(Job).filter(
            Job.last_seen_at < cutoff,
            Job.is_stale == False,
            Job.is_active == True,
        ).update({"is_stale": True})
        db.commit()
        if stale_count:
            print(f"Marked {stale_count} jobs as stale.")
    finally:
        db.close()


if __name__ == "__main__":
    import asyncio
    asyncio.run(run_all_scrapers())
