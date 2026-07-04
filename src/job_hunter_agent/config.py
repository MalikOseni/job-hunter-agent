from __future__ import annotations

import ast
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_TARGET_COUNTRIES = (
    "netherlands", "united kingdom", "uk", "ireland", "germany", "canada",
    "new zealand", "nz", "auckland", "wellington", "australia", "sydney",
    "melbourne", "brisbane", "perth", "dubai", "united arab emirates", "uae",
    "qatar", "saudi", "sweden", "denmark", "norway", "switzerland", "belgium",
    "luxembourg", "amsterdam", "london", "berlin", "toronto", "vancouver",
    "doha", "abu dhabi", "emea",
)
DEFAULT_ACCEPTED_MOBILITY_TAGS = (
    "visa/relocation",
    "work-anywhere",
    "emea-remote",
    "target-country",
    "remote",
)


class ConfigError(RuntimeError):
    """Raised when runtime configuration is invalid."""


@dataclass(frozen=True)
class PolicyConfig:
    min_score_default: int
    max_age_days_default: int
    require_mobility_match: bool
    accepted_mobility_tags: tuple[str, ...]
    target_country_keywords: tuple[str, ...]
    require_target_country_for_non_remote: bool
    allow_remote: bool
    allow_work_anywhere: bool
    allow_emea_remote: bool
    notice_default_weeks: int
    notice_fast_track_weeks: int
    notice_immediate_weeks: int
    notice_fast_track_keywords: tuple[str, ...]
    notice_immediate_keywords: tuple[str, ...]
    salary_default_offer_strategy: str
    salary_fallback_strategy: str
    salary_currency: str
    salary_market_rate_uplift_percent: float
    writing_rules_profile: str


@dataclass(frozen=True)
class ProfileConfig:
    full_name: str
    location: str
    resume_path: Path
    notice_period_weeks: int
    alternate_notice_period_weeks: int
    relocation_assistance_required: bool
    preferred_remote_regions: tuple[str, ...]
    login_email_env_var: str
    login_password_env_var: str


@dataclass(frozen=True)
class WritingRulesConfig:
    ban_em_dash: bool
    ban_ai_tells: bool
    banned_punctuation: tuple[str, ...]
    banned_phrases: tuple[str, ...]
    banned_openers: tuple[str, ...]


@dataclass(frozen=True)
class RuntimeSettings:
    config_dir: Path
    policy: PolicyConfig
    profile: ProfileConfig
    writing_rules: WritingRulesConfig


@dataclass(frozen=True)
class _YamlLine:
    indent: int
    content: str
    line_number: int


class _MiniYamlParser:
    """Small YAML subset parser used when PyYAML is unavailable."""

    def __init__(self, text: str, source_path: Path):
        self.lines = self._tokenize(text)
        self.index = 0
        self.source_path = source_path

    def parse(self) -> Any:
        if not self.lines:
            return {}
        if self.lines[0].indent != 0:
            raise ConfigError(
                f"{self.source_path}: first content line must have zero indentation "
                f"(line {self.lines[0].line_number})"
            )
        result = self._parse_block(0)
        return result

    def _parse_block(self, indent: int) -> Any:
        if self.index >= len(self.lines):
            return {}
        current = self.lines[self.index]
        if current.indent != indent:
            raise ConfigError(
                f"{self.source_path}: unexpected indentation at line {current.line_number}"
            )
        if current.content.startswith("- "):
            return self._parse_list(indent)
        return self._parse_mapping(indent)

    def _parse_mapping(self, indent: int) -> dict[str, Any]:
        result: dict[str, Any] = {}
        while self.index < len(self.lines):
            line = self.lines[self.index]
            if line.indent < indent:
                break
            if line.indent > indent:
                raise ConfigError(
                    f"{self.source_path}: malformed mapping indentation at line "
                    f"{line.line_number}"
                )
            if line.content.startswith("- "):
                raise ConfigError(
                    f"{self.source_path}: list item found where mapping key was expected "
                    f"(line {line.line_number})"
                )
            key, sep, raw_value = line.content.partition(":")
            if not sep:
                raise ConfigError(
                    f"{self.source_path}: expected key:value at line {line.line_number}"
                )
            key = key.strip()
            raw_value = raw_value.strip()
            self.index += 1
            if not key:
                raise ConfigError(
                    f"{self.source_path}: empty key at line {line.line_number}"
                )
            if raw_value:
                result[key] = _parse_scalar(raw_value)
                if self.index < len(self.lines) and self.lines[self.index].indent > indent:
                    raise ConfigError(
                        f"{self.source_path}: unexpected nested block under scalar key "
                        f"'{key}' at line {self.lines[self.index].line_number}"
                    )
                continue
            if self.index < len(self.lines) and self.lines[self.index].indent > indent:
                result[key] = self._parse_block(self.lines[self.index].indent)
            else:
                result[key] = {}
        return result

    def _parse_list(self, indent: int) -> list[Any]:
        items: list[Any] = []
        while self.index < len(self.lines):
            line = self.lines[self.index]
            if line.indent < indent:
                break
            if line.indent > indent:
                raise ConfigError(
                    f"{self.source_path}: malformed list indentation at line "
                    f"{line.line_number}"
                )
            if not line.content.startswith("- "):
                break
            body = line.content[2:].strip()
            self.index += 1
            if not body:
                if self.index < len(self.lines) and self.lines[self.index].indent > indent:
                    items.append(self._parse_block(self.lines[self.index].indent))
                else:
                    items.append(None)
                continue
            if (
                ":" in body
                and not body.startswith(("'", '"', "[", "{"))
                and body.split(":", 1)[0].strip()
            ):
                key, _, raw_value = body.partition(":")
                key = key.strip()
                raw_value = raw_value.strip()
                item: dict[str, Any] = {}
                if raw_value:
                    item[key] = _parse_scalar(raw_value)
                elif self.index < len(self.lines) and self.lines[self.index].indent > indent:
                    item[key] = self._parse_block(self.lines[self.index].indent)
                else:
                    item[key] = {}
                if self.index < len(self.lines) and self.lines[self.index].indent > indent:
                    extra = self._parse_block(self.lines[self.index].indent)
                    if not isinstance(extra, dict):
                        raise ConfigError(
                            f"{self.source_path}: list item mapping at line "
                            f"{line.line_number} must contain key-value pairs"
                        )
                    item.update(extra)
                items.append(item)
                continue
            items.append(_parse_scalar(body))
            if self.index < len(self.lines) and self.lines[self.index].indent > indent:
                raise ConfigError(
                    f"{self.source_path}: nested blocks under list scalar are not "
                    f"supported (line {self.lines[self.index].line_number})"
                )
        return items

    def _tokenize(self, text: str) -> list[_YamlLine]:
        tokenized: list[_YamlLine] = []
        for index, raw_line in enumerate(text.splitlines(), start=1):
            if not raw_line.strip():
                continue
            stripped = _strip_inline_comment(raw_line)
            if not stripped.strip():
                continue
            leading = len(stripped) - len(stripped.lstrip(" "))
            if "\t" in stripped[:leading]:
                raise ConfigError(
                    f"{self.source_path}: tabs are not supported for indentation "
                    f"(line {index})"
                )
            tokenized.append(
                _YamlLine(
                    indent=leading,
                    content=stripped.strip(),
                    line_number=index,
                )
            )
        return tokenized


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_config_dir() -> Path:
    env_override = os.environ.get("JOB_HUNTER_CONFIG_DIR")
    if env_override:
        return Path(env_override).expanduser()
    return project_root() / "config"


def load_runtime_settings(config_dir: Path | None = None) -> RuntimeSettings:
    resolved_config_dir = (
        config_dir.expanduser() if config_dir is not None else default_config_dir()
    )
    policy_data = _as_mapping(
        "policy",
        _load_yaml_file(resolved_config_dir / "policy.yaml"),
    )
    profile_data = _as_mapping(
        "profile",
        _load_yaml_file(resolved_config_dir / "profile.yaml"),
    )
    writing_rules_data = _as_mapping(
        "writing_rules",
        _load_yaml_file(resolved_config_dir / "writing_rules.yaml"),
    )

    pipeline = _as_mapping("policy.pipeline", policy_data.get("pipeline", {}))
    relocation = _as_mapping("policy.relocation", policy_data.get("relocation", {}))
    notice = _as_mapping("policy.notice_period", policy_data.get("notice_period", {}))
    salary = _as_mapping("policy.salary", policy_data.get("salary", {}))
    writing_policy = _as_mapping("policy.writing", policy_data.get("writing", {}))

    candidate = _as_mapping("profile.candidate", profile_data.get("candidate", {}))
    accounts = _as_mapping("profile.accounts", profile_data.get("accounts", {}))
    style = _as_mapping("writing_rules.style", writing_rules_data.get("style", {}))

    policy = PolicyConfig(
        min_score_default=_as_int(pipeline.get("min_score_default"), 2),
        max_age_days_default=_as_int(pipeline.get("max_age_days_default"), 21),
        require_mobility_match=_as_bool(
            relocation.get("require_mobility_match"),
            True,
        ),
        accepted_mobility_tags=_as_str_tuple(
            relocation.get("accepted_mobility_tags"),
            DEFAULT_ACCEPTED_MOBILITY_TAGS,
        ),
        target_country_keywords=_as_str_tuple(
            relocation.get("target_country_keywords"),
            DEFAULT_TARGET_COUNTRIES,
        ),
        require_target_country_for_non_remote=_as_bool(
            relocation.get("require_target_country_for_non_remote"),
            False,
        ),
        allow_remote=_as_bool(relocation.get("allow_remote"), True),
        allow_work_anywhere=_as_bool(relocation.get("allow_work_anywhere"), True),
        allow_emea_remote=_as_bool(relocation.get("allow_emea_remote"), True),
        notice_default_weeks=_as_int(notice.get("default_weeks"), 4),
        notice_fast_track_weeks=_as_int(notice.get("fast_track_weeks"), 2),
        notice_immediate_weeks=_as_int(notice.get("immediate_weeks"), 0),
        notice_fast_track_keywords=_as_str_tuple(
            notice.get("fast_track_keywords"),
            ("two weeks", "2 weeks", "quick start"),
        ),
        notice_immediate_keywords=_as_str_tuple(
            notice.get("immediate_keywords"),
            ("asap", "immediate start", "urgent"),
        ),
        salary_default_offer_strategy=_as_str(
            salary.get("default_offer_strategy"),
            "top_of_range_if_provided",
        ),
        salary_fallback_strategy=_as_str(
            salary.get("fallback_strategy"),
            "market_rate_estimate",
        ),
        salary_currency=_as_str(salary.get("target_currency"), "GBP"),
        salary_market_rate_uplift_percent=_as_float(
            salary.get("market_rate_uplift_percent"),
            10.0,
        ),
        writing_rules_profile=_as_str(writing_policy.get("rules_profile"), "strict"),
    )
    profile = ProfileConfig(
        full_name=_as_str(candidate.get("full_name"), "Candidate"),
        location=_as_str(candidate.get("location"), ""),
        resume_path=Path(_as_str(candidate.get("resume_path"), "")).expanduser(),
        notice_period_weeks=_as_int(candidate.get("notice_period_weeks"), 4),
        alternate_notice_period_weeks=_as_int(
            candidate.get("alternate_notice_period_weeks"),
            2,
        ),
        relocation_assistance_required=_as_bool(
            candidate.get("relocation_assistance_required"),
            True,
        ),
        preferred_remote_regions=_as_str_tuple(
            candidate.get("preferred_remote_regions"),
            ("global", "emea"),
        ),
        login_email_env_var=_as_str(
            accounts.get("login_email_env_var"),
            "JOB_HUNTER_LOGIN_EMAIL",
        ),
        login_password_env_var=_as_str(
            accounts.get("login_password_env_var"),
            "JOB_HUNTER_LOGIN_PASSWORD",
        ),
    )
    writing_rules = WritingRulesConfig(
        ban_em_dash=_as_bool(style.get("ban_em_dash"), True),
        ban_ai_tells=_as_bool(style.get("ban_ai_tells"), True),
        banned_punctuation=_as_str_tuple(style.get("banned_punctuation"), ("—",)),
        banned_phrases=_as_str_tuple(style.get("banned_phrases"), tuple()),
        banned_openers=_as_str_tuple(style.get("banned_openers"), tuple()),
    )
    return RuntimeSettings(
        config_dir=resolved_config_dir,
        policy=policy,
        profile=profile,
        writing_rules=writing_rules,
    )


def _load_yaml_file(path: Path) -> Any:
    if not path.exists():
        raise ConfigError(f"Missing configuration file: {path}")
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        loaded = yaml.safe_load(text)
        return {} if loaded is None else loaded
    except ModuleNotFoundError:
        pass
    except Exception as exc:  # pragma: no cover - only reached with PyYAML installed
        raise ConfigError(f"Could not parse YAML {path}: {exc}") from exc
    try:
        loaded = json.loads(text)
        return {} if loaded is None else loaded
    except json.JSONDecodeError:
        parser = _MiniYamlParser(text, path)
        return parser.parse()


def _strip_inline_comment(raw_line: str) -> str:
    in_single = False
    in_double = False
    for index, character in enumerate(raw_line):
        if character == "'" and not in_double:
            in_single = not in_single
            continue
        if character == '"' and not in_single:
            in_double = not in_double
            continue
        if character == "#" and not in_single and not in_double:
            return raw_line[:index].rstrip()
    return raw_line.rstrip()


def _parse_scalar(token: str) -> Any:
    value = token.strip()
    if value == "":
        return ""
    lowered = value.lower()
    if lowered in {"null", "none", "~"}:
        return None
    if lowered in {"true", "yes", "on"}:
        return True
    if lowered in {"false", "no", "off"}:
        return False
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    if re.fullmatch(r"-?\d+\.\d+", value):
        return float(value)
    if value.startswith(('"', "'")) and value.endswith(('"', "'")):
        try:
            return ast.literal_eval(value)
        except Exception as exc:
            raise ConfigError(f"Invalid quoted string literal: {value}") from exc
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(piece.strip()) for piece in inner.split(",")]
    return value


def _as_mapping(name: str, value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"{name} must be a mapping, got {type(value).__name__}")
    return value


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "1", "on"}:
            return True
        if lowered in {"false", "no", "0", "off"}:
            return False
    raise ConfigError(f"Expected boolean value, got {value!r}")


def _as_int(value: Any, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        raise ConfigError(f"Expected integer value, got {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
        return int(value.strip())
    raise ConfigError(f"Expected integer value, got {value!r}")


def _as_float(value: Any, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, bool):
        raise ConfigError(f"Expected float value, got {value!r}")
    if isinstance(value, (float, int)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError as exc:
            raise ConfigError(f"Expected float value, got {value!r}") from exc
    raise ConfigError(f"Expected float value, got {value!r}")


def _as_str(value: Any, default: str) -> str:
    if value is None:
        return default
    if isinstance(value, (str, int, float)):
        return str(value).strip()
    raise ConfigError(f"Expected string value, got {value!r}")


def _as_str_tuple(value: Any, default: tuple[str, ...]) -> tuple[str, ...]:
    if value is None:
        return tuple(default)
    if isinstance(value, str):
        trimmed = value.strip()
        return (trimmed,) if trimmed else tuple(default)
    if isinstance(value, list):
        output: list[str] = []
        for item in value:
            if item is None:
                continue
            if not isinstance(item, (str, int, float)):
                raise ConfigError(f"Expected string list item, got {item!r}")
            text = str(item).strip()
            if text:
                output.append(text)
        return tuple(output)
    raise ConfigError(f"Expected list of strings, got {value!r}")
