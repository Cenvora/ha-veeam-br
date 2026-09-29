"""Recognize Veeam licenses this integration does not support.

Veeam Backup & Replication Community Edition, and a server with no license installed at
all, are outside what this integration is tested against: entitlements differ, some
endpoints answer differently or not at all, and the resulting failures look like integration
bugs rather than licensing limits.

Detection is a warning, never a block. A Community Edition server that works is not worth
refusing, and the check is only as good as what the license endpoint reports — so it errs
toward saying nothing when the answer is unclear.

Kept free of Home Assistant imports so it can be tested directly.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

# EInstalledLicenseEdition value for Community Edition
COMMUNITY_EDITION = "community"

# EInstalledLicenseType values that mean "not a paid license". "Free" is what Community
# Edition reports; "Empty" is a server with no license installed, which runs as Community.
UNLICENSED_TYPES = frozenset({"free", "empty"})

# Reason codes, used as translation placeholders and log detail
REASON_COMMUNITY_EDITION = "community_edition"
REASON_NO_LICENSE = "no_license"

# Raise a repair issue this many days before the license expires
LICENSE_WARNING_DAYS = 30

# Expiration states, also the repair issues' translation keys
LICENSE_EXPIRING = "license_expiring"
LICENSE_EXPIRED = "license_expired"


def _normalize(value: Any) -> str:
    """Lower-case a license field, tolerating None and non-strings."""
    if not isinstance(value, str):
        return ""
    return value.strip().lower()


def unsupported_license_reason(license_info: dict[str, Any] | None) -> str | None:
    """Return why this license is unsupported, or None if it looks supported.

    Returns REASON_COMMUNITY_EDITION or REASON_NO_LICENSE. An absent or unreadable license
    returns None: the license endpoint failing is its own problem, already logged where it
    happens, and guessing "unlicensed" from missing data would warn people wrongly.
    """
    if not license_info:
        return None

    edition = _normalize(license_info.get("edition"))
    license_type = _normalize(license_info.get("type"))

    if edition == COMMUNITY_EDITION:
        return REASON_COMMUNITY_EDITION

    if license_type in UNLICENSED_TYPES:
        # "Empty" is specifically no license at all, which is worth naming separately
        return REASON_NO_LICENSE if license_type == "empty" else REASON_COMMUNITY_EDITION

    return None


def describe_license(license_info: dict[str, Any] | None) -> str:
    """Summarize a license for a log line, e.g. "Community/Free"."""
    if not license_info:
        return "unknown"

    edition = license_info.get("edition") or "Unknown"
    license_type = license_info.get("type") or "Unknown"
    return f"{edition}/{license_type}"


def license_expiration(
    license_info: dict[str, Any] | None, now: datetime
) -> tuple[str, datetime] | None:
    """Whether the license is expired or expires within LICENSE_WARNING_DAYS, and when.

    Returns (LICENSE_EXPIRED or LICENSE_EXPIRING, expiration), or None for a license that is
    good for longer. A license with no expiration date, such as a perpetual one, never
    expires, and a date that cannot be read says nothing either way.
    """
    expiration = (license_info or {}).get("expiration_date")
    if not isinstance(expiration, datetime):
        return None
    if expiration.tzinfo is None:
        expiration = expiration.replace(tzinfo=timezone.utc)
    if expiration <= now:
        return LICENSE_EXPIRED, expiration
    if expiration - now <= timedelta(days=LICENSE_WARNING_DAYS):
        return LICENSE_EXPIRING, expiration
    return None
