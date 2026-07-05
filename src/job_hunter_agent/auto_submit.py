from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from .config import ProfileConfig
from .secrets import AccountCredentials
from .types import JobRecord

_TRUE_VALUES = {"1", "true", "yes", "on"}
_IN_PROGRESS_STAGES = {"applied", "submitted", "interview", "interview_progressed", "assessment", "offer"}


@dataclass(frozen=True)
class AutoSubmitConfig:
    enabled: bool
    max_per_run: int
    applicant_first_name: str
    applicant_last_name: str
    applicant_email: str
    applicant_phone: str
    resume_path: Path | None
    greenhouse_api_keys: dict[str, str]
    min_score_required: int = 0
    require_goal_alignment: bool = False


@dataclass(frozen=True)
class AutoSubmitOutcome:
    attempted: bool
    applied: bool
    skipped: bool
    final_stage: str
    stage_color: str
    reason_code: str
    notes: str
    external_application_id: str | None = None


@dataclass(frozen=True)
class AutoSubmitSummary:
    enabled: bool
    shortlisted_considered: int
    attempted: int
    applied: int
    blocked: int
    skipped: int

    @property
    def success_rate(self) -> float:
        if self.attempted == 0:
            return 0.0
        return round((self.applied / self.attempted) * 100, 2)


@dataclass(frozen=True)
class GreenhouseSubmissionRequest:
    board_token: str
    job_id: str
    api_key: str
    first_name: str
    last_name: str
    email: str
    phone: str
    location: str
    resume_path: Path


@dataclass(frozen=True)
class GreenhouseSubmissionResponse:
    external_application_id: str | None
    notes: str


class AutoSubmitError(RuntimeError):
    def __init__(self, reason_code: str, message: str):
        super().__init__(message)
        self.reason_code = reason_code
        self.message = message


class AutoSubmitter:
    def __init__(
        self,
        config: AutoSubmitConfig,
        *,
        greenhouse_submitter: Callable[[GreenhouseSubmissionRequest], GreenhouseSubmissionResponse] | None = None,
    ):
        self.config = config
        self._greenhouse_submitter = greenhouse_submitter or submit_greenhouse_application
        self._shortlisted_considered = 0
        self._attempted = 0
        self._applied = 0
        self._blocked = 0
        self._skipped = 0

    def summary(self) -> AutoSubmitSummary:
        return AutoSubmitSummary(
            enabled=self.config.enabled,
            shortlisted_considered=self._shortlisted_considered,
            attempted=self._attempted,
            applied=self._applied,
            blocked=self._blocked,
            skipped=self._skipped,
        )

    def maybe_submit(
        self,
        *,
        job: JobRecord,
        source: str,
        current_stage: str,
    ) -> AutoSubmitOutcome:
        self._shortlisted_considered += 1
        normalized_stage = (current_stage or "pending_review").strip().lower()
        queue_stage = _queue_stage_for_skip(normalized_stage)

        if normalized_stage in _IN_PROGRESS_STAGES:
            self._skipped += 1
            return self._skip_outcome(
                final_stage=normalized_stage,
                reason_code="auto_submit_already_progressed",
                notes="Application already progressed beyond queue stage.",
            )

        if not self.config.enabled:
            self._skipped += 1
            return self._skip_outcome(
                final_stage=queue_stage,
                reason_code="auto_submit_disabled",
                notes="Auto-submit disabled by configuration.",
            )

        if self._attempted >= self.config.max_per_run:
            self._skipped += 1
            return self._skip_outcome(
                final_stage=queue_stage,
                reason_code="auto_submit_max_per_run_reached",
                notes=f"Skipped because max-per-run limit ({self.config.max_per_run}) was reached.",
            )
        job_score = _coerce_score(job.get("score"))
        if self.config.min_score_required > 0 and job_score < self.config.min_score_required:
            self._skipped += 1
            return self._skip_outcome(
                final_stage=queue_stage,
                reason_code="auto_submit_below_quality_threshold",
                notes=(
                    f"Skipped because score {job_score} is below required threshold "
                    f"{self.config.min_score_required}."
                ),
            )
        if self.config.require_goal_alignment and not is_goal_aligned_tags(job.get("tags", "")):
            self._skipped += 1
            return self._skip_outcome(
                final_stage=queue_stage,
                reason_code="auto_submit_not_goal_aligned",
                notes=(
                    "Skipped because role lacks relocation/work-anywhere goal tags "
                    "(needs visa/relocation, work-anywhere, emea-remote, or remote+target-country)."
                ),
            )
        if not self.config.applicant_email:
            self._skipped += 1
            return self._skip_outcome(
                final_stage=queue_stage,
                reason_code="auto_submit_missing_applicant_email",
                notes="Applicant email is missing; set JOB_HUNTER_LOGIN_EMAIL or JOB_HUNTER_APPLICANT_EMAIL.",
            )
        if not self.config.applicant_first_name or not self.config.applicant_last_name:
            self._skipped += 1
            return self._skip_outcome(
                final_stage=queue_stage,
                reason_code="auto_submit_missing_applicant_name",
                notes="Applicant first/last name is missing; set profile full_name or override env vars.",
            )
        if self.config.resume_path is None or not self.config.resume_path.exists():
            self._skipped += 1
            return self._skip_outcome(
                final_stage=queue_stage,
                reason_code="auto_submit_missing_resume_file",
                notes="Resume file is missing; set JOB_HUNTER_RESUME_PATH or profile.resume_path.",
            )

        source_value = (source or "").strip().lower()
        if source_value.startswith("greenhouse/"):
            board_token = source_value.split("/", 1)[1].strip()
            if not board_token:
                self._skipped += 1
                return self._skip_outcome(
                    final_stage=queue_stage,
                    reason_code="auto_submit_missing_greenhouse_board",
                    notes="Could not determine Greenhouse board token from source.",
                )
            api_key = self.config.greenhouse_api_keys.get(board_token)
            if not api_key:
                self._skipped += 1
                return self._skip_outcome(
                    final_stage=queue_stage,
                    reason_code=f"auto_submit_missing_greenhouse_api_key:{board_token}",
                    notes=(
                        "Missing Greenhouse Job Board API key for board token "
                        f"'{board_token}'."
                    ),
                )
            job_id = _extract_greenhouse_job_id((job.get("url", "") or "").strip())
            if not job_id:
                self._skipped += 1
                return self._skip_outcome(
                    final_stage=queue_stage,
                    reason_code="auto_submit_missing_greenhouse_job_id",
                    notes="Could not parse Greenhouse job ID from URL.",
                )
            outcome = self._attempt_greenhouse_submission(
                job=job,
                board_token=board_token,
                api_key=api_key,
                job_id=job_id,
            )
            self._attempted += 1
            if outcome.applied:
                self._applied += 1
            elif outcome.final_stage == "blocked":
                self._blocked += 1
            return outcome
        self._skipped += 1
        return self._skip_outcome(
            final_stage=queue_stage,
            reason_code=f"auto_submit_unsupported_source:{source_value or 'unknown'}",
            notes=f"No auto-submit driver implemented for source '{source_value or 'unknown'}'.",
        )

    def _attempt_greenhouse_submission(
        self,
        *,
        job: JobRecord,
        board_token: str,
        api_key: str,
        job_id: str,
    ) -> AutoSubmitOutcome:
        try:
            response = self._greenhouse_submitter(
                GreenhouseSubmissionRequest(
                    board_token=board_token,
                    job_id=job_id,
                    api_key=api_key,
                    first_name=self.config.applicant_first_name,
                    last_name=self.config.applicant_last_name,
                    email=self.config.applicant_email,
                    phone=self.config.applicant_phone,
                    location=(job.get("location", "") or "").strip(),
                    resume_path=self.config.resume_path,
                )
            )
        except AutoSubmitError as exc:
            return self._blocked_outcome(
                reason_code=f"auto_submit_greenhouse_{exc.reason_code}",
                notes=exc.message,
            )
        return AutoSubmitOutcome(
            attempted=True,
            applied=True,
            skipped=False,
            final_stage="applied",
            stage_color=_stage_color_for_stage("applied"),
            reason_code="auto_submit_greenhouse_submitted",
            notes=response.notes,
            external_application_id=response.external_application_id,
        )

    @staticmethod
    def _skip_outcome(*, final_stage: str, reason_code: str, notes: str) -> AutoSubmitOutcome:
        return AutoSubmitOutcome(
            attempted=False,
            applied=False,
            skipped=True,
            final_stage=final_stage,
            stage_color=_stage_color_for_stage(final_stage),
            reason_code=reason_code,
            notes=notes,
        )

    @staticmethod
    def _blocked_outcome(*, reason_code: str, notes: str) -> AutoSubmitOutcome:
        return AutoSubmitOutcome(
            attempted=True,
            applied=False,
            skipped=False,
            final_stage="blocked",
            stage_color=_stage_color_for_stage("blocked"),
            reason_code=reason_code,
            notes=notes,
        )


def build_auto_submit_config(
    profile: ProfileConfig,
    credentials: AccountCredentials,
) -> AutoSubmitConfig:
    enabled = _env_bool("JOB_HUNTER_AUTO_SUBMIT_ENABLED", True)
    max_per_run = _env_int("JOB_HUNTER_AUTO_SUBMIT_MAX_PER_RUN", 25)
    min_score_required = _env_int("JOB_HUNTER_AUTO_SUBMIT_MIN_SCORE", 10)
    require_goal_alignment = _env_bool(
        "JOB_HUNTER_AUTO_SUBMIT_REQUIRE_RELOCATION_OR_WORK_ANYWHERE",
        True,
    )
    first_name, last_name = _split_name(profile.full_name)
    first_name = os.environ.get("JOB_HUNTER_APPLICANT_FIRST_NAME", first_name).strip()
    last_name = os.environ.get("JOB_HUNTER_APPLICANT_LAST_NAME", last_name).strip()
    applicant_email = (
        os.environ.get("JOB_HUNTER_APPLICANT_EMAIL")
        or credentials.email
        or ""
    ).strip()
    applicant_phone = os.environ.get("JOB_HUNTER_APPLICANT_PHONE", "").strip()
    resume_override = os.environ.get("JOB_HUNTER_RESUME_PATH")
    resume_path = Path(resume_override).expanduser() if resume_override else profile.resume_path
    resume_path = resume_path.expanduser() if str(resume_path).strip() else None

    return AutoSubmitConfig(
        enabled=enabled,
        max_per_run=max_per_run,
        applicant_first_name=first_name,
        applicant_last_name=last_name,
        applicant_email=applicant_email,
        applicant_phone=applicant_phone,
        resume_path=resume_path,
        greenhouse_api_keys=_parse_greenhouse_api_keys(),
        min_score_required=min_score_required,
        require_goal_alignment=require_goal_alignment,
    )


def submit_greenhouse_application(
    request: GreenhouseSubmissionRequest,
) -> GreenhouseSubmissionResponse:
    endpoint = (
        f"https://boards-api.greenhouse.io/v1/boards/{request.board_token}/jobs/{request.job_id}"
    )
    fields: dict[str, str] = {
        "first_name": request.first_name,
        "last_name": request.last_name,
        "email": request.email,
        "data_compliance[gdpr_consent_given]": "true",
        "data_compliance[gdpr_processing_consent_given]": "true",
        "data_compliance[gdpr_retention_consent_given]": "true",
    }
    if request.phone:
        fields["phone"] = request.phone
    if request.location:
        fields["location"] = request.location

    body, boundary = _encode_multipart(
        fields=fields,
        file_field_name="resume",
        file_path=request.resume_path,
    )
    encoded_key = base64.b64encode(f"{request.api_key}:".encode("utf-8")).decode("ascii")
    http_request = Request(
        endpoint,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Basic {encoded_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": "job-hunter-agent/auto-submit",
        },
    )

    try:
        with urlopen(http_request, timeout=40) as response:
            raw = response.read().decode("utf-8", "replace")
    except HTTPError as exc:
        error_text = _clean_error_text(exc.read().decode("utf-8", "replace"))
        raise AutoSubmitError(
            reason_code=f"http_{exc.code}",
            message=error_text or f"Greenhouse API returned HTTP {exc.code}.",
        ) from exc
    except URLError as exc:
        raise AutoSubmitError(
            reason_code="network_error",
            message=f"Network error during Greenhouse submit: {exc.reason}",
        ) from exc

    payload: dict[str, object] = {}
    if raw:
        try:
            loaded = json.loads(raw)
            if isinstance(loaded, dict):
                payload = loaded
        except json.JSONDecodeError:
            payload = {}

    external_application_id = _as_string(
        payload.get("id")
        or payload.get("application_id")
        or payload.get("candidate_id")
    )
    return GreenhouseSubmissionResponse(
        external_application_id=external_application_id,
        notes=f"Greenhouse submission accepted for board={request.board_token}, job_id={request.job_id}.",
    )


def _encode_multipart(
    *,
    fields: dict[str, str],
    file_field_name: str,
    file_path: Path,
) -> tuple[bytes, str]:
    boundary = f"----jobhunter-{uuid.uuid4().hex}"
    chunks: list[bytes] = []

    for name, value in fields.items():
        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        chunks.append(
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8")
        )
        chunks.append(str(value).encode("utf-8"))
        chunks.append(b"\r\n")

    mime_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    file_bytes = file_path.read_bytes()
    chunks.append(f"--{boundary}\r\n".encode("utf-8"))
    chunks.append(
        (
            f'Content-Disposition: form-data; name="{file_field_name}"; '
            f'filename="{file_path.name}"\r\n'
        ).encode("utf-8")
    )
    chunks.append(f"Content-Type: {mime_type}\r\n\r\n".encode("utf-8"))
    chunks.append(file_bytes)
    chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), boundary


def _extract_greenhouse_job_id(url: str) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    if "gh_jid" in params and params["gh_jid"]:
        job_id = params["gh_jid"][0].strip()
        if job_id:
            return job_id
    path_match = re.search(r"/jobs/(\d+)", parsed.path)
    if path_match:
        return path_match.group(1)
    return None


def _stage_color_for_stage(stage: str) -> str:
    normalized = stage.strip().lower()
    if normalized in {"applied", "submitted"}:
        return "green"
    if normalized in {"interview", "interview_progressed", "assessment", "offer"}:
        return "blue"
    if normalized in {"blocked", "waiting_on_candidate", "needs_credentials"}:
        return "orange"
    if normalized in {"rejected", "declined", "not_selected", "withdrawn"}:
        return "red"
    if normalized in {"pending_review", "queued", "draft"}:
        return "yellow"
    return "gray"

def _queue_stage_for_skip(stage: str) -> str:
    normalized = (stage or "").strip().lower()
    if not normalized:
        return "pending_review"
    if normalized in _IN_PROGRESS_STAGES:
        return normalized
    if normalized in {"blocked", "waiting_on_candidate", "needs_credentials"}:
        return "pending_review"
    return normalized


def _split_name(full_name: str) -> tuple[str, str]:
    cleaned = (full_name or "").strip()
    if not cleaned:
        return "", ""
    parts = cleaned.split()
    if len(parts) == 1:
        return parts[0], parts[0]
    return parts[0], " ".join(parts[1:])


def _parse_greenhouse_api_keys() -> dict[str, str]:
    parsed: dict[str, str] = {}
    raw_json = os.environ.get("JOB_HUNTER_GREENHOUSE_API_KEYS_JSON", "").strip()
    if raw_json:
        try:
            loaded = json.loads(raw_json)
        except json.JSONDecodeError:
            loaded = {}
        if isinstance(loaded, dict):
            for key, value in loaded.items():
                key_text = str(key).strip().lower()
                value_text = str(value).strip()
                if key_text and value_text:
                    parsed[key_text] = value_text

    prefix = "JOB_HUNTER_GREENHOUSE_API_KEY_"
    for env_key, env_value in os.environ.items():
        if not env_key.startswith(prefix):
            continue
        token = env_key[len(prefix):].strip().lower().replace("_", "-")
        if token and env_value.strip():
            parsed[token] = env_value.strip()
    return parsed


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUE_VALUES


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        parsed = int(raw.strip())
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _clean_error_text(value: str) -> str:
    compact = " ".join((value or "").split())
    return compact[:500]


def _as_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def is_goal_aligned_tags(raw_tags: object) -> bool:
    tags = _parse_tags(raw_tags)
    if "visa/relocation" in tags:
        return True
    if "work-anywhere" in tags:
        return True
    if "emea-remote" in tags:
        return True
    if "remote" in tags and "target-country" in tags:
        return True
    return False


def goal_alignment_priority(raw_tags: object) -> int:
    tags = _parse_tags(raw_tags)
    if "visa/relocation" in tags:
        return 3
    if "work-anywhere" in tags or "emea-remote" in tags:
        return 2
    if "remote" in tags and "target-country" in tags:
        return 1
    return 0


def _parse_tags(raw_tags: object) -> set[str]:
    text = str(raw_tags or "")
    return {piece.strip().lower() for piece in text.split(",") if piece.strip()}


def _coerce_score(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
