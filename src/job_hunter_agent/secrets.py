from __future__ import annotations

import os
from dataclasses import dataclass

from .config import ProfileConfig


@dataclass(frozen=True)
class AccountCredentials:
    email: str | None
    password: str | None
    email_env_var: str
    password_env_var: str

    @property
    def is_complete(self) -> bool:
        return bool(self.email and self.password)

    @property
    def missing_env_vars(self) -> tuple[str, ...]:
        missing: list[str] = []
        if not self.email:
            missing.append(self.email_env_var)
        if not self.password:
            missing.append(self.password_env_var)
        return tuple(missing)

    def redacted_status(self) -> str:
        email_state = _mask_secret(self.email)
        password_state = _mask_secret(self.password)
        return (
            f"{self.email_env_var}={email_state}, "
            f"{self.password_env_var}={password_state}"
        )


def load_account_credentials(profile: ProfileConfig) -> AccountCredentials:
    return AccountCredentials(
        email=os.environ.get(profile.login_email_env_var),
        password=os.environ.get(profile.login_password_env_var),
        email_env_var=profile.login_email_env_var,
        password_env_var=profile.login_password_env_var,
    )


def _mask_secret(value: str | None) -> str:
    if not value:
        return "missing"
    if len(value) <= 2:
        return "*" * len(value)
    return f"{value[0]}{'*' * (len(value) - 2)}{value[-1]}"
