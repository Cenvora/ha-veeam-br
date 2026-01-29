"""Constants for the Veeam Backup & Replication integration."""

DOMAIN = "veeam_br"
DEFAULT_NAME = "Veeam Backup & Replication"

# Configuration keys
CONF_HOST = "host"
CONF_PORT = "port"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_VERIFY_SSL = "verify_ssl"

# Defaults
DEFAULT_PORT = 9419
DEFAULT_VERIFY_SSL = True

# Update interval
UPDATE_INTERVAL = 60  # seconds

# Platforms
PLATFORMS = ["sensor"]
