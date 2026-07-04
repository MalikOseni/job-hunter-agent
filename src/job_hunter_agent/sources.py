from __future__ import annotations
import html
import json
import sys
import time
import urllib.request
from typing import Any

from .scoring import add_job
from .types import JobRecord

UA = {"User-Agent": "Mozilla/5.0 (job-hunter/1.0)"}

# Companies with known ATS boards worth polling directly. Extend freely:
# ("greenhouse", "boardtoken") | ("lever", "site") | ("ashby", "org")
ATS_BOARDS = [
    ("greenhouse", "adyen"), ("greenhouse", "bookingcom"),
    ("greenhouse", "mollie"), ("greenhouse", "backbase"),
    ("greenhouse", "miro"), ("greenhouse", "elastic"),
    ("greenhouse", "gitlab"), ("greenhouse", "cloudflare"),
    ("greenhouse", "datadog"), ("greenhouse", "mongodb"),
    ("greenhouse", "okta"), ("greenhouse", "wise"),
    ("greenhouse", "checkoutcom"), ("greenhouse", "personio"),
    ("greenhouse", "picnic"), ("greenhouse", "bunq"),
    ("lever", "netlight"), ("lever", "palantir"),
    ("ashby", "ramp"), ("ashby", "deel"), ("ashby", "remotecom"),
]


def fetch(url: str, timeout: int = 25) -> str | None:
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.read().decode("utf-8", "replace")
    except Exception as exc:
        print(f"  [skip] {url[:80]} ({exc})", file=sys.stderr)
        return None


def fetch_json(url: str) -> Any:
    raw = fetch(url)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def sweep_arbeitnow(jobs: list[JobRecord]) -> None:
    print("Arbeitnow (relocation board)...")
    for page in range(1, 4):
        data = fetch_json(f"https://www.arbeitnow.com/api/job-board-api?page={page}")
        if not data:
            break
        for job in data.get("data", []):
            body = job.get("description", "")
            tags = " ".join(job.get("tags", []) + job.get("job_types", []))
            if job.get("visa_sponsorship"):
                body += "\nvisa sponsorship"
            add_job(
                jobs,
                "arbeitnow",
                job.get("company_name", ""),
                job.get("title", ""),
                job.get("location", ""),
                job.get("url", ""),
                body + " " + tags,
            )
        time.sleep(0.5)


def sweep_remotive(jobs: list[JobRecord]) -> None:
    print("Remotive (remote roles)...")
    for category in ("software-dev", "devops-sysadmin"):
        data = fetch_json(
            f"https://remotive.com/api/remote-jobs?category={category}&limit=200"
        )
        if not data:
            continue
        for job in data.get("jobs", []):
            add_job(
                jobs,
                "remotive",
                job.get("company_name", ""),
                job.get("title", ""),
                job.get("candidate_required_location", "remote"),
                job.get("url", ""),
                job.get("description", ""),
                job.get("publication_date", "")[:10],
            )


def sweep_remoteok(jobs: list[JobRecord]) -> None:
    print("RemoteOK (remote roles)...")
    data = fetch_json("https://remoteok.com/api")
    if not isinstance(data, list):
        return
    for job in data[1:]:
        if not isinstance(job, dict):
            continue
        add_job(
            jobs,
            "remoteok",
            job.get("company", ""),
            job.get("position", ""),
            job.get("location", "remote"),
            job.get("url", ""),
            (job.get("description", "") or "") + " " + " ".join(job.get("tags", [])),
            (job.get("date", "") or "")[:10],
        )


def sweep_ats(jobs: list[JobRecord]) -> None:
    print("ATS boards (Greenhouse/Lever/Ashby)...")
    for kind, token in ATS_BOARDS:
        if kind == "greenhouse":
            data = fetch_json(
                f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
            )
            if not data:
                continue
            for job in data.get("jobs", []):
                add_job(
                    jobs,
                    f"greenhouse/{token}",
                    token,
                    job.get("title", ""),
                    (job.get("location") or {}).get("name", ""),
                    job.get("absolute_url", ""),
                    html.unescape(job.get("content", "") or ""),
                    (job.get("updated_at", "") or "")[:10],
                )
        elif kind == "lever":
            data = fetch_json(f"https://api.lever.co/v0/postings/{token}?mode=json")
            if not data:
                continue
            for job in data:
                add_job(
                    jobs,
                    f"lever/{token}",
                    token,
                    job.get("text", ""),
                    (job.get("categories") or {}).get("location", ""),
                    job.get("hostedUrl", ""),
                    job.get("descriptionPlain", "") or "",
                )
        elif kind == "ashby":
            data = fetch_json(
                f"https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true"
            )
            if not data:
                continue
            for job in data.get("jobs", []):
                add_job(
                    jobs,
                    f"ashby/{token}",
                    token,
                    job.get("title", ""),
                    job.get("location", ""),
                    job.get("jobUrl", ""),
                    job.get("descriptionPlain", "") or "",
                )
        time.sleep(0.3)


def run_all_sweeps(jobs: list[JobRecord]) -> None:
    sweep_arbeitnow(jobs)
    sweep_remotive(jobs)
    sweep_remoteok(jobs)
    sweep_ats(jobs)
