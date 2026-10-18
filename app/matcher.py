"""
Resume matching engine. Scores jobs against a local resume profile using keyword matching.
"""
import yaml
import os
import re
from typing import Optional

RESUME_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "resume.yaml")

_resume_cache: Optional[dict] = None


def load_resume() -> dict:
    global _resume_cache
    if _resume_cache is None:
        if os.path.exists(RESUME_PATH):
            with open(RESUME_PATH, encoding="utf-8") as f:
                _resume_cache = yaml.safe_load(f) or {}
        else:
            _resume_cache = {"match_keywords": {"primary": [], "secondary": []}, "skills": {}}
    return _resume_cache


def score_job(title: str, description: str, company: str = "") -> float:
    """
    Score a job (0-100) based on how well it matches the local resume profile.
    Returns a float where higher = better match.
    """
    resume = load_resume()
    keywords_primary = resume.get("match_keywords", {}).get("primary", [])
    keywords_secondary = resume.get("match_keywords", {}).get("secondary", [])

    text = f"{title} {description}".lower()
    company_lower = company.lower()

    score = 0.0

    # Reject penalty: learn from past "not interested" reasons
    penalty = get_reject_penalty(title, description, company)
    # Like boost: learn from past "liked" reasons
    boost = get_like_boost(title, description, company)

    # Company prestige bonus
    enterprise_companies = [
        "harness", "sysdig", "appdynamics", "new relic", "netapp", "oracle",
        "sun microsystems", "procter & gamble", "upbound", "datadog", "splunk",
        "dynatrace", "elastic", "grafana", "honeycomb", "chronosphere",
        "google", "microsoft", "amazon", "aws", "meta", "apple", "netflix",
        "salesforce", "servicenow", "snowflake", "mongodb", "confluent",
        "hashicorp", "red hat", "ibm", "cisco", "vmware", "palo alto networks",
        "crowdstrike", "okta", "twilio", "stripe", "square", "block",
        "gitlab", "github", "atlassian", "launchdarkly", "split.io",
    ]
    if any(c in company_lower for c in enterprise_companies):
        score += 10

    # Title bonus
    title_lower = title.lower()
    if "senior" in title_lower:
        score += 5
    if "director" in title_lower or "head" in title_lower:
        score += 7
    if "vp" in title_lower or "vice president" in title_lower:
        score += 3  # might be a stretch

    # Primary keyword matching (heavy weight)
    primary_matches = 0
    for kw in keywords_primary:
        if kw.lower() in text:
            primary_matches += 1

    if primary_matches > 0:
        score += min(primary_matches * 7, 50)  # Cap primary at 50

    # Secondary keyword matching (lighter weight)
    secondary_matches = 0
    for kw in keywords_secondary:
        if kw.lower() in text:
            secondary_matches += 1

    if secondary_matches > 0:
        score += min(secondary_matches * 3, 20)  # Cap secondary at 20

    # Skill matching
    skills_all = []
    for category_skills in resume.get("skills", {}).values():
        skills_all.extend(category_skills)

    skill_matches = 0
    for skill in skills_all:
        if skill.lower() in text:
            skill_matches += 1

    if skill_matches > 0:
        score += min(skill_matches * 2, 20)  # Cap skills at 20

    return min(round(max(score - penalty + boost, 0), 1), 100.0)


def categorize_job(title: str, description: str) -> str:
    """
    Auto-categorize a job as PMM, education, IT support, or non-profit marketing.
    Returns category value matching JobCategory enum.
    """
    text = f"{title} {description}".lower()

    # PMM patterns — stricter: title must contain explicit PMM/marketing keywords
    title_lower = title.lower()
    pmm_title_patterns = [
        r"product market(ing)?", r"\bpmm\b",
        r"market(ing)? manager", r"market(ing)? director",
        r"head of market(ing)?", r"vp.*market(ing)?",
        r"go.?to.?market", r"demand gen",
        r"content market(ing)?", r"growth market(ing)?",
        r"developer market(ing)?", r"technical market(ing)?",
        r"portfolio market(ing)?", r"solutions? market(ing)?",
        r"product market(ing)?", r"brand market(ing)?",
    ]
    for pat in pmm_title_patterns:
        if re.search(pat, title_lower):
            return "pmm"

    # Education patterns
    edu_patterns = [
        r"\bteacher\b", r"\binstructor\b", r"\beducator\b",
        r"education\b", r"curriculum\b", r"\bclassroom\b",
        r"learning (management|platform)", r"\blms\b",
        r"teaching\b", r"\bschool\b",
    ]
    for pat in edu_patterns:
        if re.search(pat, text):
            return "education"

    # IT Support patterns
    it_patterns = [
        r"\bit support\b", r"help\s?desk", r"desktop support",
        r"systems? admin(istrator)?\b", r"technical support",
        r"\bit technician\b", r"network (admin|engineer)",
        r"end.?user (support|computing)",
    ]
    for pat in it_patterns:
        if re.search(pat, text):
            return "it_support"

    # Non-profit marketing
    nonprofit_patterns = [
        r"non.?profit", r"\bnpo\b", r"nonprofit",
        r"charity", r"\bngo\b",
    ]
    for pat in nonprofit_patterns:
        if re.search(pat, text):
            return "nonprofit_marketing"

    return "other"


def get_reject_penalty(title: str, description: str, company: str) -> float:
    """
    Query the database for reject signals and apply a penalty.
    """
    try:
        from .database import get_db, Job
        db = get_db()
        try:
            text = f"{title} {description}".lower()
            penalty = 0.0

            # Get all jobs with NOT_INTERESTED notes
            jobs_with_reject = db.query(Job.notes).filter(
                Job.notes.like("%NOT_INTERESTED:%")
            ).all()

            for (notes,) in jobs_with_reject:
                if not notes:
                    continue
                # Extract the reason after NOT_INTERESTED:
                import re
                match = re.search(r'NOT_INTERESTED:\s*([^|]+)', notes)
                if not match:
                    continue
                reason = match.group(1).strip().lower()

                # Domain/industry rejection patterns
                domain_keywords = [
                    "security", "cyber", "fintech", "financial services",
                    "healthcare", "insurance", "gaming", "crypto",
                    "defense", "military", "adtech", "legal",
                ]
                for kw in domain_keywords:
                    if kw in reason and kw in text:
                        penalty += 8

                # Role mismatch
                for kw in ["not pmm", "wrong role", "not marketing", "sales", "engineering"]:
                    if kw in reason and kw in text:
                        penalty += 10

                # Seniority mismatch
                if "too senior" in reason and ("junior" in title.lower() or "associate" in title.lower()):
                    penalty += 5

                # Company-specific
                if "company" in reason and company.lower() in reason:
                    penalty += 15

            return min(penalty, 40.0)

        finally:
            db.close()
    except Exception:
        return 0.0


def get_like_boost(title: str, description: str, company: str) -> float:
    """
    Query the database for positive signals and boost score.
    """
    try:
        from .database import get_db, Job
        db = get_db()
        try:
            text = f"{title} {description} {company}".lower()
            boost = 0.0

            # Get all jobs with LIKED notes
            jobs_with_likes = db.query(Job.notes).filter(
                Job.notes.like("%LIKED:%")
            ).all()

            for (notes,) in jobs_with_likes:
                if not notes:
                    continue
                import re
                match = re.search(r'LIKED:\s*([^|]+)', notes)
                if not match:
                    continue
                reason = match.group(1).strip().lower()

                # Domain/industry likes
                liked_domains = [
                    "devtools", "developer tools", "cloud", "infrastructure",
                    "observability", "monitoring", "ai", "machine learning",
                    "open source", "saas", "enterprise",
                ]
                for domain in liked_domains:
                    if domain in reason and domain in text:
                        boost += 5

                # Role fit
                if any(w in reason for w in ["great role", "perfect fit", "great fit"]):
                    if "product marketing" in text or "pmm" in text:
                        boost += 4

                # Location
                if "location" in reason:
                    if "remote" in reason and "remote" in text:
                        boost += 3
                    if "san francisco" in reason and "san francisco" in text:
                        boost += 3

                # Tech stack
                for tech in ["kubernetes", "prometheus", "docker", "terraform", "aws", "gcp", "azure", "linux"]:
                    if tech in reason and tech in text:
                        boost += 4

            return min(boost, 25.0)

        finally:
            db.close()
    except Exception:
        return 0.0
