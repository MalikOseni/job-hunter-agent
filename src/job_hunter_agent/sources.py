from __future__ import annotations
import html
import json
import re
import sys
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import urllib.request
from urllib.parse import urlencode
from typing import Any
from xml.etree import ElementTree

from .scoring import add_job
from .types import JobRecord

UA = {"User-Agent": "Mozilla/5.0 (job-hunter/1.0)"}
LINKEDIN_GUEST_SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
LINKEDIN_KEYWORD_LOCATION_PAIRS = (
    ("identity engineer visa sponsorship", "Remote"),
    ("microsoft 365 security engineer relocation", "Remote"),
    ("intune engineer relocation", "Remote"),
)

# Companies with known ATS boards worth polling directly. Extend freely:
# ("greenhouse", "boardtoken") | ("lever", "site") | ("ashby", "org")
ATS_BOARDS = [
    # Core targets aligned to identity/security/modern workplace depth.
    ("greenhouse", "okta"),
    ("greenhouse", "cloudflare"),
    ("greenhouse", "datadog"),
    ("greenhouse", "mongodb"),
    ("greenhouse", "gitlab"),
    ("greenhouse", "elastic"),
    ("greenhouse", "adyen"),
    # Additional tech employers to reduce concentration on a single company.
    ("greenhouse", "stripe"),
    ("greenhouse", "reddit"),
    ("greenhouse", "twilio"),
    ("greenhouse", "intercom"),
    ("greenhouse", "coinbase"),
    ("greenhouse", "dropbox"),
    ("greenhouse", "figma"),
    ("greenhouse", "duolingo"),
    # Non-profit / mission-driven organizations.
    ("greenhouse", "mozilla"),
    ("greenhouse", "khanacademy"),
    ("greenhouse", "codeforamerica"),
    ("greenhouse", "aclu"),
    ("greenhouse", "humanrightswatch"),
    ("greenhouse", "wri"),
    # Additional ATS ecosystems.
    ("lever", "palantir"),
    ("lever", "netlight"),
    ("ashby", "ramp"),
    ("ashby", "deel"),
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
    for category in ("software-dev", "devops-sysadmin", "all-others"):
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

def sweep_weworkremotely(jobs: list[JobRecord]) -> None:
    print("WeWorkRemotely (niche remote board)...")
    raw = fetch("https://weworkremotely.com/remote-jobs.rss")
    if not raw:
        return
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError:
        return
    for item in root.findall("./channel/item"):
        title = _xml_text(item.find("title"))
        url = _xml_text(item.find("link"))
        description = html.unescape(_xml_text(item.find("description")))
        category_blob = " ".join(
            _xml_text(category) for category in item.findall("category")
        ).strip()
        posted = _normalize_posted_date(_xml_text(item.find("pubDate")))
        company, role_title = _split_company_and_title(title)
        body = " ".join(piece for piece in (description, category_blob) if piece).strip()
        add_job(
            jobs,
            "weworkremotely",
            company,
            role_title,
            "Remote",
            url,
            body,
            posted,
        )


def sweep_linkedin_guest(jobs: list[JobRecord]) -> None:
    print("LinkedIn guest search (mobility-filtered)...")
    for keywords, location in LINKEDIN_KEYWORD_LOCATION_PAIRS:
        query = urlencode(
            {
                "keywords": keywords,
                "location": location,
                "f_TPR": "r604800",
                "position": 1,
                "pageNum": 0,
                "start": 0,
            }
        )
        raw = fetch(f"{LINKEDIN_GUEST_SEARCH_URL}?{query}")
        if not raw:
            continue
        for card in _parse_linkedin_cards(raw):
            add_job(
                jobs,
                "linkedin/guest",
                card["company"],
                card["title"],
                card["location"] or location,
                card["url"],
                card["body"],
                card["posted"],
            )
        time.sleep(0.4)


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
    sweep_weworkremotely(jobs)
    sweep_linkedin_guest(jobs)
    sweep_ats(jobs)


def _xml_text(element: ElementTree.Element | None) -> str:
    if element is None:
        return ""
    return (element.text or "").strip()


def _split_company_and_title(raw_title: str) -> tuple[str, str]:
    title = (raw_title or "").strip()
    for separator in ("—", "–", "-", "|", ":"):
        if separator in title:
            left, right = title.split(separator, 1)
            company = left.strip() or "unknown"
            role_title = right.strip() or title
            return company, role_title
    return "unknown", title


def _normalize_posted_date(raw_value: str) -> str:
    text = (raw_value or "").strip()
    if not text:
        return ""
    if re.match(r"^\d{4}-\d{2}-\d{2}$", text):
        return text
    if re.match(r"^\d{4}-\d{2}-\d{2}T", text):
        return text[:10]
    if "ago" in text.lower():
        return datetime.now(timezone.utc).date().isoformat()
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).date().isoformat()


def _parse_linkedin_cards(raw_html: str) -> list[dict[str, str]]:
    cards: list[dict[str, str]] = []
    for match in re.finditer(r"<li[\s\S]*?</li>", raw_html):
        block = match.group(0)
        url = _extract_first(block, r'href="([^"]+)"')
        title = _strip_html(_extract_first(block, r'base-search-card__title[^>]*>([\s\S]*?)</h3>'))
        company = _strip_html(_extract_first(block, r'base-search-card__subtitle[^>]*>([\s\S]*?)</h4>'))
        location = _strip_html(_extract_first(block, r'job-search-card__location[^>]*>([\s\S]*?)</span>'))
        datetime_attr = _extract_first(block, r'<time[^>]*datetime="([^"]+)"')
        posted_text = datetime_attr or _strip_html(_extract_first(block, r'<time[^>]*>([\s\S]*?)</time>'))
        posted = _normalize_posted_date(posted_text)
        body = " ".join(piece for piece in (title, company, location) if piece).strip()
        if not url or not title:
            continue
        cards.append(
            {
                "url": html.unescape(url).strip(),
                "title": title,
                "company": company or "unknown",
                "location": location,
                "body": body,
                "posted": posted,
            }
        )
    return cards


def _extract_first(text: str, pattern: str) -> str:
    match = re.search(pattern, text, flags=re.I)
    if not match:
        return ""
    return match.group(1).strip()


def _strip_html(raw_value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", raw_value or "")
    return " ".join(html.unescape(text).split())
