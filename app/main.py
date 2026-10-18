"""
Job Search Assistant — FastAPI Application
"""
from fastapi import FastAPI, Query, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from fastapi.staticfiles import StaticFiles
from sqlalchemy import desc, asc, or_
from datetime import datetime, timezone, timedelta
from typing import Optional
import os
import json

from .database import (
    init_db, get_db, Job, Application, Contact, Interaction,
    JobCategory, ApplicationStage, JobLocation, InteractionType, JobSource,
    ActivityLog, AppSetting, log_activity, get_setting, set_setting,
)
from .matcher import score_job, categorize_job
from .scraper import run_all_scrapers, fetch_job_page_description
from .emailer import run_daily_digest

app = FastAPI(title="Job Search Assistant")

# Templates
TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
j2env = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR),
    autoescape=select_autoescape(["html"]),
    cache_size=0,  # Disable caching for Python 3.14 compat
)


def render_template(name: str, context: dict) -> HTMLResponse:
    """Render a Jinja2 template and return an HTMLResponse."""
    template = j2env.get_template(name)
    return HTMLResponse(template.render(**context))


def build_job_description(url: str, custom_description: str) -> tuple[str, str]:
    """Return the best description and where it came from."""
    custom_description = (custom_description or "").strip()
    fetched_description = fetch_job_page_description(url.strip()) if url and url.strip() else ""

    if fetched_description:
        return fetched_description, "url"
    if custom_description:
        return custom_description, "custom"
    return "", "none"


# Initialize DB on startup
@app.on_event("startup")
def startup():
    init_db()


# ─── Template helpers ───

STAGE_LABELS = {
    "saved": "📌 Saved",
    "applied": "📤 Applied",
    "phone_screen": "📞 Phone Screen",
    "interview_1": "🗣 Interview 1",
    "interview_2": "🗣 Interview 2",
    "interview_final": "🏁 Final Interview",
    "offer": "🎉 Offer",
    "accepted": "✅ Accepted",
    "rejected": "❌ Rejected",
    "withdrawn": "🚫 Withdrawn",
    "not_interested": "🚫 Not Interested",
}

CATEGORY_LABELS = {
    "pmm": "📊 PMM",
    "education": "📚 Education",
    "it_support": "💻 IT Support",
    "nonprofit_marketing": "❤️ Non-Profit Marketing",
    "other": "📋 Other",
}

LOCATION_LABELS = {
    "remote": "🏠 Remote",
    "hybrid": "🔄 Hybrid",
    "onsite": "🏢 On-site",
}

STAGE_ORDER = [
    "applied", "phone_screen", "interview_1",
    "interview_2", "interview_final", "offer", "accepted",
    "rejected", "withdrawn", "saved",
]


def stage_sort_key(stage: str) -> int:
    try:
        return STAGE_ORDER.index(stage)
    except ValueError:
        return 99


def is_new_job(job: Job) -> bool:
    """Treat jobs as new based on published date when available, otherwise discovery date."""
    reference_dt = job.published_at or job.discovered_at
    if not reference_dt:
        return False
    if reference_dt.tzinfo is None:
        reference_dt = reference_dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - reference_dt).days < 10


def serialize_job(job: Job) -> dict:
    app_stage = job.application.stage.value if job.application else "saved"
    return {
        "id": job.id,
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "location_type": job.location_type.value,
        "url": job.url,
        "source": job.source,
        "category": job.category.value,
        "category_label": CATEGORY_LABELS.get(job.category.value, job.category.value),
        "match_score": job.match_score,
        "stage": app_stage,
        "stage_label": STAGE_LABELS.get(app_stage, app_stage),
        "is_active": job.is_active,
        "is_stale": job.is_stale,
        "is_excluded": job.is_excluded,
        "discovered_at": job.discovered_at.isoformat() if job.discovered_at else None,
        "published_at": job.published_at.isoformat() if job.published_at else None,
        "is_new": is_new_job(job),
        "notes": job.notes,
        "salary_range": job.salary_range,
    }


# ─── Pages ───

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    return render_template("dashboard.html", {
        "request": request,
        "categories": [(c.value, CATEGORY_LABELS[c.value]) for c in JobCategory],
        "stages": [(s.value, STAGE_LABELS[s.value]) for s in ApplicationStage],
        "location_types": [(l.value, LOCATION_LABELS[l.value]) for l in JobLocation],
    })


# ─── API: Jobs ───

@app.get("/api/jobs")
def list_jobs(
    request: Request,
    category: Optional[str] = Query(None),
    stage: Optional[str] = Query(None),
    location_type: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    sort: Optional[str] = Query("match_score"),
    order: Optional[str] = Query("desc"),
    active_only: bool = Query(True),
    show_excluded: bool = Query(False),
    page: int = Query(1),
    per_page: str = Query("50"),
):
    db = get_db()
    try:
        try:
            per_page_value = None if per_page == "all" else max(1, int(per_page))
        except ValueError:
            per_page = "50"
            per_page_value = 50

        base_query = db.query(Job)

        if active_only:
            base_query = base_query.filter(Job.is_active == True)
        if category:
            base_query = base_query.filter(Job.category == category)
        if location_type:
            base_query = base_query.filter(Job.location_type == location_type)
        if source:
            base_query = base_query.filter(Job.source == source)
        if search:
            search_term = f"%{search}%"
            base_query = base_query.filter(
                or_(
                    Job.title.ilike(search_term),
                    Job.company.ilike(search_term),
                    Job.description.ilike(search_term),
                    Job.location.ilike(search_term),
                )
            )

        active_query = base_query.filter(Job.is_excluded == False)
        archived_query = base_query.filter(Job.is_excluded == False).join(Application).filter(
            Application.stage == ApplicationStage.NOT_INTERESTED
        )
        excluded_query = base_query.filter(Job.is_excluded == True)

        if stage:
            if stage == ApplicationStage.NOT_INTERESTED.value:
                active_query = active_query.join(Application).filter(
                    Application.stage == ApplicationStage.NOT_INTERESTED
                )
            else:
                active_query = active_query.join(Application, isouter=True).filter(
                    Application.stage == stage
                )
                archived_query = archived_query.filter(False)

        if sort == "stage":
            all_active = active_query.all()
            active_serialized = []
            for job in all_active:
                payload = serialize_job(job)
                if payload["stage"] != ApplicationStage.NOT_INTERESTED.value:
                    active_serialized.append(payload)
            active_serialized.sort(key=lambda job: stage_sort_key(job["stage"]))
        else:
            if not stage:
                active_query = active_query.join(Application, isouter=True)
                active_query = active_query.filter(
                    (Application.stage.is_(None)) | (Application.stage != ApplicationStage.NOT_INTERESTED)
                )
            elif stage == ApplicationStage.NOT_INTERESTED.value:
                active_query = active_query.filter(False)
            sort_col = getattr(Job, sort, Job.match_score)
            if order == "asc":
                active_query = active_query.order_by(asc(sort_col))
            else:
                active_query = active_query.order_by(desc(sort_col))
            active_serialized = None

        archived_jobs = [serialize_job(job) for job in archived_query.all()]
        excluded_jobs = [serialize_job(job) for job in excluded_query.all()] if show_excluded else []

        if sort == "stage":
            total = len(active_serialized)
            effective_per_page = total if per_page == "all" else per_page_value
            start = (page - 1) * effective_per_page
            result = active_serialized[start:start + effective_per_page]
        else:
            total = active_query.count()
            effective_per_page = total if per_page == "all" else per_page_value
            jobs = active_query.offset((page - 1) * effective_per_page).limit(effective_per_page).all()
            result = [serialize_job(job) for job in jobs]

        pages = 1 if per_page == "all" else max(1, (total + effective_per_page - 1) // effective_per_page)

        # Return HTML for HTMX requests, JSON for API
        if request.headers.get("HX-Request"):
            return render_template("jobs_table.html", {
                "request": request,
                "jobs": result,
                "archived_jobs": archived_jobs,
                "excluded_jobs": excluded_jobs,
                "current_sort": sort,
                "total": total,
                "page": page,
                "pages": pages,
            })

        return {
            "jobs": result,
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": pages,
        }
    finally:
        db.close()


@app.get("/api/jobs/{job_id}")
def get_job(job_id: int):
    db = get_db()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        app_data = None
        if job.application:
            app_data = {
                "stage": job.application.stage.value,
                "stage_label": STAGE_LABELS.get(job.application.stage.value, ""),
                "applied_at": job.application.applied_at.isoformat() if job.application.applied_at else None,
                "resume_version": job.application.resume_version,
                "notes": job.application.notes,
            }

        contacts = []
        for c in job.contacts:
            interactions = []
            for ix in c.interactions:
                interactions.append({
                    "id": ix.id,
                    "type": ix.type.value if ix.type else "other",
                    "happened_at": ix.happened_at.isoformat() if ix.happened_at else None,
                    "summary": ix.summary,
                    "follow_up_needed": ix.follow_up_needed,
                    "follow_up_date": ix.follow_up_date.isoformat() if ix.follow_up_date else None,
                    "thank_you_sent": ix.thank_you_sent,
                    "thank_you_sent_at": ix.thank_you_sent_at.isoformat() if ix.thank_you_sent_at else None,
                })
            contacts.append({
                "id": c.id,
                "name": c.name,
                "title": c.title,
                "email": c.email,
                "linkedin_url": c.linkedin_url,
                "role": c.role,
                "notes": c.notes,
                "interactions": interactions,
            })

        return {
            "id": job.id,
            "title": job.title,
            "company": job.company,
            "location": job.location,
            "location_type": job.location_type.value,
            "url": job.url,
            "description": job.description,
            "salary_range": job.salary_range,
            "source": job.source,
            "category": job.category.value,
            "category_label": CATEGORY_LABELS.get(job.category.value, job.category.value),
            "match_score": job.match_score,
            "is_active": job.is_active,
            "is_stale": job.is_stale,
            "is_excluded": job.is_excluded,
            "discovered_at": job.discovered_at.isoformat() if job.discovered_at else None,
            "notes": job.notes,
            "application": app_data,
            "contacts": contacts,
            "activities": [{
                "id": a.id,
                "action": a.action,
                "detail": a.detail,
                "old_value": a.old_value,
                "new_value": a.new_value,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            } for a in (job.activities or [])],
        }
    finally:
        db.close()


@app.put("/api/jobs/{job_id}")
def update_job(
    job_id: int,
    category: Optional[str] = Form(None),
    is_active: Optional[bool] = Form(None),
    is_excluded: Optional[bool] = Form(None),
    notes: Optional[str] = Form(None),
    salary_range: Optional[str] = Form(None),
):
    db = get_db()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        if category is not None:
            job.category = JobCategory(category)
        if is_active is not None:
            job.is_active = is_active
        if is_excluded is not None:
            old = "interested" if not job.is_excluded else "not_interested"
            job.is_excluded = is_excluded
            reason_text = "Marked as not interested" if is_excluded else "Restored to interested"
            if notes and is_excluded:
                reason_text += f": {notes}"
            log_activity(job_id, "not_interested" if is_excluded else "restored",
                        reason_text,
                        old, "not_interested" if is_excluded else "interested")
        if notes is not None and notes != job.notes:
            # Don't double-log if this is a not-interested reason
            if not (is_excluded and is_excluded):
                old_notes = job.notes
                job.notes = notes
                log_activity(job_id, "note_added", notes, old_notes, notes)
            else:
                job.notes = notes
        if salary_range is not None:
            job.salary_range = salary_range

        db.commit()
        return {"ok": True}
    finally:
        db.close()


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: int):
    db = get_db()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        db.delete(job)
        db.commit()
        return {"ok": True}
    finally:
        db.close()


# ─── API: Applications ───

@app.post("/api/jobs/{job_id}/apply")
def apply_to_job(
    job_id: int,
    stage: str = Form("applied"),
    resume_version: str = Form(""),
    notes: str = Form(""),
):
    db = get_db()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        if job.application:
            app = job.application
            app.stage = ApplicationStage(stage)
            app.stage_updated_at = datetime.now(timezone.utc)
            if resume_version:
                app.resume_version = resume_version
            if notes:
                app.notes = notes
        else:
            app = Application(
                job_id=job_id,
                stage=ApplicationStage(stage),
                applied_at=datetime.now(timezone.utc) if stage in ("applied", "phone_screen", "interview_1", "interview_2", "interview_final") else None,
                stage_updated_at=datetime.now(timezone.utc),
                resume_version=resume_version,
                notes=notes,
            )
            db.add(app)

        db.commit()
        return {"ok": True, "stage": stage, "stage_label": STAGE_LABELS.get(stage, stage)}
    finally:
        db.close()


@app.put("/api/jobs/{job_id}/stage")
def update_stage(job_id: int, stage: str = Form(...)):
    db = get_db()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        old_stage = job.application.stage.value if job.application else "saved"

        if not job.application:
            app = Application(job_id=job_id, stage=ApplicationStage(stage))
            db.add(app)
        else:
            job.application.stage = ApplicationStage(stage)
            job.application.stage_updated_at = datetime.now(timezone.utc)

        # Set applied_at on first move to applied
        if stage == "applied" and (not job.application or not job.application.applied_at):
            if job.application:
                job.application.applied_at = datetime.now(timezone.utc)

        # Log activity
        log_activity(job_id, "stage_change",
                    f"Stage changed: {STAGE_LABELS.get(old_stage, old_stage)} → {STAGE_LABELS.get(stage, stage)}",
                    old_stage, stage)

        db.commit()
        return {"ok": True, "stage": stage, "stage_label": STAGE_LABELS.get(stage, stage)}
    finally:
        db.close()


# ─── API: Contacts ───

@app.post("/api/jobs/{job_id}/contacts")
def add_contact(
    job_id: int,
    name: str = Form(...),
    title: str = Form(""),
    email: str = Form(""),
    linkedin_url: str = Form(""),
    role: str = Form(""),
    notes: str = Form(""),
):
    db = get_db()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        contact = Contact(
            job_id=job_id,
            name=name,
            title=title,
            email=email,
            linkedin_url=linkedin_url,
            role=role,
            notes=notes,
        )
        db.add(contact)
        db.commit()
        log_activity(job_id, "contact_added", f"Added contact: {name} ({role})" if role else f"Added contact: {name}")
        return {"ok": True, "id": contact.id}
    finally:
        db.close()


@app.delete("/api/contacts/{contact_id}")
def delete_contact(contact_id: int):
    db = get_db()
    try:
        contact = db.query(Contact).filter(Contact.id == contact_id).first()
        if not contact:
            raise HTTPException(status_code=404, detail="Contact not found")
        db.delete(contact)
        db.commit()
        return {"ok": True}
    finally:
        db.close()


# ─── API: Interactions ───

@app.post("/api/contacts/{contact_id}/interactions")
def add_interaction(
    contact_id: int,
    type: str = Form("other"),
    summary: str = Form(""),
    follow_up_needed: bool = Form(False),
    follow_up_date: Optional[str] = Form(None),
    thank_you_sent: bool = Form(False),
):
    db = get_db()
    try:
        contact = db.query(Contact).filter(Contact.id == contact_id).first()
        if not contact:
            raise HTTPException(status_code=404, detail="Contact not found")

        interaction = Interaction(
            contact_id=contact_id,
            type=InteractionType(type),
            happened_at=datetime.now(timezone.utc),
            summary=summary,
            follow_up_needed=follow_up_needed,
            follow_up_date=datetime.fromisoformat(follow_up_date) if follow_up_date else None,
            thank_you_sent=thank_you_sent,
            thank_you_sent_at=datetime.now(timezone.utc) if thank_you_sent else None,
        )
        db.add(interaction)
        db.commit()
        log_activity(contact.job_id, "interaction_added", f"{type} with {contact.name}" + (f": {summary}" if summary else ""))
        return {"ok": True, "id": interaction.id}
    finally:
        db.close()


@app.put("/api/interactions/{interaction_id}/thank-you")
def mark_thank_you_sent(interaction_id: int):
    db = get_db()
    try:
        ix = db.query(Interaction).filter(Interaction.id == interaction_id).first()
        if not ix:
            raise HTTPException(status_code=404, detail="Interaction not found")
        ix.thank_you_sent = True
        ix.thank_you_sent_at = datetime.now(timezone.utc)
        db.commit()
        log_activity(ix.contact.job_id if ix.contact else None, "thank_you_sent", f"Thank you sent to {ix.contact.name}" if ix.contact else "Thank you sent")
        return {"ok": True}
    finally:
        db.close()


# ─── API: Actions ───

@app.post("/api/scrape")
def trigger_scrape():
    """Manually trigger a job scrape. Returns immediately, runs async."""
    import asyncio
    import threading

    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(run_all_scrapers())

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return {"ok": True, "message": "Scraping started in background"}


@app.post("/api/digest")
def trigger_digest():
    """Manually trigger the daily digest email."""
    count = run_daily_digest(force=True)
    return {"ok": True, "jobs_sent": count}


@app.get("/api/settings")
def get_settings():
    theme = get_setting("theme", "light")
    if theme == "trailmap":
        theme = "trailblazer"
    return {
        "digest_email": get_setting("digest_email", ""),
        "theme": theme,
    }


@app.put("/api/settings")
def update_settings(
    digest_email: str | None = Form(None),
    theme: str | None = Form(None),
):
    response = {"ok": True}

    if digest_email is not None:
        email = digest_email.strip()
        if "@" not in email or "." not in email.split("@")[-1]:
            raise HTTPException(status_code=400, detail="Invalid email address")
        set_setting("digest_email", email)
        response["digest_email"] = email

    if theme is not None:
        theme_value = theme.strip().lower()
        if theme_value == "trailmap":
            theme_value = "trailblazer"
        if theme_value not in {"light", "dark", "trailblazer", "stargazer"}:
            raise HTTPException(status_code=400, detail="Invalid theme")
        set_setting("theme", theme_value)
        response["theme"] = theme_value

    if len(response) == 1:
        raise HTTPException(status_code=400, detail="No settings provided")

    return response


@app.post("/api/jobs/add")
def add_job_manual(
    title: str = Form(...),
    company: str = Form(...),
    location: str = Form(""),
    location_type: str = Form("remote"),
    url: str = Form(""),
    source: str = Form("manual"),
    category: str = Form("other"),
    salary_range: str = Form(""),
    description: str = Form(""),
):
    db = get_db()
    try:
        resolved_description, description_source = build_job_description(url, description)
        score = score_job(title, resolved_description, company)
        try:
            cat = JobCategory(category)
        except ValueError:
            cat = JobCategory.OTHER

        try:
            lt = JobLocation(location_type)
        except ValueError:
            lt = JobLocation.REMOTE

        job = Job(
            title=title,
            company=company,
            location=location,
            location_type=lt,
            url=url,
            description=resolved_description,
            salary_range=salary_range,
            source=source,
            category=cat,
            match_score=score,
        )
        db.add(job)
        db.commit()
        return {
            "ok": True,
            "id": job.id,
            "match_score": score,
            "description_source": description_source,
        }
    finally:
        db.close()


@app.post("/api/jobs/{job_id}/recalculate-match")
def recalculate_job_match(job_id: int):
    db = get_db()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        old_score = job.match_score
        old_description = job.description or ""
        fetched_description = fetch_job_page_description(job.url.strip()) if job.url and job.url.strip() else ""
        if fetched_description:
            resolved_description = fetched_description
            description_source = "url"
        elif old_description:
            resolved_description = old_description
            description_source = "saved"
        else:
            resolved_description = ""
            description_source = "none"

        if fetched_description and resolved_description != old_description:
            job.description = resolved_description

        job.match_score = score_job(job.title, resolved_description, job.company)
        db.commit()

        detail = f"Match recalculated: {old_score:.0f}% → {job.match_score:.0f}%"
        if description_source == "url":
            detail += " using job page description"
        elif description_source == "saved":
            detail += " using saved description"
        log_activity(job_id, "match_recalculated", detail, f"{old_score:.1f}", f"{job.match_score:.1f}")

        return {
            "ok": True,
            "match_score": job.match_score,
            "description_source": description_source,
            "description_updated": resolved_description != old_description,
        }
    finally:
        db.close()


@app.post("/api/jobs/{job_id}/feedback")
def add_feedback(
    job_id: int,
    action: str = Form(...),
    detail: str = Form(""),
    liked: str = Form(""),
    disliked: str = Form(""),
):
    """Log feedback on a job for tuning the matcher.
    Supports combined liked+disliked in one call.
    Deduplicates: repeated same-action clicks are ignored."""
    db = get_db()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        # Handle legacy single-action calls
        if action in ("liked", "not_interested") and not liked and not disliked:
            liked = detail if action == "liked" else ""
            disliked = detail if action == "not_interested" else ""

        # Build new notes from combined feedback
        new_notes_parts = []
        if job.notes:
            # Remove old feedback markers but keep other notes
            import re
            base = re.sub(r'\s*\|?\s*(LIKED:|NOT_INTERESTED:)[^|]*(\||$)', '', job.notes).strip()
            if base:
                new_notes_parts.append(base)

        if liked.strip():
            new_notes_parts.append(f"LIKED: {liked.strip()}")
        if disliked.strip():
            new_notes_parts.append(f"NOT_INTERESTED: {disliked.strip()}")

        job.notes = " | ".join(new_notes_parts) if new_notes_parts else ""

        db.commit()  # Commit first so log_activity doesn't deadlock

        # Log activities after commit
        if liked.strip():
            existing = db.query(ActivityLog).filter(
                ActivityLog.job_id == job_id,
                ActivityLog.action == "liked",
            ).first()
            if not existing:
                log_activity(job_id, "liked", liked.strip())

        if disliked.strip():
            existing = db.query(ActivityLog).filter(
                ActivityLog.job_id == job_id,
                ActivityLog.action == "not_interested",
            ).first()
            if not existing:
                log_activity(job_id, "not_interested", disliked.strip())

        has_feedback = bool(liked.strip() or disliked.strip())
        return {"ok": True, "has_feedback": has_feedback}
    finally:
        db.close()


# ─── API: Job Sources ───

@app.get("/api/sources")
def list_sources():
    db = get_db()
    try:
        sources = db.query(JobSource).order_by(JobSource.name).all()
        return [{
            "id": s.id,
            "name": s.name,
            "url": s.url,
            "source_type": s.source_type,
            "is_enabled": s.is_enabled,
            "search_query": s.search_query,
            "notes": s.notes,
            "created_at": s.created_at.isoformat() if s.created_at else None,
        } for s in sources]
    finally:
        db.close()


@app.post("/api/sources")
def add_source(
    name: str = Form(...),
    url: str = Form(""),
    source_type: str = Form("company_page"),
    search_query: str = Form(""),
    notes: str = Form(""),
):
    db = get_db()
    try:
        source = JobSource(
            name=name,
            url=url,
            source_type=source_type,
            search_query=search_query,
            notes=notes,
            is_enabled=True,
        )
        db.add(source)
        db.commit()
        return {"ok": True, "id": source.id}
    finally:
        db.close()


@app.put("/api/sources/{source_id}")
def update_source(
    source_id: int,
    name: str | None = Form(None),
    url: str | None = Form(None),
    source_type: str | None = Form(None),
    is_enabled: bool | None = Form(None),
    search_query: str | None = Form(None),
    notes: str | None = Form(None),
):
    db = get_db()
    try:
        source = db.query(JobSource).filter(JobSource.id == source_id).first()
        if not source:
            raise HTTPException(status_code=404, detail="Source not found")
        if name is not None:
            source.name = name
        if url is not None:
            source.url = url
        if source_type is not None:
            source.source_type = source_type
        if is_enabled is not None:
            source.is_enabled = is_enabled
        if search_query is not None:
            source.search_query = search_query
        if notes is not None:
            source.notes = notes
        db.commit()
        return {"ok": True}
    finally:
        db.close()


@app.delete("/api/sources/{source_id}")
def delete_source(source_id: int):
    db = get_db()
    try:
        source = db.query(JobSource).filter(JobSource.id == source_id).first()
        if not source:
            raise HTTPException(status_code=404, detail="Source not found")
        db.delete(source)
        db.commit()
        return {"ok": True}
    finally:
        db.close()


@app.put("/api/sources/{source_id}/toggle")
def toggle_source(source_id: int):
    db = get_db()
    try:
        source = db.query(JobSource).filter(JobSource.id == source_id).first()
        if not source:
            raise HTTPException(status_code=404, detail="Source not found")
        source.is_enabled = not source.is_enabled
        db.commit()
        return {"ok": True, "is_enabled": source.is_enabled}
    finally:
        db.close()


@app.get("/api/stats")
def get_stats():
    """Dashboard stats."""
    db = get_db()
    try:
        total_jobs = db.query(Job).filter(Job.is_active == True, Job.is_excluded == False).count()
        pmm_jobs = db.query(Job).filter(Job.is_active == True, Job.is_excluded == False, Job.category == JobCategory.PMM).count()
        new_today = db.query(Job).filter(
            Job.discovered_at >= datetime.now(timezone.utc) - timedelta(days=1),
            Job.is_active == True,
        ).count()

        # Application stage counts
        stage_counts = {}
        apps = db.query(Application).join(Job).filter(Job.is_active == True).all()
        for app in apps:
            stage = app.stage.value
            stage_counts[stage] = stage_counts.get(stage, 0) + 1

        return {
            "total_jobs": total_jobs,
            "pmm_jobs": pmm_jobs,
            "new_today": new_today,
            "stage_counts": stage_counts,
        }
    finally:
        db.close()


# ─── Static files ───
STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
