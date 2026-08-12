"""Tests for unsupported-license detection.

licensing.py imports no Home Assistant modules, so it runs directly. The values here are the
real EInstalledLicenseEdition and EInstalledLicenseType strings the API returns.
"""

import importlib.util
import json
from pathlib import Path

import pytest

COMPONENT = Path(__file__).parent.parent / "custom_components" / "veeam_br"


def _load_licensing():
    """Load licensing.py standalone."""
    spec = importlib.util.spec_from_file_location("veeam_br_licensing", COMPONENT / "licensing.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def license_info(edition="Enterprise", type_="Perpetual"):
    """Shaped like coordinator.data["license_info"]."""
    return {"status": "Valid", "edition": edition, "type": type_}


# EInstalledLicenseEdition: Community, Enterprise, EnterprisePlus, Standard, Unspecified
# EInstalledLicenseType: Empty, Evaluation, Free, NFR, Perpetual, Promo, Rental, Subscription


@pytest.mark.parametrize(
    "edition,type_",
    [
        ("Enterprise", "Perpetual"),
        ("EnterprisePlus", "Subscription"),
        ("Standard", "Rental"),
        ("Enterprise", "Evaluation"),
        ("EnterprisePlus", "NFR"),
        ("Enterprise", "Promo"),
    ],
)
def test_supported_licenses_are_not_flagged(edition, type_):
    """Paid, evaluation and NFR licenses all entitle the endpoints this integration uses."""
    licensing = _load_licensing()

    assert licensing.unsupported_license_reason(license_info(edition, type_)) is None


def test_community_edition_is_flagged():
    licensing = _load_licensing()

    reason = licensing.unsupported_license_reason(license_info("Community", "Free"))

    assert reason == licensing.REASON_COMMUNITY_EDITION


def test_free_type_is_flagged_even_if_the_edition_is_unclear():
    """Some servers report the type but leave the edition Unspecified."""
    licensing = _load_licensing()

    reason = licensing.unsupported_license_reason(license_info("Unspecified", "Free"))

    assert reason == licensing.REASON_COMMUNITY_EDITION


def test_no_license_is_reported_separately():
    """ "Empty" means nothing is installed, which is worth naming distinctly."""
    licensing = _load_licensing()

    reason = licensing.unsupported_license_reason(license_info("Unspecified", "Empty"))

    assert reason == licensing.REASON_NO_LICENSE


def test_edition_and_type_are_matched_case_insensitively():
    """Casing is Veeam's to change; detection should not hinge on it."""
    licensing = _load_licensing()

    assert licensing.unsupported_license_reason(license_info("COMMUNITY", "free")) is not None
    assert licensing.unsupported_license_reason(license_info(" community ", "Free")) is not None


@pytest.mark.parametrize(
    "info",
    [None, {}, {"status": "Valid"}, {"edition": None, "type": None}, {"edition": 12, "type": []}],
    ids=["none", "empty", "status-only", "null-fields", "wrong-types"],
)
def test_unreadable_license_is_not_flagged(info):
    """Missing license data is its own failure, logged where it happens.

    Guessing "unlicensed" from an absent license would warn people who simply hit an API
    error, which is worse than staying quiet.
    """
    licensing = _load_licensing()

    assert licensing.unsupported_license_reason(info) is None


def test_describe_license_summarizes_for_a_log_line():
    licensing = _load_licensing()

    assert licensing.describe_license(license_info("Community", "Free")) == "Community/Free"
    assert licensing.describe_license(None) == "unknown"
    assert licensing.describe_license({}) == "unknown"
    assert "Unknown" in licensing.describe_license({"edition": None, "type": None})


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def test_warning_is_raised_as_a_repair_issue_and_logged():
    """A log line alone is invisible; repairs surface in the UI."""
    content = (COMPONENT / "__init__.py").read_text(encoding="utf-8")

    check = content[content.index("def _check_license_support") :]
    check = check[: check.index("async def async_setup_entry")]

    assert "ir.async_create_issue" in check, "should raise a repair issue"
    assert "_LOGGER.warning" in check, "should also log, for reload visibility"
    assert "IssueSeverity.WARNING" in check, "a warning, not an error: setup still works"
    assert "ir.async_delete_issue" in check, "should clear once the license is supported"
    assert "return False" not in check, "an unsupported license must not block setup"


def test_license_is_checked_on_every_setup():
    """Setup runs again on reload, which is how the warning reappears."""
    content = (COMPONENT / "__init__.py").read_text(encoding="utf-8")

    first_refresh = content.index("await coordinator.async_config_entry_first_refresh()")
    check = content.index("_check_license_support(hass, entry, coordinator.data)")
    forward = content.index("async_forward_entry_setups")

    assert first_refresh < check < forward, (
        "the check needs coordinator data, so it belongs after the first refresh and before "
        "platforms are set up"
    )


def test_issue_is_cleared_when_the_entry_is_removed():
    """A stale warning for a deleted entry would never go away on its own."""
    content = (COMPONENT / "__init__.py").read_text(encoding="utf-8")

    assert "async def async_remove_entry" in content
    remove = content[content.index("async def async_remove_entry") :]
    assert "ir.async_delete_issue" in remove

    # Unload also runs on reload, so clearing there would make the warning flicker
    unload = content[content.index("async def async_unload_entry") :]
    unload = unload[: unload.index("async def async_remove_entry")]
    assert "async_delete_issue" not in unload


def test_repair_issue_text_exists_and_is_translated():
    """An untranslated repair renders as a raw key in the UI."""
    for name in ("strings.json", "translations/en.json"):
        data = json.loads((COMPONENT / name).read_text(encoding="utf-8"))
        issue = data["issues"]["unsupported_license"]

        assert "{host}" in issue["title"]
        assert "{license}" in issue["description"]
        # The text should say it is not blocking, since the integration keeps running
        assert "keeps running" in issue["description"]


def test_diagnostics_report_what_a_bug_report_needs():
    """The issue template points people at diagnostics, so it has to carry the basics."""
    content = (COMPONENT / "diagnostics.py").read_text(encoding="utf-8")

    assert '"api_version"' in content, "which API revision was in use"
    assert "_veeam_br_version" in content, "which library version was installed"
    assert "unsupported_reason" in content, "whether the license is a supported one"
