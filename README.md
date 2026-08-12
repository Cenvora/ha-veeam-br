<h1 align="center">
<br>
<img src="https://raw.githubusercontent.com/Cenvora/ha-veeam-br/main/media/Veeam_logo_2024_RGB_main_20.png"
     alt="Veeam Logo"
     height="100">
<br>
<br>
Veeam Backup & Replication Integration for Home Assistant
</h1>

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)

A Home Assistant custom integration that monitors Veeam Backup & Replication servers. This integration provides real-time monitoring of backup jobs and their status directly in Home Assistant. 

This project is an independent, open source project. It is not affiliated with, endorsed by, or sponsored by Veeam Software.

## Features

- 🔧 **UI Configuration Flow**: Easy setup through Home Assistant's UI
- 📊 **Job Monitoring**: Track all backup jobs and their current status
- 🔄 **Automatic Updates**: Polls the Veeam server every 60 seconds
- 🎨 **Dynamic Icons**: Visual indicators based on job status (success, running, failed, warning)
- 📱 **Rich Attributes**: Detailed information including last run, next run, and job type

## Requirements

- Home Assistant 2023.1.0 or newer
- Veeam Backup & Replication server with REST API enabled (Community Edition not supported)

### Supported API Versions

The **API Version** option selects the REST API revision used against your server. It
defaults to the newest revision; on an older server, pick the revision matching your VBR
release. Veeam also serves older revisions to newer servers, so a lower revision is a safe
choice if you are unsure.

| VBR Version | API Version | Notes |
| ----------- | ----------- | ----- |
| 13.1.0.411  | `1.3-rev2`  | Default |
| 13.0.1.180  | `1.3-rev1`  | |
| 13.0.0.4967 | `1.3-rev0`  | |
| 12.3.1.1139 | `1.2-rev1`  | |

Older VBR releases are not supported. The list is discovered at runtime from the installed
[veeam-br](https://github.com/Cenvora/veeam-br) library, so it reflects whichever revisions
that version ships (`1.3-rev2` requires veeam-br 0.3.0 or newer).

## Installation
### HACS (Recommended)

Have [HACS](https://hacs.xyz/) installed, this will allow you to update easily.

* Adding ha-veeam-br to HACS can be using this button:

[![image](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Cenvora&repository=ha-veeam-br&category=integration)

> [!NOTE]
> If the button above doesn't work, add `https://github.com/Cenvora/ha-veeam-br` as a custom repository of type Integration in HACS.

* Click install on the `Veeam Backup & Replication` integration.
* Restart Home Assistant.

<details><summary>Manual Install</summary>

* Copy the `ha-veeam-br`  folder from [latest release](https://github.com/Cenvora/ha-veeam-br/releases/latest) to the [`custom_components` folder](https://developers.home-assistant.io/docs/creating_integration_file_structure/#where-home-assistant-looks-for-integrations) in your config directory.
* Restart the Home Assistant.
</details>

## Configuration

### Configuration Parameters

The integration supports the following configuration options:

#### Required Parameters
- **Host**: Your Veeam Backup & Replication server hostname or IP address
- **Port**: REST API port (default: 9419)
- **Username**: Account with administrator privileges on the Veeam server
- **Password**: Password for the specified user account

#### Optional Parameters
- **Verify SSL**: Enable/disable SSL certificate verification (default: enabled)
  - Disable only if using self-signed certificates in a trusted environment
- **API Version**: Select the Veeam REST API version to use (configured via integration options)

### Via UI (Recommended)

1. Go to **Settings** → **Devices & Services**
2. Click **+ Add Integration**
3. Search for "Veeam Backup & Replication"
4. Enter your Veeam server details:
   - **Host**: Your Veeam server hostname or IP address
   - **Port**: REST API port (default: 9419)
   - **Username**: Veeam server username
   - **Password**: Veeam server password
   - **Verify SSL**: Whether to verify SSL certificates (recommended: enabled)
5. Click **Submit**

### Reconfiguration

To update the integration settings:

1. Go to **Settings** → **Devices & Services**
2. Find the **Veeam Backup & Replication** integration
3. Click the three dots menu (⋮) and select **Reconfigure**
4. Update any settings as needed
5. Click **Submit**

### Re-authentication

If credentials expire or change:

1. Home Assistant will automatically prompt for re-authentication
2. Enter the new **Username** and **Password**
3. Click **Submit**

The integration will reconnect without losing any device or entity configurations.

## Data Updates

The integration polls the Veeam Backup & Replication server every **60 seconds** to retrieve:
- Job status and statistics
- Repository information and capacity
- Server information and health status
- License details and expiration dates

**Update Behavior:**
- **New jobs/repositories**: Automatically detected and added as new devices
- **Status changes**: Reflected within the next polling cycle (60 seconds)
- **Failed connections**: Integration marks entities as unavailable and logs the error
- **Connection recovery**: Entities automatically become available when connection restored

## Entities

The integration creates devices for each monitored object (jobs, repositories, server, license), with multiple sensor entities per device:

### Job Devices

Each backup job creates a device with the following sensors:

- **Status Sensor**: `sensor.<job_name>_status`
  - State: Current job status (`success`, `running`, `failed`, `warning`, `unknown`)
- **Type Sensor**: `sensor.<job_name>_type`
  - State: Type of backup job
- **Last Run Sensor**: `sensor.<job_name>_last_run`
  - State: Timestamp of the last job execution
- **Next Run Sensor**: `sensor.<job_name>_next_run`
  - State: Timestamp of the next scheduled run

### Other Devices

The integration also creates devices for:
- **Repositories**: Each repository device has sensors for type, capacity, free space, used space, online status, etc., and a rescan button.
- **Scale-Out Backup Repositories (SOBRs)**: Each SOBR device has sensors for description, extent count, and buttons for each extent to enable/disable sealed mode and maintenance mode.
- **Server**: Server device has sensors for build version, platform, database info, etc.
- **License**: License device has sensors for status, edition, expiration dates, etc.

## Example Automations

### Notify on Backup Failure

```yaml
automation:
  - alias: "Notify on Veeam Backup Failure"
    trigger:
      - platform: state
        entity_id: sensor.my_backup_job_status
        to: "failed"
    action:
      - service: notify.notify
        data:
          title: "Veeam Backup Failed"
          message: "Backup job {{ trigger.to_state.name | replace(' Status', '') }} has failed!"
```

### Daily Backup Status Report

```yaml
automation:
  - alias: "Daily Veeam Status Report"
    trigger:
      - platform: time
        at: "08:00:00"
    action:
      - service: notify.notify
        data:
          title: "Veeam Backup Status"
          message: >
            {% set ns = namespace(jobs=[]) %}
            {% for sensor in states.sensor %}
              {% if sensor.entity_id.endswith('_status') and device_attr(sensor.entity_id, 'manufacturer') == 'Veeam' and device_attr(sensor.entity_id, 'model') == 'Backup Job' %}
                {% set ns.jobs = ns.jobs + [sensor.name | replace(' Status', '') ~ ': ' ~ sensor.state] %}
              {% endif %}
            {% endfor %}
            {{ ns.jobs | join('\n') if ns.jobs else 'No Veeam backup jobs found.' }}
```

## Removal

To remove the integration from Home Assistant:

1. Go to **Settings** → **Devices & Services**
2. Find the **Veeam Backup & Replication** integration
3. Click the three dots menu (⋮) and select **Delete**
4. Confirm the deletion

All devices and entities associated with this integration will be removed.

## Troubleshooting

### Connection Issues

**Problem**: Integration fails to connect to Veeam server

**Solutions**:
- Verify the Veeam server is running and accessible from Home Assistant
- Check that the REST API is enabled on the Veeam server
- Confirm the hostname/IP and port (default: 9419) are correct
- Ensure firewall rules allow traffic on port 9419
- Try disabling SSL verification if using self-signed certificates

### Authentication Failures

**Problem**: Invalid credentials error during setup or re-authentication

**Solutions**:
- Verify the username and password are correct
- Ensure the account has administrator privileges on the Veeam server
- Check if account is locked or password has expired
- Try logging in to the Veeam console with the same credentials

### Missing Entities

**Problem**: Some jobs or repositories don't appear as entities

**Solutions**:
- Wait for the next polling cycle (60 seconds)
- Restart Home Assistant to force a full refresh
- Check the Home Assistant logs for API errors
- Verify the jobs/repositories exist in Veeam console

### Entities Unavailable

**Problem**: Entities show as "unavailable"

**Solutions**:
- Check network connectivity to the Veeam server
- Review Home Assistant logs for connection errors
- Verify the Veeam server and REST API are running
- Try re-authenticating the integration

### High API Load

**Problem**: Veeam server experiencing high API load

**Solutions**:
- The integration uses `PARALLEL_UPDATES = 1` to limit concurrent requests
- Polling interval is set to 60 seconds to balance freshness and load
- Consider adjusting via code if needed for very large deployments

## Known Limitations

- **Veeam Community Edition**: Not supported (lacks REST API)
- **API Version Compatibility**: Requires Veeam B&R 12.1 or newer
- **Stale Devices**: Deleted jobs/repositories remain as devices until manual removal (planned enhancement)
- **Large Deployments**: Polling 100+ jobs may take several seconds per cycle
- **Real-time Updates**: Changes reflected every 60 seconds, not immediately
- **SSL Certificates**: Self-signed certificates require SSL verification to be disabled
- **Null Timestamps**: Veeam's published API schema declares timestamps such as
  `lastRun`/`nextRun` as non-nullable, but the server sends `null` for jobs that are
  *Not scheduled* or have never run. The `veeam-br` models are generated from that schema, so
  they reject the whole jobs response ([#83](https://github.com/Cenvora/ha-veeam-br/issues/83)).
  The integration patches the affected models at startup to read a null timestamp as absent.
- **API Version Must Match New Workloads**: the client rejects enum values it does not know, and
  the jobs response is parsed as a whole. If the server returns a job type the selected API
  version predates — a Proxmox VE or Nutanix AHV job on 13.1 read over `1.3-rev1`, for example —
  *all* job entities become unavailable, not just that job. Selecting the API version matching
  your server avoids this.

## Supported Devices & Functions

### Supported Veeam Objects

The integration monitors the following Veeam objects:

- ✅ **Backup Jobs** - All job types (Backup, Replica, Copy, etc.), including the Proxmox VE
  and Nutanix AHV job types added in Veeam B&R 13.1 (requires the `1.3-rev2` API version)
- ✅ **Repositories** - Standard backup repositories
- ✅ **Scale-Out Repositories** - SOBR and extents
- ✅ **Server Information** - Veeam server details
- ✅ **License Information** - License status and expiration

### Supported Entities

- **Sensors**: Status, type, timestamps, capacity, statistics
- **Binary Sensors**: Online/offline, connectivity, update available
- **Buttons**: Repository rescan, extent maintenance/sealed mode, start/stop/enable/disable job

### Unsupported (Future Enhancements)

- ⏳ Tape libraries and media
- ⏳ Cloud repositories
- ⏳ SureBackup jobs
- ⏳ Instant VM Recovery sessions

## Support

- **Issues**: [GitHub Issues](https://github.com/Cenvora/ha-veeam-br/issues)
- **Documentation**: This README and inline code documentation

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

### Development Setup

To set up the development environment:

```bash
# Install development dependencies
pip install black isort flake8 mypy pre-commit

# Install pre-commit hooks (optional but recommended)
pre-commit install
```

### Code Quality

This project uses automated testing and formatting:

- **Black**: Code formatting (line length: 100)
- **isort**: Import sorting
- **flake8**: Linting
- **mypy**: Type checking
- **HACS Action**: HACS integration validation
- **Hassfest**: Home Assistant manifest validation

Run formatting and checks locally:

```bash
# Format code
black custom_components/
isort custom_components/

# Run linting
flake8 custom_components/

# Type checking
mypy custom_components/ --ignore-missing-imports

# Validate JSON
python -m json.tool custom_components/veeam_br/manifest.json
```

### CI/CD

All pull requests are automatically validated with:
- Python code formatting (Black, isort)
- Linting (flake8)
- Type checking (mypy)
- HACS validation
- Home Assistant manifest validation (hassfest)
- JSON schema validation

### Release Process

The version in `manifest.json` is automatically updated when a new release tag is created:

1. Create and push a tag with the format `v*` (e.g., `v1.0.0`, `v0.3.1b3`)
   ```bash
   git tag v1.0.0
   git push origin v1.0.0
   ```
   
   **Note:** Tags should be created from the default branch to ensure consistency.

2. The GitHub Actions workflow automatically:
   - Extracts the version from the tag (removes the `v` prefix)
   - Updates the `version` field in `custom_components/veeam_br/manifest.json`
   - Commits and pushes the change to the default branch

3. The updated manifest.json is now ready for the release

## License

This project is licensed under the terms included in the LICENSE file.

## Credits

This integration uses the [veeam-br](https://github.com/Cenvora/veeam-br) Python library for communication with Veeam Backup & Replication servers. 


## 🤝 Core Contributors
This project is made possible thanks to the efforts of our core contributors:

- [Jonah May](https://github.com/JonahMMay)  
- [Maurice Kevenaar](https://github.com/mkevenaar)  

We’re grateful for their continued support and contributions.
