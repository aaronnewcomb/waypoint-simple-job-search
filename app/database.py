from sqlalchemy import create_engine, String, Integer, Float, Boolean, DateTime, Text, Enum as SAEnum, ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, Session
from datetime import datetime, timezone
import enum
import os

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "jobs.db")
engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)


class Base(DeclarativeBase):
    pass


class JobCategory(str, enum.Enum):
    PMM = "pmm"
    EDUCATION = "education"
    IT_SUPPORT = "it_support"
    NONPROFIT_MARKETING = "nonprofit_marketing"
    OTHER = "other"


class ApplicationStage(str, enum.Enum):
    SAVED = "saved"
    APPLIED = "applied"
    PHONE_SCREEN = "phone_screen"
    INTERVIEW_1 = "interview_1"
    INTERVIEW_2 = "interview_2"
    INTERVIEW_FINAL = "interview_final"
    OFFER = "offer"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"
    NOT_INTERESTED = "not_interested"


class JobLocation(str, enum.Enum):
    REMOTE = "remote"
    HYBRID = "hybrid"
    ONSITE = "onsite"


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    company: Mapped[str] = mapped_column(String(200))
    location: Mapped[str] = mapped_column(String(200), default="")
    location_type: Mapped[JobLocation] = mapped_column(default=JobLocation.REMOTE)
    url: Mapped[str] = mapped_column(String(1000))
    description: Mapped[str] = mapped_column(Text, default="")
    salary_range: Mapped[str] = mapped_column(String(100), default="")
    source: Mapped[str] = mapped_column(String(50))  # linkedin, indeed, company_page
    category: Mapped[JobCategory] = mapped_column(default=JobCategory.OTHER)
    match_score: Mapped[float] = mapped_column(Float, default=0.0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_stale: Mapped[bool] = mapped_column(Boolean, default=False)
    is_excluded: Mapped[bool] = mapped_column(Boolean, default=False)
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    notes: Mapped[str] = mapped_column(Text, default="")

    application: Mapped["Application | None"] = relationship(
        back_populates="job", uselist=False, cascade="all, delete-orphan"
    )
    contacts: Mapped[list["Contact"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )
    activities: Mapped[list["ActivityLog"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="ActivityLog.created_at.desc()"
    )


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), unique=True)
    stage: Mapped[ApplicationStage] = mapped_column(default=ApplicationStage.SAVED)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    stage_updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    resume_version: Mapped[str] = mapped_column(String(50), default="")
    notes: Mapped[str] = mapped_column(Text, default="")

    job: Mapped[Job] = relationship(back_populates="application")


class Contact(Base):
    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"))
    name: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(200), default="")
    email: Mapped[str] = mapped_column(String(200), default="")
    linkedin_url: Mapped[str] = mapped_column(String(500), default="")
    role: Mapped[str] = mapped_column(String(100), default="")  # recruiter, hiring_manager, interviewer
    notes: Mapped[str] = mapped_column(Text, default="")

    job: Mapped[Job] = relationship(back_populates="contacts")
    interactions: Mapped[list["Interaction"]] = relationship(
        back_populates="contact", cascade="all, delete-orphan"
    )


class InteractionType(str, enum.Enum):
    EMAIL = "email"
    LINKEDIN_MSG = "linkedin_msg"
    PHONE = "phone"
    INTERVIEW = "interview"
    OTHER = "other"


class Interaction(Base):
    __tablename__ = "interactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id"))
    type: Mapped[InteractionType] = mapped_column(default=InteractionType.OTHER)
    happened_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    summary: Mapped[str] = mapped_column(Text, default="")
    follow_up_needed: Mapped[bool] = mapped_column(Boolean, default=False)
    follow_up_date: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    thank_you_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    thank_you_sent_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)

    contact: Mapped[Contact] = relationship(back_populates="interactions")


class DigestLog(Base):
    __tablename__ = "digest_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    job_count: Mapped[int] = mapped_column(Integer, default=0)
    job_ids: Mapped[str] = mapped_column(Text, default="[]")  # JSON list of job IDs


class ActivityLog(Base):
    __tablename__ = "activity_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(100))  # stage_change, note_added, contact_added, not_interested, etc.
    detail: Mapped[str] = mapped_column(Text, default="")  # Human-readable description
    old_value: Mapped[str | None] = mapped_column(String(500), nullable=True)
    new_value: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    job: Mapped[Job | None] = relationship(back_populates="activities")


class JobSource(Base):
    __tablename__ = "job_sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))          # Display name, e.g. "IBM Careers"
    url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    source_type: Mapped[str] = mapped_column(String(50), default="company_page")  # linkedin, indeed, company_page
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    search_query: Mapped[str] = mapped_column(String(300), default="")  # Search term for scraping
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    notes: Mapped[str] = mapped_column(Text, default="")


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


def get_db() -> Session:
    return Session(engine)


def init_db():
    Base.metadata.create_all(engine)
    db = Session(engine)
    try:
        if db.query(JobSource).count() == 0:
            db.add_all([
                JobSource(name="LinkedIn Jobs", source_type="linkedin", is_enabled=True, notes="Primary source — searches for PMM and other roles"),
                JobSource(name="Indeed", source_type="indeed", is_enabled=True, notes="Secondary aggregator"),
            ])

        if not db.query(AppSetting).filter(AppSetting.key == "digest_email").first():
            db.add(AppSetting(key="digest_email", value=""))
        if not db.query(AppSetting).filter(AppSetting.key == "theme").first():
            db.add(AppSetting(key="theme", value="light"))

        db.commit()
    finally:
        db.close()


def get_setting(key: str, default: str = "") -> str:
    db = Session(engine)
    try:
        setting = db.query(AppSetting).filter(AppSetting.key == key).first()
        return setting.value if setting and setting.value is not None else default
    finally:
        db.close()


def set_setting(key: str, value: str) -> None:
    db = Session(engine)
    try:
        setting = db.query(AppSetting).filter(AppSetting.key == key).first()
        if setting:
            setting.value = value
        else:
            db.add(AppSetting(key=key, value=value))
        db.commit()
    finally:
        db.close()


def log_activity(job_id: int | None, action: str, detail: str = "", old_value: str | None = None, new_value: str | None = None):
    """Log an activity to the activity_logs table."""
    db = Session(engine)
    try:
        entry = ActivityLog(
            job_id=job_id,
            action=action,
            detail=detail,
            old_value=old_value,
            new_value=new_value,
        )
        db.add(entry)
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()
