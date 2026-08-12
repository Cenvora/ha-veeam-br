"""Tests for reading license dates across API revisions.

Veeam moved the license dates between revisions: 1.2-rev1 exposes expirationDate and
supportExpirationDate on the license itself, while 1.3-rev* removed them from the top level and
keep them only inside the per-package summary. Reading the top level alone leaves the sensors
unknown on any 13.x server.

The helpers live in __init__.py, which imports Home Assistant, so they are lifted out with ast
— the same approach as the HA cluster tests.
"""

import ast
from datetime import datetime, timezone
from pathlib import Path

import pytest

INIT_PATH = Path(__file__).parent.parent / "custom_components" / "veeam_br" / "__init__.py"
HELPERS = ("_is_unset", "_license_datetime", "_license_text")


@pytest.fixture(name="helpers", scope="module")
def helpers_fixture():
    """Execute just the license helpers from __init__.py."""
    tree = ast.parse(INIT_PATH.read_text(encoding="utf-8"))
    wanted = [
        node
        for node in tree.body
        if (isinstance(node, ast.FunctionDef) and node.name in HELPERS)
        or (
            isinstance(node, ast.Assign)
            and getattr(node.targets[0], "id", "") == "LICENSE_SUMMARIES"
        )
    ]
    assert len(wanted) == len(HELPERS) + 1, f"expected {HELPERS} + LICENSE_SUMMARIES, got {wanted}"

    namespace = {}
    exec(compile(ast.Module(body=wanted, type_ignores=[]), str(INIT_PATH), "exec"), namespace)
    return namespace


class Unset:
    """Stands in for the generated UNSET sentinel, matched by class name."""


UNSET = Unset()

EXPIRES = datetime(2027, 7, 17, tzinfo=timezone.utc)
SUPPORT_EXPIRES = datetime(2027, 1, 1, tzinfo=timezone.utc)


class Summary:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class License:
    """A license as the generated model presents it."""

    def __init__(self, **kwargs):
        self.status = "Valid"
        self.edition = "EnterprisePlus"
        self.licensed_to = "CyberFortress"
        self.support_id = ""
        self.instance_license_summary = UNSET
        self.socket_license_summary = UNSET
        self.capacity_license_summary = UNSET
        self.__dict__.update(kwargs)


def rev2_license():
    """The 13.1 shape: no top-level dates, everything inside instanceLicenseSummary.

    Mirrors a real /api/v1/license response from a 13.1 server with an NFR license.
    """
    return License(
        type_="NFR",
        instance_license_summary=Summary(
            package="Suite",
            licensed_instances_number=20,
            used_instances_number=11,
            expiration_date=EXPIRES,
            support_expiration_date=UNSET,
        ),
    )


def rev1_license():
    """The 12.x shape: dates on the license itself."""
    return License(
        type_="Perpetual",
        expiration_date=EXPIRES,
        support_expiration_date=SUPPORT_EXPIRES,
    )


def test_reads_the_nested_expiration_on_13_1(helpers):
    """The reported bug: this used to come back as unknown."""
    assert helpers["_license_datetime"](rev2_license(), "expiration_date") == EXPIRES


def test_reads_the_top_level_expiration_on_12_x(helpers):
    assert helpers["_license_datetime"](rev1_license(), "expiration_date") == EXPIRES


def test_top_level_wins_when_both_are_present(helpers):
    """If a revision ever reports both, the license's own value is authoritative."""
    other = datetime(2030, 1, 1, tzinfo=timezone.utc)
    license_data = rev1_license()
    license_data.instance_license_summary = Summary(expiration_date=other)

    assert helpers["_license_datetime"](license_data, "expiration_date") == EXPIRES


def test_unset_top_level_falls_through_to_the_summary(helpers):
    """1.2-rev1 declares the field but can still leave it UNSET."""
    license_data = rev2_license()
    license_data.expiration_date = UNSET

    assert helpers["_license_datetime"](license_data, "expiration_date") == EXPIRES


def test_socket_summary_is_used_when_there_is_no_instance_summary(helpers):
    license_data = License(socket_license_summary=Summary(expiration_date=EXPIRES))

    assert helpers["_license_datetime"](license_data, "expiration_date") == EXPIRES


def test_support_expiration_is_read_the_same_way(helpers):
    license_data = License(
        instance_license_summary=Summary(support_expiration_date=SUPPORT_EXPIRES)
    )

    assert helpers["_license_datetime"](license_data, "support_expiration_date") == SUPPORT_EXPIRES


def test_absent_support_date_stays_none(helpers):
    """A license with no support contract reports nothing; unknown is the honest answer."""
    assert helpers["_license_datetime"](rev2_license(), "support_expiration_date") is None


def test_capacity_only_license_has_no_dates(helpers):
    """CapacityLicenseSummaryModel carries no date fields at all."""
    license_data = License(
        capacity_license_summary=Summary(licensed_capacity_tb=100, used_capacity_tb=10)
    )

    assert helpers["_license_datetime"](license_data, "expiration_date") is None


def test_a_perpetual_license_reports_no_expiry(helpers):
    assert helpers["_license_datetime"](License(), "expiration_date") is None


def test_summary_order_is_instance_then_socket_then_capacity(helpers):
    """A multi-section license can carry several; pick deterministically."""
    assert helpers["LICENSE_SUMMARIES"] == (
        "instance_license_summary",
        "socket_license_summary",
        "capacity_license_summary",
    )


@pytest.mark.parametrize(
    "value,expected",
    [
        ("01234567", "01234567"),
        ("", "Unknown"),
        ("   ", "Unknown"),
        (UNSET, "Unknown"),
        (None, "Unknown"),
    ],
    ids=["set", "empty", "whitespace", "unset", "none"],
)
def test_blank_text_reads_as_unknown(helpers, value, expected):
    """supportId comes back as "" with no support contract, which would show as a blank state."""
    assert helpers["_license_text"](License(support_id=value), "support_id") == expected


def test_licensed_to_survives(helpers):
    assert helpers["_license_text"](rev2_license(), "licensed_to") == "CyberFortress"


@pytest.mark.parametrize(
    "value,unset",
    [(None, True), (UNSET, True), ("", False), (0, False), (EXPIRES, False)],
    ids=["none", "sentinel", "empty-string", "zero", "datetime"],
)
def test_is_unset_only_matches_absence(helpers, value, unset):
    """Zero and empty string are values, not absences."""
    assert helpers["_is_unset"](value) is unset


def test_the_schema_change_is_documented():
    """Whoever reads this next should not have to rediscover why the fallback exists."""
    source = INIT_PATH.read_text(encoding="utf-8")
    docstring = source[source.index("def _license_datetime") :][:900]

    assert "1.2-rev1" in docstring and "1.3-rev" in docstring


# ---------------------------------------------------------------------------
# Instance licensing figures
#
# Fixtures mirror a real 13.1 NFR response: 20 licensed, 11 used, a per-type breakdown, and a
# workload list naming each protected object.
# ---------------------------------------------------------------------------

USAGE_HELPERS = ("_is_unset", "_license_text", "_number_or_none", "_license_instance_usage")


def _lift(names, wanted):
    """Execute a subset of __init__.py's module-level functions and return one of them."""
    tree = ast.parse(INIT_PATH.read_text(encoding="utf-8"))
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert len(nodes) == len(names), f"missing helpers, found {[n.name for n in nodes]}"

    namespace = {}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(INIT_PATH), "exec"), namespace)
    return namespace[wanted]


@pytest.fixture(name="usage", scope="module")
def usage_fixture():
    return _lift(USAGE_HELPERS, "_license_instance_usage")


def instance_license(**overrides):
    summary = Summary(
        package="Suite",
        licensed_instances_number=20,
        used_instances_number=11,
        new_instances_number=0,
        rental_instances_number=0,
        expiration_date=EXPIRES,
        objects=[
            Summary(type_="Virtual Machines", count=10, multiplier=1, used_instances_number=10),
            Summary(type_="Servers", count=1, multiplier=1, used_instances_number=1),
            Summary(type_="File Shares (500 GB)", count=0, multiplier=1, used_instances_number=0),
        ],
        workload=[Summary(name=f"vm-{n}") for n in range(13)],
    )
    summary.__dict__.update(overrides)
    return License(instance_license_summary=summary)


def test_reads_the_licensed_and_used_counts(usage):
    figures = usage(instance_license())

    assert figures["instances_licensed"] == 20
    assert figures["instances_used"] == 11
    assert figures["package"] == "Suite"


def test_computes_the_used_percentage(usage):
    assert usage(instance_license())["instances_used_percent"] == 55.0


def test_percentage_is_omitted_when_the_licence_count_is_zero(usage):
    """An unlimited or malformed licence must not divide by zero."""
    figures = usage(instance_license(licensed_instances_number=0))

    assert "instances_used_percent" not in figures
    assert figures["instances_licensed"] == 0


def test_percentage_is_omitted_when_usage_is_unknown(usage):
    figures = usage(instance_license(used_instances_number=UNSET))

    assert figures["instances_used"] is None
    assert "instances_used_percent" not in figures


def test_keeps_the_per_type_breakdown(usage):
    objects = usage(instance_license())["instance_objects"]

    assert {o["type"] for o in objects} == {
        "Virtual Machines",
        "Servers",
        "File Shares (500 GB)",
    }
    assert next(o for o in objects if o["type"] == "Virtual Machines")["used"] == 10


def test_counts_workloads_without_listing_them(usage):
    """A large estate has thousands of workloads, and attributes are recorded every update."""
    figures = usage(instance_license())

    assert figures["instance_workload_count"] == 13
    assert "workload" not in figures, "the full list must not reach state attributes"


def test_a_socket_or_capacity_licence_reports_no_instance_figures(usage):
    assert usage(License(socket_license_summary=Summary(licensed_sockets_number=4))) == {}
    assert usage(License()) == {}


def test_missing_optional_fields_do_not_break_parsing(usage):
    figures = usage(License(instance_license_summary=Summary(licensed_instances_number=5)))

    assert figures["instances_licensed"] == 5
    assert figures["instances_used"] is None
    assert "instance_objects" not in figures
    assert "instance_workload_count" not in figures


def test_usage_sensors_are_only_created_for_instance_licences():
    """A socket licence would otherwise get instance sensors that read unknown forever."""
    sensor_source = (INIT_PATH.parent / "sensor.py").read_text(encoding="utf-8")

    assert 'get("instances_licensed") is not None' in sensor_source
    assert 'get("instances_used_percent") is not None' in sensor_source


# ---------------------------------------------------------------------------
# "No HA cluster" versus a real cluster failure
# ---------------------------------------------------------------------------


@pytest.fixture(name="cluster_reason", scope="module")
def cluster_reason_fixture():
    return _lift(("_is_unset", "_license_text", "_cluster_absent_reason"), "_cluster_absent_reason")


class ApiError:
    """Shaped like the generated Error model, whose extras carry the HTTP status."""

    def __init__(self, message, status=None, error_code="UnknownError"):
        self.message = message
        self.error_code = error_code
        self.additional_properties = {"status": status} if status else {}


NOT_CONFIGURED = (
    "High Availability cluster is not configured. Configure a cluster before using this operation."
)


def test_a_400_not_configured_is_normal(cluster_reason):
    """The real response from an unclustered server — it must not warn on every poll."""
    unclustered, detail = cluster_reason(ApiError(NOT_CONFIGURED, status=400))

    assert unclustered is True
    assert "not configured" in detail


def test_not_configured_without_a_status_is_still_recognised(cluster_reason):
    unclustered, _ = cluster_reason(ApiError(NOT_CONFIGURED))

    assert unclustered is True


@pytest.mark.parametrize("status", [401, 403, 500])
def test_other_errors_are_reported_not_hidden(cluster_reason, status):
    """Reading an auth or server failure as "no cluster" would bury it in debug."""
    unclustered, detail = cluster_reason(ApiError("Unauthorized", status=status))

    assert unclustered is False
    assert str(status) in detail
