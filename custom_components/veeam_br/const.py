"""Constants for the Veeam Backup & Replication integration."""

import importlib.util
import logging
import os
import re

DOMAIN = "veeam_br"
DEFAULT_NAME = "Veeam Backup & Replication"

# Configuration keys
CONF_VERIFY_SSL = "verify_ssl"
CONF_API_VERSION = "api_version"

# Defaults
DEFAULT_PORT = 9419
DEFAULT_VERIFY_SSL = True
DEFAULT_API_VERSION = "1.3-rev1"

_LOGGER = logging.getLogger(__name__)


def _discover_api_versions() -> dict[str, str]:
    """Dynamically discover available API versions from the veeam-br package.

    Returns:
        dict: Mapping of display version (e.g., "1.2-rev1") to module name (e.g., "v1_2_rev1")
    """
    versions = {}

    try:
        # Find the veeam_br package
        spec = importlib.util.find_spec("veeam_br")
        if spec is None:
            _LOGGER.warning("veeam_br package not found, using default API versions")
            return {
                "1.2-rev1": "v1_2_rev1",
                "1.3-rev0": "v1_3_rev0",
                "1.3-rev1": "v1_3_rev1",
            }

        # Get the package directory (handle namespace packages)
        if spec.submodule_search_locations:
            veeam_br_path = spec.submodule_search_locations[0]
        elif spec.origin:
            veeam_br_path = os.path.dirname(spec.origin)
        else:
            _LOGGER.warning("Could not determine veeam_br package path, using defaults")
            return {
                "1.2-rev1": "v1_2_rev1",
                "1.3-rev0": "v1_3_rev0",
                "1.3-rev1": "v1_3_rev1",
            }

        # Pattern to match version directories: v{major}_{minor}_rev{revision}
        api_version_pattern = re.compile(r"^v(\d+)_(\d+)_rev(\d+)$")

        # Scan for version directories
        for item in os.listdir(veeam_br_path):
            item_path = os.path.join(veeam_br_path, item)
            if os.path.isdir(item_path) and api_version_pattern.match(item):
                match = api_version_pattern.match(item)
                if match:
                    major, minor, rev = match.groups()
                    # Convert to display format: "1.2-rev1"
                    display_version = f"{major}.{minor}-rev{rev}"
                    versions[display_version] = item

        if not versions:
            _LOGGER.warning("No API versions found in veeam_br package, using defaults")
            return {
                "1.2-rev1": "v1_2_rev1",
                "1.3-rev0": "v1_3_rev0",
                "1.3-rev1": "v1_3_rev1",
            }

        _LOGGER.debug("Discovered API versions: %s", list(versions.keys()))

    except Exception as err:
        _LOGGER.warning("Failed to discover API versions: %s, using defaults", err)
        return {
            "1.2-rev1": "v1_2_rev1",
            "1.3-rev0": "v1_3_rev0",
            "1.3-rev1": "v1_3_rev1",
        }

    return versions


# API Version options - dynamically discovered from veeam-br package
API_VERSIONS = _discover_api_versions()

# Update interval
UPDATE_INTERVAL = 60  # seconds
