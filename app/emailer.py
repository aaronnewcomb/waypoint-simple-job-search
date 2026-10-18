"""
Email digest module. Sends daily job digest via Gmail using gog CLI.
"""
import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone, timedelta
from .database import get_db, init_db, Job, DigestLog, get_setting


def build_digest_html(pmm_jobs: list[Job], other_jobs: list[Job]) -> str:
    """Build HTML email content for the daily digest."""

    def job_html(job: Job) -> str:
        score = f"{job.match_score:.0f}%" if job.match_score else "—"
        return f"""
        <tr>
            <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb">
                <a href="{job.url}" style="color:#2563eb;text-decoration:none;font-weight:600">{job.title}</a>
            </td>
            <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb">{job.company}</td>
            <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb">{job.location}</td>
            <td style="padding:8px 12px;border-bottom:1px solid #e5e7eb;text-align:center">
                <span style="background:#dbeafe;color:#1e40af;padding:2px 8px;border-radius:12px;font-size:12px">{score}</span>
            </td>
        </tr>"""

    pmm_rows = "".join(job_html(j) for j in pmm_jobs) if pmm_jobs else '<tr><td colspan="4" style="padding:12px;color:#6b7280;text-align:center">No new PMM jobs today</td></tr>'
    other_rows = "".join(job_html(j) for j in other_jobs) if other_jobs else '<tr><td colspan="4" style="padding:12px;color:#6b7280;text-align:center">No new other jobs today</td></tr>'

    return f"""
    <html>
    <body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#f9fafb;padding:20px">
    <div style="max-width:700px;margin:0 auto;background:white;border-radius:12px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,0.1)">
        <div style="background:#2563eb;color:white;padding:24px">
            <h1 style="margin:0;font-size:24px">🔍 Daily Job Digest</h1>
            <p style="margin:8px 0 0;opacity:0.9">{datetime.now().strftime('%A, %B %d, %Y')}</p>
        </div>
        <div style="padding:24px">
            <h2 style="color:#1f2937;font-size:18px;margin:0 0 12px">📊 Product Marketing Jobs ({len(pmm_jobs)})</h2>
            <table style="width:100%;border-collapse:collapse;margin-bottom:24px">
                <tr style="background:#f3f4f6">
                    <th style="padding:8px 12px;text-align:left;font-size:13px;color:#6b7280">Title</th>
                    <th style="padding:8px 12px;text-align:left;font-size:13px;color:#6b7280">Company</th>
                    <th style="padding:8px 12px;text-align:left;font-size:13px;color:#6b7280">Location</th>
                    <th style="padding:8px 12px;text-align:center;font-size:13px;color:#6b7280">Match</th>
                </tr>
                {pmm_rows}
            </table>

            <h2 style="color:#1f2937;font-size:18px;margin:0 0 12px">🔖 Other Jobs ({len(other_jobs)})</h2>
            <table style="width:100%;border-collapse:collapse;margin-bottom:24px">
                <tr style="background:#f3f4f6">
                    <th style="padding:8px 12px;text-align:left;font-size:13px;color:#6b7280">Title</th>
                    <th style="padding:8px 12px;text-align:left;font-size:13px;color:#6b7280">Company</th>
                    <th style="padding:8px 12px;text-align:left;font-size:13px;color:#6b7280">Location</th>
                    <th style="padding:8px 12px;text-align:center;font-size:13px;color:#6b7280">Match</th>
                </tr>
                {other_rows}
            </table>

            <div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:16px;margin-top:24px">
                <p style="margin:0;color:#166534;font-size:14px">
                    📋 <strong>Manage your jobs:</strong> Open your Waypoint dashboard to track applications, update stages, and manage contacts.
                </p>
            </div>
        </div>
    </div>
    </body>
    </html>"""


def send_digest_email(html_content: str, to_email: str | None = None):
    """Send digest email via gog CLI."""
    subject = f"🔍 Job Digest — {datetime.now().strftime('%b %d, %Y')}"

    # Read password from env or file
    keyring_pass = os.environ.get("GOG_KEYRING_PASSWORD", "")
    gog_account = os.environ.get("GOG_ACCOUNT", "")
    recipient = to_email or get_setting("digest_email", "")

    if not gog_account or not recipient:
        print("Email digest is not configured. Set GOG_ACCOUNT and choose a recipient in Settings.")
        return False

    # Write HTML to temp file for gog
    with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False) as f:
        f.write(html_content)
        tmp_path = f.name

    env = os.environ.copy()
    env["GOG_ACCOUNT"] = gog_account
    if keyring_pass:
        env["GOG_KEYRING_PASSWORD"] = keyring_pass

    try:
        # Read HTML for body-html argument
        with open(tmp_path) as fh:
            html_body = fh.read()

        # gog gmail send with HTML body
        result = subprocess.run(
            [
                "gog", "gmail", "send",
                "--to", recipient,
                "--subject", subject,
                "--body-html", html_body,
            ],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        if result.returncode != 0:
            print(f"gog send failed: {result.stderr}")
            return False
        return True
    except Exception as e:
        print(f"Email send error: {e}")
        return False
    finally:
        os.unlink(tmp_path)


def run_daily_digest(force: bool = False):
    """Main entry point: find new jobs since last digest, send email, log it."""
    init_db()
    db = get_db()

    try:
        # Find the last digest to determine "new since then"
        last_digest = db.query(DigestLog).order_by(DigestLog.sent_at.desc()).first()
        since = last_digest.sent_at if last_digest else datetime.now(timezone.utc) - timedelta(days=1)

        # Get new jobs since last digest
        new_jobs = db.query(Job).filter(
            Job.discovered_at > since,
            Job.is_active == True,
            Job.is_excluded == False,
        ).order_by(Job.match_score.desc()).all()

        if not new_jobs and not force:
            print("No new jobs since last digest.")
            db.close()
            return 0

        if not new_jobs and force:
            new_jobs = db.query(Job).filter(
                Job.is_active == True,
                Job.is_excluded == False,
            ).order_by(Job.match_score.desc()).limit(25).all()
            if not new_jobs:
                print("No jobs available for test digest.")
                db.close()
                return 0

        # Split into PMM and other
        pmm_jobs = [j for j in new_jobs if j.category == Job.__dict__.get("PMM", "pmm") or j.category.value == "pmm"]

        # Use the enum values
        pmm_jobs = [j for j in new_jobs if j.category.value == "pmm"]
        other_jobs = [j for j in new_jobs if j.category.value != "pmm"]

        # Build and send
        html = build_digest_html(pmm_jobs, other_jobs)
        success = send_digest_email(html)

        if success:
            # Log the digest
            job_ids = json.dumps([j.id for j in new_jobs])
            log = DigestLog(
                sent_at=datetime.now(timezone.utc),
                job_count=len(new_jobs),
                job_ids=job_ids,
            )
            db.add(log)
            db.commit()
            print(f"Digest sent with {len(new_jobs)} jobs ({len(pmm_jobs)} PMM, {len(other_jobs)} other).")
            return len(new_jobs)
        else:
            print("Digest email failed to send.")

    except Exception as e:
        db.rollback()
        print(f"Digest error: {e}")
        raise
    finally:
        db.close()

    return 0


if __name__ == "__main__":
    run_daily_digest()
