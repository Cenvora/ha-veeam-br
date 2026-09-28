"""Device names: "VBR <kind> <name>", with the kind left out when the name already says it.

The VB365 integration follows the same rule with its own prefix, so a repository both products
call "Default Backup Repository" gets two distinct entity IDs.
"""

import pytest

from custom_components.veeam_br.entity import device_name


@pytest.mark.parametrize(
    ("kind", "name", "expected"),
    [
        ("Job", "Nightly VMs", "VBR Job Nightly VMs"),
        ("Repository", "Default Backup Repository", "VBR Default Backup Repository"),
        ("Repository", "temp local repository", "VBR temp local repository"),
        ("Proxy", "VMware Backup Proxy", "VBR VMware Backup Proxy"),
        # A word that merely contains the kind is not the kind
        ("Job", "Jobsite Servers", "VBR Job Jobsite Servers"),
        ("Server", "vbr01", "VBR Server vbr01"),
        ("License", None, "VBR License"),
        ("License", "", "VBR License"),
    ],
)
def test_device_name(kind, name, expected):
    assert device_name(kind, name) == expected
