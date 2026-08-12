"""Tests for turning Veeam's API values into readable labels.

display.py imports no Home Assistant modules, so it runs directly. The inputs below are real
values from the generated enums (EJobType, ERepositoryType, EInstalledLicenseEdition,
EJobStatus, ESessionResult, EHaPatroniNodeState) rather than invented ones.
"""

import importlib.util
from pathlib import Path

import pytest

COMPONENT = Path(__file__).parent.parent / "custom_components" / "veeam_br"


def _load_display():
    spec = importlib.util.spec_from_file_location("veeam_br_display", COMPONENT / "display.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(name="humanize", scope="module")
def humanize_fixture():
    return _load_display().humanize


@pytest.mark.parametrize(
    "value,expected",
    [
        # Job types, including the ones 13.1 added
        ("Backup", "Backup"),
        ("BackupCopy", "Backup Copy"),
        ("ProxmoxBackupJob", "Proxmox Backup Job"),
        ("NutanixAHVBackupJob", "Nutanix AHV Backup Job"),
        ("HyperVBackup", "Hyper V Backup"),
        ("EntraIDTenantBackup", "Entra ID Tenant Backup"),
        ("CloudBackupAWS", "Cloud Backup AWS"),
        ("WindowsAgentBackupFailoverCluster", "Windows Agent Backup Failover Cluster"),
        # License editions and types
        ("EnterprisePlus", "Enterprise Plus"),
        ("Enterprise", "Enterprise"),
        ("NFR", "NFR"),
        ("Perpetual", "Perpetual"),
        ("Subscription", "Subscription"),
        # Node roles and states from the HA cluster
        ("StandbyLeader", "Standby leader"),
        ("SyncStandby", "Sync standby"),
        ("Streaming", "Streaming"),
        ("InitdbFailed", "Initialisation failed"),
    ],
)
def test_reads_like_english(humanize, value, expected):
    assert humanize(value) == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        ("WinLocal", "Windows (local)"),
        ("LinuxLocal", "Linux (local)"),
        ("LinuxHardened", "Linux (hardened)"),
        ("SmbShare", "SMB share"),
        ("AmazonS3", "Amazon S3"),
        ("AzureBlob", "Azure Blob"),
        ("S3Compatible", "S3 compatible"),
        ("DDBoost", "Dell Data Domain (DD Boost)"),
        ("HPEStoreOnce", "HPE StoreOnce"),
    ],
)
def test_overrides_win_where_splitting_would_read_badly(humanize, value, expected):
    """ "WinLocal" splits to "Win Local", which is not what anyone calls it."""
    assert humanize(value) == expected


@pytest.mark.parametrize("status", ["running", "Running", " running ", "Running "])
def test_casing_does_not_change_the_label(humanize, status):
    """Job status is lower case on 1.2-rev1 and capitalised on 1.3-rev*.

    Rendering the same state differently depending on which server answered would be worse
    than leaving it raw.
    """
    assert humanize(status) == "Running"


@pytest.mark.parametrize(
    "result,expected",
    [("Success", "Success"), ("failed", "Failed"), ("Warning", "Warning")],
)
def test_session_results(humanize, result, expected):
    assert humanize(result) == expected


def test_none_result_says_what_it_means(humanize):
    """ESessionResult "None" means the job has no result yet, not a missing value."""
    assert humanize("None") == "No result"


def test_empty_license_says_what_it_means(humanize):
    """EInstalledLicenseType "Empty" means no license is installed."""
    assert humanize("Empty") == "No license"


@pytest.mark.parametrize(
    "value",
    [None, "", "   ", 12, 0, True, [], {}],
    ids=["none", "empty", "whitespace", "int", "zero", "bool", "list", "dict"],
)
def test_non_values_fall_back_to_the_default(humanize, value):
    assert humanize(value) is None
    assert humanize(value, "Unknown") == "Unknown"


def test_unknown_values_still_get_a_label(humanize):
    """A value from a future API revision must not come out blank."""
    assert humanize("SomeFutureBackupKind") == "Some Future Backup Kind"
    assert humanize("XYZBackup") == "XYZ Backup"


def test_unlisted_all_caps_runs_are_left_alone(humanize):
    """Safer than title-casing: an unknown run of capitals is usually an acronym.

    Veeam does not send fully upper-cased values, so this only affects hypotheticals.
    """
    assert humanize("SOBR") == "SOBR"
    assert humanize("VBRBackup") == "VBR Backup"


def test_snake_and_kebab_case_are_handled(humanize):
    assert humanize("backup_copy") == "Backup Copy"
    assert humanize("scale-out") == "Scale-out"


def test_acronyms_are_not_title_cased(humanize):
    display = _load_display()

    for acronym in ("AWS", "SQL", "AHV", "NFR", "VM"):
        assert acronym in display.ACRONYMS
        assert humanize(acronym) == acronym


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def test_raw_values_are_kept_alongside_the_labels():
    """Prettifying changes sensor states, so exact matching needs somewhere to go."""
    init_source = (COMPONENT / "__init__.py").read_text(encoding="utf-8")

    for key in (
        "status_raw",
        "type_raw",
        "last_result_raw",
        "edition_raw",
        "role_raw",
        "state_raw",
    ):
        assert f'"{key}"' in init_source, f"{key} should be stored next to its label"


def test_prettified_sensors_expose_the_raw_value():
    sensor_source = (COMPONENT / "sensor.py").read_text(encoding="utf-8")

    assert (
        sensor_source.count('"raw_value"') >= 7
    ), "each sensor whose state is now a label should expose the API value as an attribute"


def test_blueprints_still_match_after_prettifying():
    """The blueprints compare lower-cased, so Success/success both work — keep it that way."""
    blueprints = Path(__file__).parent.parent / "blueprints" / "automation" / "veeam_br"

    for name in ("job_failed.yaml", "daily_backup_summary.yaml"):
        text = (blueprints / name).read_text(encoding="utf-8")
        assert "| lower" in text or "map('lower')" in text

        # A label is a single word for results, so no blueprint should match on a raw enum
        # identifier that prettifying would have split
        assert "EnterprisePlus" not in text
        assert "BackupCopy" not in text
