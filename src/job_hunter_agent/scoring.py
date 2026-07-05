import re

from .types import JobRecord

# Core skills from the resume. Weight = how strongly it signals a close match.
SKILL_WEIGHTS = {
    "intune": 3, "entra": 3, "azure ad": 3, "conditional access": 3,
    "microsoft 365": 3, "m365": 3, "office 365": 2, "modern workplace": 3,
    "purview": 3, "iam": 3, "identity and access": 3, "identity engineer": 3,
    "zero trust": 2, "defender for endpoint": 3, "defender": 2,
    "defender for office 365": 3, "copilot data security": 3, "ediscovery": 3,
    "sentinelone": 2, "mimecast": 2, "dlp": 2, "retention": 2,
    "powershell": 2, "graph api": 2, "autopilot": 2, "jamf": 2,
    "active directory": 2, "exchange online": 2, "sharepoint": 1,
    "endpoint management": 2, "mdm": 2, "azure": 1, "security engineer": 2,
    "sso": 1, "saml": 1, "pim": 1, "mfa": 1, "itil": 1, "servicenow": 1,
    "workspace one": 1, "citrix": 1, "avd": 1, "fslogix": 1, "vdi": 1,
    "halo itsm": 1, "jira service management": 1, "watchguard": 1,
    "juniper srx": 1, "wireshark": 1, "windows server": 1, "iis": 1,
    "dns": 1, "dhcp": 1, "rbac": 1, "entitlement management": 2,
    "okta": 1, "sailpoint": 1, "cyberark": 1,
}

# Role-title patterns worth surfacing even before skill scoring.
TITLE_PATTERNS = re.compile(
    r"(modern workplace|workplace engineer|m365|microsoft 365|office 365|"
    r"intune|entra|identity|iam\b|access management|endpoint|"
    r"security engineer|cyber ?security engineer|cloud security|"
    r"infrastructure engineer|systems? engineer|azure engineer|"
    r"it engineer|desktop engineer|euc\b|end user computing|device engineer)",
    re.I,
)
# Exclude clearly non-target roles even if the description contains matching terms.
EXCLUDED_TITLE_PATTERNS = re.compile(
    r"(account executive|sales|recruiter|talent acquisition|hr\b|human resources|"
    r"marketing|country manager|business development|customer success|"
    r"executive assistant|bid manager|account manager|partnerships?|"
    r"organizing strategist|people operations)",
    re.I,
)

# Mobility: role must offer at least one of these.
VISA_TERMS = [
    "visa sponsorship", "sponsorship available", "sponsor visa", "work permit",
    "relocation", "relocate", "highly skilled migrant", "skilled worker visa",
    "work visa", "immigration support", "kennismigrant", "blue card",
]
ANYWHERE_TERMS = [
    "work from anywhere", "fully remote", "remote worldwide", "100% remote",
    "remote - global", "remote (global)", "anywhere in the world",
]
EMEA_REMOTE_TERMS = [
    "remote emea", "emea remote", "remote - emea", "remote (emea)",
    "remote in emea", "remote within emea", "work from anywhere in emea",
    "anywhere in emea",
]
TARGET_COUNTRIES = [
    "netherlands", "united kingdom", "uk", "ireland", "germany", "canada",
    "new zealand", "nz", "auckland", "wellington",
    "australia", "sydney", "melbourne", "brisbane", "perth",
    "dubai", "united arab emirates", "uae", "qatar", "saudi", "sweden",
    "denmark", "norway", "switzerland", "belgium", "luxembourg", "amsterdam",
    "london", "berlin", "toronto", "vancouver", "doha", "abu dhabi", "emea",
]


def score_text(title: str, body: str) -> tuple[int, list[str]]:
    text = f"{title}\n{body}".lower()
    score, hits = 0, []
    for skill, weight in SKILL_WEIGHTS.items():
        if skill in text:
            score += weight
            hits.append(skill)
    if TITLE_PATTERNS.search(title):
        score += 3
    return score, hits


def mobility(title: str, body: str, location: str) -> list[str]:
    text = f"{title}\n{body}\n{location}".lower()
    tags: list[str] = []
    if any(term in text for term in VISA_TERMS):
        tags.append("visa/relocation")
    has_work_anywhere = any(term in text for term in ANYWHERE_TERMS)
    has_emea_remote = (
        any(term in text for term in EMEA_REMOTE_TERMS)
        or ("emea" in text and "remote" in text)
    )
    if has_work_anywhere or has_emea_remote:
        tags.append("work-anywhere")
    elif "remote" in text:
        tags.append("remote")
    if has_emea_remote:
        tags.append("emea-remote")
    if any(country in location.lower() for country in TARGET_COUNTRIES):
        tags.append("target-country")
    return tags


def add_job(
    jobs: list[JobRecord],
    source: str,
    company: str,
    title: str,
    location: str,
    url: str,
    body: str,
    posted: str = "",
) -> None:
    if EXCLUDED_TITLE_PATTERNS.search(title or ""):
        return
    score, hits = score_text(title, body)
    title_is_match = bool(TITLE_PATTERNS.search(title or ""))
    if score < 1:
        return
    # Keep relevance high: if title is weakly related, require stronger skill evidence.
    if not title_is_match and score < 5:
        return
    tags = mobility(title, body, location)
    # Keep only roles with a mobility angle OR a very strong skill match
    # in a target country.
    if not tags and score < 6:
        return
    jobs.append(
        {
            "score": score,
            "source": source,
            "company": company,
            "title": (title or "").strip(),
            "location": (location or "").strip() or "n/a",
            "url": url or "",
            "tags": ", ".join(tags) or "check posting",
            "skills": ", ".join(hits[:8]),
            "posted": posted or "",
        }
    )
