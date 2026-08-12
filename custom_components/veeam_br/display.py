"""Turn Veeam's API values into something readable.

The REST API reports enum values as identifiers — ``EnterprisePlus``, ``ProxmoxBackupJob``,
``WinLocal``, ``inactive`` — which is fine for code and poor on a dashboard.

Two rules, in order:

1. An explicit override, for values where splitting the identifier gives the wrong answer:
   ``WinLocal`` is "Windows (local)", not "Win Local".
2. Otherwise split the identifier into words and title-case them, keeping runs of capitals
   intact so ``NutanixAHVBackupJob`` becomes "Nutanix AHV Backup Job".

Matching is case-insensitive on purpose. Veeam is not consistent between revisions — job
status is lower case in 1.2-rev1 and capitalised in 1.3-rev* — and a display layer that
renders the same state two different ways depending on the server is worse than useless.

The raw value is kept alongside the label wherever this is used, so automations that need to
match exactly have something stable to match on.

Kept free of Home Assistant imports so it can be tested directly.
"""

from __future__ import annotations

import re
from typing import Any

# Words that should never be title-cased into something silly
ACRONYMS = frozenset(
    {
        "AD",
        "AHV",
        "API",
        "AWS",
        "CDP",
        "DNS",
        "GB",
        "HA",
        "ID",
        "IP",
        "NAS",
        "NFR",
        "NFS",
        "S3",
        "SMB",
        "SQL",
        "SSH",
        "TB",
        "VM",
        "VMS",
        "VPC",
    }
)

# Values whose split would read badly, or that have an established spelling
OVERRIDES = {
    # License editions and types
    "enterpriseplus": "Enterprise Plus",
    "nfr": "NFR",
    "empty": "No license",
    "unspecified": "Unspecified",
    # Session results. "None" on its own reads as a missing value rather than "never ran"
    "none": "No result",
    # Repository types
    "winlocal": "Windows (local)",
    "linuxlocal": "Linux (local)",
    "linuxhardened": "Linux (hardened)",
    "smbshare": "SMB share",
    "nfsshare": "NFS share",
    "amazons3": "Amazon S3",
    "amazons3external": "Amazon S3 (external)",
    "amazons3glacier": "Amazon S3 Glacier",
    "azureblob": "Azure Blob",
    "azureblobexternal": "Azure Blob (external)",
    "azurearchive": "Azure Archive",
    "azuredatabox": "Azure Data Box",
    "googlecloud": "Google Cloud",
    "s3compatible": "S3 compatible",
    "s3compatible2nas": "S3 compatible (NAS)",
    "ddboost": "Dell Data Domain (DD Boost)",
    "hpestoreonce": "HPE StoreOnce",
    "wasabicloud": "Wasabi Cloud",
    "ibmcloud": "IBM Cloud",
    "scaleout": "Scale-out",
    "cifs": "SMB share",
    "nfs": "NFS share",
    # Patroni node roles and states, from the HA cluster
    "standbyleader": "Standby leader",
    "syncstandby": "Sync standby",
    "initdbfailed": "Initialisation failed",
    "inarchiverecovery": "In archive recovery",
    "creatingreplica": "Creating replica",
    "initializingnewcluster": "Initialising new cluster",
    "startfailed": "Start failed",
    "stopfailed": "Stop failed",
    "restartfailed": "Restart failed",
}

# Split on underscores, hyphens, spaces, lower→upper boundaries, and the end of a run of
# capitals that is followed by a normal word (so "AHVBackup" splits as "AHV" + "Backup")
_SPLIT = re.compile(r"[_\-\s]+|(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def _word(word: str) -> str:
    if word.upper() in ACRONYMS:
        return word.upper()
    if word.isupper() and len(word) > 1:
        # An unlisted all-caps run is probably still an acronym
        return word
    return word[:1].upper() + word[1:]


def humanize(value: Any, default: str | None = None) -> str | None:
    """Return a readable label for an API value.

    Anything that is not a non-empty string — None, the library's UNSET sentinel, a number —
    returns ``default``, so callers can decide between "Unknown" and leaving a sensor empty.
    """
    if not isinstance(value, str):
        return default

    text = value.strip()
    if not text:
        return default

    # Looked up with separators removed too, so "scale-out" and "ScaleOut" agree
    lowered = text.lower()
    override = OVERRIDES.get(lowered) or OVERRIDES.get(re.sub(r"[_\-\s]+", "", lowered))
    if override:
        return override

    words = [word for word in _SPLIT.split(text) if word]
    if not words:
        return default

    return " ".join(_word(word) for word in words)
