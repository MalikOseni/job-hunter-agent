from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import uuid
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from .config import ProfileConfig
from .secrets import AccountCredentials
from .types import JobRecord

_TRUE_VALUES = {"1", "true", "yes", "on"}
_IN_PROGRESS_STAGES = {"applied", "submitted", "interview", "interview_progressed", "assessment", "offer"}
_GREENHOUSE_STANDARD_FIELDS = {
    "first_name",
    "last_name",
    "preferred_name",
    "email",
    "phone",
    "resume_text",
    "cover_letter_text",
    "location",
    "latitude",
    "longitude",
    "country_short_name",
    "candidate_location",
    "resume",
    "cover_letter",
    "education",
    "employment",
}


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
    applicant_location: str = ""
    greenhouse_hosted_fallback_enabled: bool = False
    greenhouse_question_answers: dict[str, object] = field(default_factory=dict)
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
class AutoSubmitReadiness:
    enabled: bool
    candidate_greenhouse_roles: int
    greenhouse_hosted_fallback_enabled: bool
    missing_prerequisites: tuple[str, ...]
    missing_greenhouse_board_tokens: dict[str, int]

    @property
    def ready_for_real_attempts(self) -> bool:
        if not self.enabled:
            return False
        if self.missing_prerequisites:
            return False
        if (
            self.candidate_greenhouse_roles > 0
            and self.missing_greenhouse_board_tokens
            and not self.greenhouse_hosted_fallback_enabled
        ):
            return False
        return True


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


@dataclass(frozen=True)
class GreenhouseHostedContext:
    submit_path: str
    fingerprint: str
    jben_url: str
    cookie_header: str
    job_post: dict[str, object]


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
            if api_key:
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
            elif self.config.greenhouse_hosted_fallback_enabled:
                outcome = self._attempt_greenhouse_hosted_submission(job=job)
            else:
                self._skipped += 1
                return self._skip_outcome(
                    final_stage=queue_stage,
                    reason_code=f"auto_submit_missing_greenhouse_api_key:{board_token}",
                    notes=(
                        "Missing Greenhouse Job Board API key for board token "
                        f"'{board_token}'. Enable hosted fallback or provide key."
                    ),
                )

            if outcome.attempted:
                self._attempted += 1
                if outcome.applied:
                    self._applied += 1
                elif outcome.final_stage == "blocked":
                    self._blocked += 1
            else:
                self._skipped += 1
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
    def _attempt_greenhouse_hosted_submission(self, *, job: JobRecord) -> AutoSubmitOutcome:
        job_url = (job.get("url", "") or "").strip()
        if not job_url:
            return self._skip_outcome(
                final_stage="pending_review",
                reason_code="auto_submit_missing_greenhouse_job_url",
                notes="Could not determine Greenhouse hosted apply URL from job record.",
            )

        try:
            context = _load_greenhouse_hosted_context(job_url)
        except AutoSubmitError as exc:
            return self._blocked_outcome(
                reason_code=f"auto_submit_greenhouse_hosted_{exc.reason_code}",
                notes=exc.message,
            )

        question_fields = _extract_greenhouse_question_fields(context.job_post)
        if not question_fields:
            return self._skip_outcome(
                final_stage="pending_review",
                reason_code="auto_submit_greenhouse_hosted_missing_question_schema",
                notes="Hosted Greenhouse page did not expose question schema; cannot safely submit.",
            )

        answers: dict[str, Any] = dict(self.config.greenhouse_question_answers)
        answers.setdefault("first_name", self.config.applicant_first_name)
        answers.setdefault("last_name", self.config.applicant_last_name)
        answers.setdefault("email", self.config.applicant_email)
        answers.setdefault("phone", self.config.applicant_phone)
        applicant_location = (
            self.config.applicant_location.strip()
            or (job.get("location", "") or "").strip()
        )
        if applicant_location:
            answers.setdefault("candidate_location", applicant_location)
            answers.setdefault("location", applicant_location)

        file_paths: dict[str, Path] = {}
        if self.config.resume_path is not None:
            file_paths["resume"] = self.config.resume_path
        cover_letter_path = _path_from_answer(answers.get("cover_letter"))
        if cover_letter_path is not None:
            file_paths["cover_letter"] = cover_letter_path

        missing_required_fields: list[str] = []
        for field in question_fields:
            name = field["name"]
            label = field["label"] or name
            required = field["required"]
            field_type = field["type"]

            if field_type == "input_file":
                if name in file_paths:
                    continue
                field_path = _path_from_answer(answers.get(name))
                if field_path is not None:
                    file_paths[name] = field_path
                    continue
                if required:
                    missing_required_fields.append(f"{label} ({name})")
                continue

            normalized_value = _resolve_greenhouse_field_value(field, answers.get(name))
            if normalized_value is None:
                if required:
                    missing_required_fields.append(f"{label} ({name})")
                continue
            answers[name] = normalized_value

        if missing_required_fields:
            return self._skip_outcome(
                final_stage="pending_review",
                reason_code="auto_submit_missing_greenhouse_required_answers",
                notes=(
                    "Missing required Greenhouse hosted-form answers: "
                    f"{', '.join(sorted(missing_required_fields))}. "
                    "Provide values in JOB_HUNTER_GREENHOUSE_QUESTION_ANSWERS_JSON."
                ),
            )

        try:
            uploaded_files = _upload_greenhouse_files(
                jben_url=context.jben_url,
                cookie_header=context.cookie_header,
                file_paths=file_paths,
            )
        except AutoSubmitError as exc:
            return self._blocked_outcome(
                reason_code=f"auto_submit_greenhouse_hosted_{exc.reason_code}",
                notes=exc.message,
            )

        application_payload = _build_greenhouse_application_payload(
            job_post=context.job_post,
            question_fields=question_fields,
            answers=answers,
            uploaded_files=uploaded_files,
            job_url=job_url,
        )
        try:
            external_application_id = _submit_greenhouse_hosted_application(
                context=context,
                application_payload=application_payload,
            )
        except AutoSubmitError as exc:
            if exc.reason_code in {"hosted_validation_error", "hosted_bad_request"}:
                return self._skip_outcome(
                    final_stage="pending_review",
                    reason_code=f"auto_submit_greenhouse_hosted_{exc.reason_code}",
                    notes=exc.message,
                )
            return self._blocked_outcome(
                reason_code=f"auto_submit_greenhouse_hosted_{exc.reason_code}",
                notes=exc.message,
            )

        return AutoSubmitOutcome(
            attempted=True,
            applied=True,
            skipped=False,
            final_stage="applied",
            stage_color=_stage_color_for_stage("applied"),
            reason_code="auto_submit_greenhouse_hosted_submitted",
            notes="Greenhouse hosted form submitted successfully without Job Board API key.",
            external_application_id=external_application_id,
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
    applicant_location = os.environ.get("JOB_HUNTER_APPLICANT_LOCATION", profile.location).strip()
    greenhouse_hosted_fallback_enabled = _env_bool(
        "JOB_HUNTER_GREENHOUSE_HOSTED_FALLBACK_ENABLED",
        True,
    )
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
        applicant_location=applicant_location,
        resume_path=resume_path,
        greenhouse_api_keys=_parse_greenhouse_api_keys(),
        greenhouse_hosted_fallback_enabled=greenhouse_hosted_fallback_enabled,
        greenhouse_question_answers=_parse_greenhouse_question_answers(),
        min_score_required=min_score_required,
        require_goal_alignment=require_goal_alignment,
    )

def build_auto_submit_readiness(
    config: AutoSubmitConfig,
    shortlisted_jobs: list[JobRecord],
) -> AutoSubmitReadiness:
    missing_prerequisites: list[str] = []
    if not config.enabled:
        missing_prerequisites.append("Enable auto-submit with JOB_HUNTER_AUTO_SUBMIT_ENABLED=1.")
    if not config.applicant_email:
        missing_prerequisites.append(
            "Set JOB_HUNTER_APPLICANT_EMAIL (or JOB_HUNTER_LOGIN_EMAIL) for application identity."
        )
    if not config.applicant_first_name or not config.applicant_last_name:
        missing_prerequisites.append(
            "Set JOB_HUNTER_APPLICANT_FIRST_NAME and JOB_HUNTER_APPLICANT_LAST_NAME "
            "(or profile.candidate.full_name)."
        )
    if config.resume_path is None or not config.resume_path.exists():
        missing_prerequisites.append(
            "Set JOB_HUNTER_RESUME_PATH (or profile.candidate.resume_path) to an existing resume file."
        )

    missing_greenhouse_keys: dict[str, int] = {}
    candidate_greenhouse_roles = 0
    for job in shortlisted_jobs:
        score = _coerce_score(job.get("score"))
        if config.min_score_required > 0 and score < config.min_score_required:
            continue
        if config.require_goal_alignment and not is_goal_aligned_tags(job.get("tags", "")):
            continue
        board_token = _extract_greenhouse_board_token(str(job.get("source", "") or ""))
        if not board_token:
            continue
        candidate_greenhouse_roles += 1
        if board_token in config.greenhouse_api_keys:
            continue
        missing_greenhouse_keys[board_token] = missing_greenhouse_keys.get(board_token, 0) + 1

    return AutoSubmitReadiness(
        enabled=config.enabled,
        candidate_greenhouse_roles=candidate_greenhouse_roles,
        greenhouse_hosted_fallback_enabled=config.greenhouse_hosted_fallback_enabled,
        missing_prerequisites=tuple(missing_prerequisites),
        missing_greenhouse_board_tokens={
            token: missing_greenhouse_keys[token]
            for token in sorted(missing_greenhouse_keys)
        },
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
    file_content_type: str | None = None,
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

    mime_type = file_content_type or mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
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

def _extract_greenhouse_board_token(source: str) -> str | None:
    source_value = (source or "").strip().lower()
    if not source_value.startswith("greenhouse/"):
        return None
    board_token = source_value.split("/", 1)[1].strip()
    return board_token or None


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

def _parse_greenhouse_question_answers() -> dict[str, object]:
    raw_json = os.environ.get("JOB_HUNTER_GREENHOUSE_QUESTION_ANSWERS_JSON", "").strip()
    if not raw_json:
        return {}
    try:
        loaded = json.loads(raw_json)
    except json.JSONDecodeError:
        return {}
    if not isinstance(loaded, dict):
        return {}
    parsed: dict[str, object] = {}
    for key, value in loaded.items():
        key_text = str(key).strip()
        if not key_text:
            continue
        parsed[key_text] = value
    return parsed

def _load_greenhouse_hosted_context(job_url: str) -> GreenhouseHostedContext:
    req = Request(
        job_url,
        headers={
            "User-Agent": "job-hunter-agent/auto-submit",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    try:
        with urlopen(req, timeout=40) as response:
            html_text = response.read().decode("utf-8", "replace")
            cookie_header = _extract_cookie_header(response.headers)
    except HTTPError as exc:
        raise AutoSubmitError(
            reason_code=f"hosted_context_http_{exc.code}",
            message=f"Greenhouse hosted form request failed with HTTP {exc.code}.",
        ) from exc
    except URLError as exc:
        raise AutoSubmitError(
            reason_code="network_error",
            message=f"Network error while loading hosted Greenhouse form: {exc.reason}",
        ) from exc

    remix_context = _extract_remix_context(html_text)
    loader_data = remix_context.get("state", {}).get("loaderData", {})
    if not isinstance(loader_data, dict):
        raise AutoSubmitError(
            reason_code="hosted_context_missing_loader_data",
            message="Hosted Greenhouse page did not include expected loader data.",
        )
    route_data: dict[str, Any] | None = None
    for value in loader_data.values():
        if not isinstance(value, dict):
            continue
        if "submitPath" in value and "jobPost" in value:
            route_data = value
            break
    if route_data is None:
        raise AutoSubmitError(
            reason_code="hosted_context_missing_submit_path",
            message="Could not locate hosted Greenhouse submit path in page data.",
        )
    root_data = loader_data.get("root", {})
    root_env = root_data.get("ENV", {}) if isinstance(root_data, dict) else {}
    jben_url = str(root_env.get("JBEN_URL", "https://boards.greenhouse.io") or "").strip()
    if not jben_url:
        jben_url = "https://boards.greenhouse.io"

    submit_path = str(route_data.get("submitPath", "") or "").strip()
    if not submit_path:
        raise AutoSubmitError(
            reason_code="hosted_context_missing_submit_path",
            message="Hosted Greenhouse submit path is empty.",
        )
    if submit_path.startswith("/"):
        submit_path = f"{jben_url.rstrip('/')}{submit_path}"

    job_post = route_data.get("jobPost")
    if not isinstance(job_post, dict):
        raise AutoSubmitError(
            reason_code="hosted_context_missing_job_post",
            message="Hosted Greenhouse page did not expose job post payload.",
        )
    fingerprint = str(job_post.get("fingerprint", "") or "").strip()
    if not fingerprint:
        raise AutoSubmitError(
            reason_code="hosted_context_missing_fingerprint",
            message="Hosted Greenhouse payload did not include application fingerprint.",
        )
    return GreenhouseHostedContext(
        submit_path=submit_path,
        fingerprint=fingerprint,
        jben_url=jben_url,
        cookie_header=cookie_header,
        job_post=job_post,
    )

def _extract_remix_context(html_text: str) -> dict[str, Any]:
    prefix = "window.__remixContext = "
    start = html_text.find(prefix)
    if start == -1:
        raise AutoSubmitError(
            reason_code="hosted_context_missing_remix",
            message="Hosted Greenhouse page did not expose Remix context.",
        )
    start += len(prefix)
    json_start = html_text.find("{", start)
    if json_start == -1:
        raise AutoSubmitError(
            reason_code="hosted_context_invalid_remix",
            message="Hosted Greenhouse Remix context JSON did not start correctly.",
        )
    level = 0
    in_string = False
    escaped = False
    json_end: int | None = None
    for idx, character in enumerate(html_text[json_start:], json_start):
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == "\"":
                in_string = False
            continue
        if character == "\"":
            in_string = True
        elif character == "{":
            level += 1
        elif character == "}":
            level -= 1
            if level == 0:
                json_end = idx + 1
                break
    if json_end is None:
        raise AutoSubmitError(
            reason_code="hosted_context_invalid_remix",
            message="Hosted Greenhouse Remix context JSON was incomplete.",
        )
    raw_json = html_text[json_start:json_end]
    try:
        loaded = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise AutoSubmitError(
            reason_code="hosted_context_invalid_remix",
            message="Hosted Greenhouse Remix context JSON could not be parsed.",
        ) from exc
    if not isinstance(loaded, dict):
        raise AutoSubmitError(
            reason_code="hosted_context_invalid_remix",
            message="Hosted Greenhouse Remix context payload had unexpected shape.",
        )
    return loaded

def _extract_cookie_header(headers) -> str:
    cookie_values = headers.get_all("Set-Cookie") if hasattr(headers, "get_all") else []
    if not cookie_values:
        return ""
    cookies: list[str] = []
    for value in cookie_values:
        trimmed = str(value or "").split(";", 1)[0].strip()
        if trimmed:
            cookies.append(trimmed)
    return "; ".join(cookies)

def _extract_greenhouse_question_fields(job_post: dict[str, object]) -> list[dict[str, Any]]:
    questions = job_post.get("questions")
    if not isinstance(questions, list):
        return []
    extracted: list[dict[str, Any]] = []
    for question in questions:
        if not isinstance(question, dict):
            continue
        fields = question.get("fields")
        if not isinstance(fields, list) or not fields:
            continue
        first_field = fields[0]
        if not isinstance(first_field, dict):
            continue
        field_name = str(first_field.get("name", "") or "").strip()
        if not field_name:
            continue
        raw_values = first_field.get("values")
        values = raw_values if isinstance(raw_values, list) else []
        extracted.append(
            {
                "name": field_name,
                "type": str(first_field.get("type", "") or "").strip(),
                "required": bool(question.get("required")),
                "label": str(question.get("label", "") or "").strip(),
                "values": values,
            }
        )
    return extracted

def _resolve_greenhouse_field_value(field: dict[str, Any], raw_value: object) -> object | None:
    name = str(field.get("name", "") or "")
    field_type = str(field.get("type", "") or "")
    options = field.get("values", [])
    required = bool(field.get("required"))

    if field_type in {"input_text", "textarea"} or name in _GREENHOUSE_STANDARD_FIELDS:
        text_value = str(raw_value or "").strip()
        if text_value:
            return text_value
        return None

    if field_type == "multi_value_single_select":
        option_value = _normalize_greenhouse_option_value(raw_value, options)
        if option_value is not None:
            return option_value
        if required and isinstance(options, list) and len(options) == 1:
            only_option = options[0]
            if isinstance(only_option, dict):
                return only_option.get("value")
        return None

    if field_type == "multi_value_multi_select":
        values_to_process = raw_value if isinstance(raw_value, list) else [raw_value]
        normalized: list[object] = []
        for value in values_to_process:
            mapped = _normalize_greenhouse_option_value(value, options)
            if mapped is not None:
                normalized.append(mapped)
        if normalized:
            return normalized
        if required and isinstance(options, list) and len(options) == 1:
            only_option = options[0]
            if isinstance(only_option, dict):
                return [only_option.get("value")]
        return None

    if raw_value is None:
        return None
    return raw_value

def _normalize_greenhouse_option_value(raw_value: object, options: object) -> object | None:
    if raw_value is None:
        return None
    if not isinstance(options, list):
        return None

    normalized_text = str(raw_value).strip().lower()
    for option in options:
        if not isinstance(option, dict):
            continue
        option_value = option.get("value")
        option_label = str(option.get("label", "") or "").strip().lower()
        if str(option_value) == str(raw_value):
            return option_value
        if normalized_text and option_label == normalized_text:
            return option_value

    if isinstance(raw_value, bool):
        for option in options:
            if not isinstance(option, dict):
                continue
            option_value = option.get("value")
            if option_value in {0, 1, "0", "1"}:
                return 1 if raw_value else 0
    return None

def _path_from_answer(raw_value: object) -> Path | None:
    text_value = str(raw_value or "").strip()
    if not text_value:
        return None
    candidate = Path(text_value).expanduser()
    if not candidate.exists() or not candidate.is_file():
        return None
    return candidate

def _upload_greenhouse_files(
    *,
    jben_url: str,
    cookie_header: str,
    file_paths: dict[str, Path],
) -> dict[str, dict[str, str]]:
    if not file_paths:
        return {}
    presigned = _fetch_greenhouse_presigned_fields(
        jben_url=jben_url,
        cookie_header=cookie_header,
        fields=list(file_paths.keys()),
    )
    upload_url = str(presigned.get("url", "") or "").strip()
    if not upload_url:
        raise AutoSubmitError(
            reason_code="hosted_upload_missing_url",
            message="Greenhouse did not return upload URL for hosted attachments.",
        )
    uploaded: dict[str, dict[str, str]] = {}
    for field_name, file_path in file_paths.items():
        presigned_field = presigned.get(field_name)
        if not isinstance(presigned_field, dict):
            raise AutoSubmitError(
                reason_code="hosted_upload_missing_fields",
                message=f"Greenhouse did not return presigned upload fields for '{field_name}'.",
            )
        uploaded[field_name] = _upload_greenhouse_file(
            upload_url=upload_url,
            presigned_field=presigned_field,
            file_path=file_path,
        )
    return uploaded

def _fetch_greenhouse_presigned_fields(
    *,
    jben_url: str,
    cookie_header: str,
    fields: list[str],
) -> dict[str, object]:
    query = urlencode([("fields[]", field) for field in fields])
    request_url = f"{jben_url.rstrip('/')}/uncacheable_attributes/presigned_fields?{query}"
    headers = {
        "User-Agent": "job-hunter-agent/auto-submit",
        "Accept": "application/json",
    }
    if cookie_header:
        headers["Cookie"] = cookie_header
    request = Request(request_url, headers=headers)
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8", "replace")
    except HTTPError as exc:
        raise AutoSubmitError(
            reason_code=f"hosted_upload_fields_http_{exc.code}",
            message=f"Greenhouse presigned upload request failed with HTTP {exc.code}.",
        ) from exc
    except URLError as exc:
        raise AutoSubmitError(
            reason_code="network_error",
            message=f"Network error while requesting Greenhouse upload fields: {exc.reason}",
        ) from exc

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AutoSubmitError(
            reason_code="hosted_upload_fields_invalid_response",
            message="Greenhouse presigned upload response was not valid JSON.",
        ) from exc
    if not isinstance(payload, dict):
        raise AutoSubmitError(
            reason_code="hosted_upload_fields_invalid_response",
            message="Greenhouse presigned upload response had unexpected shape.",
        )
    return payload

def _upload_greenhouse_file(
    *,
    upload_url: str,
    presigned_field: dict[str, object],
    file_path: Path,
) -> dict[str, str]:
    form_fields = presigned_field.get("fields", {})
    if not isinstance(form_fields, dict):
        raise AutoSubmitError(
            reason_code="hosted_upload_fields_invalid_response",
            message=f"Invalid Greenhouse upload fields received for {file_path.name}.",
        )
    key_template = str(presigned_field.get("key", "") or "").strip()
    if not key_template:
        raise AutoSubmitError(
            reason_code="hosted_upload_fields_invalid_response",
            message=f"Missing Greenhouse upload key for {file_path.name}.",
        )
    resolved_key = (
        key_template.replace("{timestamp}", str(int(datetime.now().timestamp() * 1000)))
        .replace("{unique_id}", uuid.uuid4().hex[:12])
    )
    fields: dict[str, str] = {"utf8": "✓"}
    for key, value in form_fields.items():
        fields[str(key)] = str(value)
    fields["key"] = resolved_key
    fields["authenticity_token"] = "1234"
    fields["Content-Type"] = "application/octet-stream"
    body, boundary = _encode_multipart(
        fields=fields,
        file_field_name="file",
        file_path=file_path,
        file_content_type="application/octet-stream",
    )
    request = Request(
        upload_url,
        data=body,
        method="POST",
        headers={
            "User-Agent": "job-hunter-agent/auto-submit",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    try:
        with urlopen(request, timeout=40):
            pass
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", "replace")
        if "EntityTooLarge" in error_body:
            raise AutoSubmitError(
                reason_code="hosted_file_too_large",
                message=f"Greenhouse rejected {file_path.name}: file exceeded allowed upload size.",
            ) from exc
        raise AutoSubmitError(
            reason_code=f"hosted_upload_http_{exc.code}",
            message=f"Greenhouse upload failed for {file_path.name} with HTTP {exc.code}.",
        ) from exc
    except URLError as exc:
        raise AutoSubmitError(
            reason_code="network_error",
            message=f"Network error while uploading {file_path.name}: {exc.reason}",
        ) from exc
    return {
        "url": f"{upload_url.rstrip('/')}/{resolved_key}",
        "name": file_path.name,
    }

def _build_greenhouse_application_payload(
    *,
    job_post: dict[str, object],
    question_fields: list[dict[str, Any]],
    answers: dict[str, Any],
    uploaded_files: dict[str, dict[str, str]],
    job_url: str,
) -> dict[str, object]:
    application: dict[str, object] = {
        "first_name": str(answers.get("first_name", "") or ""),
        "last_name": str(answers.get("last_name", "") or ""),
        "email": str(answers.get("email", "") or ""),
        "answers_attributes": {},
        "demographic_answers": [],
        "data_compliance": _build_greenhouse_data_compliance(job_post.get("data_compliance")),
        "attachments": {},
        "from_job_board_renderer": True,
        "employments": [],
    }
    for key in (
        "preferred_name",
        "phone",
        "resume_text",
        "cover_letter_text",
        "location",
        "latitude",
        "longitude",
        "country_short_name",
    ):
        value = str(answers.get(key, "") or "").strip()
        if value:
            application[key] = value
    if "location" not in application:
        candidate_location = str(answers.get("candidate_location", "") or "").strip()
        if candidate_location:
            application["location"] = candidate_location

    resume_upload = uploaded_files.get("resume")
    if resume_upload:
        application["resume_url"] = resume_upload["url"]
        application["resume_url_filename"] = resume_upload["name"]
    cover_letter_upload = uploaded_files.get("cover_letter")
    if cover_letter_upload:
        application["cover_letter_url"] = cover_letter_upload["url"]
        application["cover_letter_url_filename"] = cover_letter_upload["name"]

    answers_attributes: dict[str, object] = {}
    attachments: dict[str, str] = {}
    priority = 0
    for field in question_fields:
        name = field["name"]
        field_type = field["type"]
        question_id = _extract_greenhouse_question_id(name)
        if not question_id:
            continue
        if field_type == "input_file":
            uploaded_file = uploaded_files.get(name)
            if uploaded_file:
                attachments[f"{question_id}_url"] = uploaded_file["url"]
                attachments[f"{question_id}_url_filename"] = uploaded_file["name"]
            continue
        if name in _GREENHOUSE_STANDARD_FIELDS:
            continue
        value = answers.get(name)
        if value is None:
            continue
        answer_entry: dict[str, object] = {
            "question_id": question_id,
            "priority": priority,
        }
        if field_type in {"input_text", "textarea"}:
            answer_entry["text_value"] = str(value)
        elif field_type in {"multi_value_single_select", "multi_value_multi_select"}:
            if isinstance(value, bool) or (isinstance(value, int) and value in {0, 1}):
                answer_entry["boolean_value"] = int(value)
            else:
                selected_values = value if isinstance(value, list) else [value]
                selected_options: dict[str, object] = {}
                for index, selected in enumerate(selected_values):
                    selected_options[str(index)] = {
                        "question_option_id": selected,
                    }
                answer_entry["answer_selected_options_attributes"] = selected_options
        else:
            answer_entry["text_value"] = str(value)
        answers_attributes[question_id] = answer_entry
        priority += 1
    application["answers_attributes"] = answers_attributes
    application["attachments"] = attachments

    parsed_url = urlparse(job_url)
    query_params = parse_qs(parsed_url.query)
    mapped_url_token = ""
    if query_params.get("t"):
        mapped_url_token = str(query_params["t"][0] or "").strip()
    elif query_params.get("gh_src"):
        mapped_url_token = str(query_params["gh_src"][0] or "").strip()
    if mapped_url_token:
        application["mapped_url_token"] = mapped_url_token
    appcast_click_id = ""
    if query_params.get("ccuid"):
        appcast_click_id = str(query_params["ccuid"][0] or "").strip()
    if appcast_click_id:
        application["appcast_click_id"] = appcast_click_id

    application["time_zone"] = os.environ.get("TZ", "UTC")
    return application

def _build_greenhouse_data_compliance(raw_data_compliance: object) -> dict[str, bool]:
    if not isinstance(raw_data_compliance, dict):
        return {}
    compliance: dict[str, bool] = {}
    if str(raw_data_compliance.get("type", "") or "").strip().lower() == "gdpr":
        compliance["gdpr_consent_given"] = True
        if bool(raw_data_compliance.get("requires_processing_consent")):
            compliance["gdpr_processing_consent_given"] = True
        if bool(raw_data_compliance.get("requires_retention_consent")):
            compliance["gdpr_retention_consent_given"] = True
        if bool(raw_data_compliance.get("demographic_data_consent_applies")):
            compliance["gdpr_demographic_data_consent_given"] = True
    return compliance

def _submit_greenhouse_hosted_application(
    *,
    context: GreenhouseHostedContext,
    application_payload: dict[str, object],
) -> str | None:
    body = json.dumps(
        {
            "job_application": application_payload,
            "fingerprint": context.fingerprint,
        }
    ).encode("utf-8")
    headers = {
        "User-Agent": "job-hunter-agent/auto-submit",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if context.cookie_header:
        headers["Cookie"] = context.cookie_header
    request = Request(
        context.submit_path,
        data=body,
        method="POST",
        headers=headers,
    )
    try:
        with urlopen(request, timeout=40) as response:
            raw_response = response.read().decode("utf-8", "replace")
    except HTTPError as exc:
        raw_error = exc.read().decode("utf-8", "replace")
        message = _extract_greenhouse_error_message(raw_error) or (
            f"Greenhouse hosted submit returned HTTP {exc.code}."
        )
        lowered = message.lower()
        if "captcha" in lowered or "security code" in lowered or "recaptcha" in lowered:
            raise AutoSubmitError(
                reason_code="hosted_captcha_required",
                message=message,
            ) from exc
        if exc.code in {400, 422}:
            reason = "hosted_bad_request" if exc.code == 400 else "hosted_validation_error"
            raise AutoSubmitError(
                reason_code=reason,
                message=message,
            ) from exc
        raise AutoSubmitError(
            reason_code=f"hosted_http_{exc.code}",
            message=message,
        ) from exc
    except URLError as exc:
        raise AutoSubmitError(
            reason_code="network_error",
            message=f"Network error during Greenhouse hosted submit: {exc.reason}",
        ) from exc

    payload: dict[str, object] = {}
    if raw_response:
        try:
            parsed = json.loads(raw_response)
        except json.JSONDecodeError:
            parsed = {}
        if isinstance(parsed, dict):
            payload = parsed
    if payload.get("code") == "unprocessable-entity":
        message = str(payload.get("message", "") or "").strip() or "Hosted Greenhouse submit validation failed."
        raise AutoSubmitError(
            reason_code="hosted_validation_error",
            message=message,
        )
    return _as_string(
        payload.get("id")
        or payload.get("application_id")
        or payload.get("candidate_id")
    )

def _extract_greenhouse_error_message(raw_value: str) -> str:
    text = str(raw_value or "").strip()
    if not text:
        return ""
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        return _clean_error_text(text)
    if not isinstance(loaded, dict):
        return _clean_error_text(text)
    message = str(loaded.get("message", "") or "").strip()
    if message:
        return _clean_error_text(message)
    errors = loaded.get("errors")
    if isinstance(errors, list):
        collected = []
        for item in errors:
            if isinstance(item, dict):
                attribute = str(item.get("attribute", "") or "").strip()
                detail = str(item.get("message", "") or "").strip()
                if attribute and detail:
                    collected.append(f"{attribute}: {detail}")
                elif detail:
                    collected.append(detail)
            elif item:
                collected.append(str(item))
        if collected:
            return _clean_error_text("; ".join(collected))
    return _clean_error_text(text)

def _extract_greenhouse_question_id(field_name: str) -> str | None:
    normalized = str(field_name or "").strip()
    if not normalized.startswith("question_"):
        return None
    question_id = normalized.split("_", 1)[1]
    if question_id.endswith("[]"):
        question_id = question_id[:-2]
    return question_id.strip() or None


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
