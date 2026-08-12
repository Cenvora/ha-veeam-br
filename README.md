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
- 🔎 **API Version Auto-Detection**: Finds the newest REST API revision your server serves
- 📊 **Job Monitoring**: Track all backup jobs and their current status
- 🔄 **Automatic Updates**: Polls the Veeam server every 60 seconds
- 🎨 **Dynamic Icons**: Visual indicators based on job status (success, running, failed, warning)
- 📱 **Rich Attributes**: Detailed information including last run, next run, and job type
- 🔀 **High Availability**: Monitor a clustered server and automate switchover or failover

## Requirements

- Home Assistant 2026.1 or newer
- Veeam Backup & Replication server with the REST API enabled, on a supported license
  (see [Licensing](#licensing))

### Supported API Versions

The **API Version** option selects the REST API revision used against your server. It
defaults to **auto**, which probes the server and picks the newest revision it serves, so you
do not need to know your VBR build. Pick a specific revision to pin it instead.

Detection works by asking the server which Swagger documents it publishes — Veeam's REST API
has no endpoint that reports its own supported versions, and
[Veeam's guidance](https://community.veeam.com/discussion-boards-66/for-veeam-backup-and-replication-rest-apis-x-api-version-header-for-example-1-3-rev1-can-be-obtained-from-a-rest-api-13741)
is that the caller chooses one. If the Swagger endpoints are unreachable or disabled,
detection is skipped and the newest supported revision is used; select a revision manually if
that is wrong for your server.

*auto* is stored as a standing preference, not resolved away: it is re-evaluated on every
startup and reload. Upgrading VBR, or updating `veeam-br` to a release that adds a newer
revision, moves the entry onto the newer revision by itself — no reconfiguration needed. Pick a
specific revision instead if you want it pinned.

> [!NOTE]
> A newer revision can rename enum values and add fields. That is the trade for automatic
> upgrades: if a revision ever changes something this integration reads, *auto* will adopt it on
> the next restart. Pin a version if you would rather adopt those changes deliberately. The
> resolved revision is logged at startup and shown in the diagnostics download.

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
- **Port**: REST API port (default: 443). Veeam B&R 13.1 and newer serve the REST API on
  443; use 9419 for older releases
- **Username**: Account with administrator privileges on the Veeam server
- **Password**: Password for the specified user account

#### Optional Parameters
- **Verify SSL**: Enable/disable SSL certificate verification (default: enabled)
  - Disable only if using self-signed certificates in a trusted environment
- **API Version**: REST API revision to use. Defaults to *auto*, which detects the newest
  revision the server serves (configured via integration options)

### Via UI (Recommended)

1. Go to **Settings** → **Devices & Services**
2. Click **+ Add Integration**
3. Search for "Veeam Backup & Replication"
4. Enter your Veeam server details:
   - **Host**: Your Veeam server hostname or IP address
   - **Port**: REST API port (default: 443, or 9419 on releases before 13.1)
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
  - State: What the job is doing (`Running`, `Inactive`, `Disabled`) — the pass/fail
    outcome of the last run is on the **Last Result** sensor, not here
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
- **License**: License device has sensors for status, edition, expiration dates, and — on
  instance-based licences — instances licensed, instances used and percentage used, with the
  per-workload-type breakdown as attributes.
- **Backup Proxies**: each proxy device has online, enabled and out-of-date sensors, a type
  sensor carrying its host as an attribute, and enable/disable buttons for taking a proxy out
  of service during maintenance.
- **WAN Accelerators**: cache size, with the cache folder, traffic port, stream count and
  high-bandwidth mode as attributes.
- **High Availability Cluster**: on a clustered Veeam B&R 13.1 server (API `1.3-rev2`), a
  cluster device with online and failover-in-progress sensors, cluster endpoint and last-online
  diagnostics, per-node replication state, Patroni role and replication lag, plus switchover
  and failover buttons. See [High Availability](#high-availability) below.

## High Availability

Veeam B&R 13.1 exposes its High Availability cluster over the REST API. Select the
`1.3-rev2` API version and, if the server is clustered, a **High Availability Cluster**
device appears. Servers that are not clustered get no cluster device and no extra polling.

### Entities

| Entity | Notes |
| ------ | ----- |
| Online | Connectivity of the cluster as a whole |
| Failover In Progress | Use this to gate automations, not just to observe |
| Maintenance In Progress | Diagnostic |
| Last Online | Diagnostic timestamp |
| Cluster Endpoint | Diagnostic; DNS name, cross-subnet mode and endpoint migration as attributes |
| Primary / Secondary Node State | Patroni state, e.g. `Running`, `Streaming`, `Crashed` |
| Primary / Secondary Node Role | `Leader`, `Replica`, `StandbyLeader`, `SyncStandby` |
| Primary / Secondary Node Lag | Replication lag in MB |

### Switchover vs failover

- **Switchover** is the planned, graceful role swap. It is the operation to automate, and it
  keeps Veeam's replication-lag check in force, so the server refuses to promote a badly
  lagging secondary.
- **Failover** promotes the secondary without waiting for the primary. It is for when the
  primary is already gone.

Home Assistant buttons fire immediately with no confirmation step, so the **Failover** entity
is **disabled by default** — enable it deliberately if you intend to automate it. Both buttons
report unavailable while a failover or endpoint migration is already running.

> [!WARNING]
> Both operations move the active role of your backup infrastructure. Treat these buttons as
> you would a power switch on the server itself, and prefer triggering switchover from an
> automation with its own conditions over exposing the button on a dashboard.

### Example: alert on an unplanned failover

```yaml
automation:
  - alias: "Veeam HA failover started"
    trigger:
      - platform: state
        entity_id: binary_sensor.vbr_ha_example_com_failover_in_progress
        to: "on"
    action:
      - service: notify.notify
        data:
          title: "Veeam HA failover in progress"
          message: >
            Secondary node lag was
            {{ states('sensor.vbr_ha_example_com_secondary_node_lag') }} MB.
```

## Automation Blueprints

Ready-made automations for the entities this integration creates. Each one asks you to pick
the entities to watch and what to do about it — a notification, a script, anything Home
Assistant can run — so they work with whatever notifier you already use.

Click **Import blueprint**, then create automations from it under
**Settings → Automations & scenes → Blueprints**.

> [!NOTE]
> Blueprints are not installed by HACS — Home Assistant has no mechanism for an integration to
> ship them, and HACS has no blueprint category. The import links below fetch them from this
> repository directly.

### Backup job failed

Notifies when a job's **Last Result** turns Failed (optionally Warning too).

[![Import blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2FCenvora%2Fha-veeam-br%2Fmain%2Fblueprints%2Fautomation%2Fveeam_br%2Fjob_failed.yaml)

<sub>Source: [`job_failed.yaml`](blueprints/automation/veeam_br/job_failed.yaml)</sub>

### Repository running out of space

Fires when a repository crosses a used-space threshold, with an optional recovery notification.

[![Import blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2FCenvora%2Fha-veeam-br%2Fmain%2Fblueprints%2Fautomation%2Fveeam_br%2Frepository_space_low.yaml)

<sub>Source: [`repository_space_low.yaml`](blueprints/automation/veeam_br/repository_space_low.yaml)</sub>

### HA cluster failover or outage

Fires when a High Availability cluster starts failing over or stops reporting itself online.

[![Import blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2FCenvora%2Fha-veeam-br%2Fmain%2Fblueprints%2Fautomation%2Fveeam_br%2Fha_cluster_failover.yaml)

<sub>Source: [`ha_cluster_failover.yaml`](blueprints/automation/veeam_br/ha_cluster_failover.yaml)</sub>

### License expiring soon

Daily reminder once a license or support contract is within N days of expiring.

[![Import blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2FCenvora%2Fha-veeam-br%2Fmain%2Fblueprints%2Fautomation%2Fveeam_br%2Flicense_expiring.yaml)

<sub>Source: [`license_expiring.yaml`](blueprints/automation/veeam_br/license_expiring.yaml)</sub>

### Daily backup summary

One digest a day: how many jobs succeeded, warned or failed, and which need attention.

[![Import blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2FCenvora%2Fha-veeam-br%2Fmain%2Fblueprints%2Fautomation%2Fveeam_br%2Fdaily_backup_summary.yaml)

<sub>Source: [`daily_backup_summary.yaml`](blueprints/automation/veeam_br/daily_backup_summary.yaml)</sub>

### Backup proxy offline

Fires when a proxy stays offline, and optionally when it returns. Can ignore proxies you have deliberately disabled.

[![Import blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2FCenvora%2Fha-veeam-br%2Fmain%2Fblueprints%2Fautomation%2Fveeam_br%2Fproxy_offline.yaml)

<sub>Source: [`proxy_offline.yaml`](blueprints/automation/veeam_br/proxy_offline.yaml)</sub>

### A note on which sensor to pick

For job automations, use the **Last Result** sensor, not **Status**. Status reports what the
job is doing (`Running`, `Inactive`, `Disabled`); the pass/fail outcome of the last run is on
Last Result (`Success`, `Warning`, `Failed`, `None`). An automation keyed on Status would never
see a failure.

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
- Confirm the hostname/IP and port are correct — 443 on Veeam B&R 13.1 and newer, 9419 on
  older releases. If you get the port wrong, the setup form checks the other one and tells
  you which it found
- Ensure firewall rules allow traffic on the REST API port (443, or 9419 on older releases)
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

## Licensing

This integration is developed and tested against licensed Veeam Backup & Replication
installations (Enterprise, Enterprise Plus, Standard, and evaluation or NFR licenses).

**Community Edition, and servers with no license installed, are not supported.** Entitlements
differ between editions, and some REST API endpoints answer differently or not at all, so
entities can be missing or unreliable in ways that look like integration bugs.

The integration reads the license edition it is already polling for and, if it finds an
unsupported one, raises a warning under **Settings → Repairs** and logs it on every reload.
Nothing is blocked: if the integration works for you on Community Edition, it keeps working.
The warning exists so that unexplained behaviour has an obvious first suspect, and it clears
itself once the server reports a supported license.

If you report a problem, please include the license edition — the diagnostics download
(⋮ → *Download diagnostics*) contains it, along with the API version and library version, and
no credentials.

## Entity States

Values that the REST API reports as identifiers are shown as readable labels:
`EnterprisePlus` becomes *Enterprise Plus*, `ProxmoxBackupJob` becomes *Proxmox Backup Job*,
`WinLocal` becomes *Windows (local)*, and `inactive` becomes *Inactive*. Job status is lower
case on API 1.2-rev1 and capitalised on 1.3-rev*, so labels are matched case-insensitively and
render identically whichever server answers.

Every sensor whose state is a label also carries the untouched API value as a `raw_value`
attribute, so automations that need to match exactly have something stable:

```yaml
{{ state_attr('sensor.nightly_vms_last_result', 'raw_value') == 'Failed' }}
```

> [!NOTE]
> This changed in 0.6.0. Templates comparing against raw identifiers — `== 'EnterprisePlus'`,
> `== 'inactive'` — should switch to `raw_value`, or compare case-insensitively against the
> label. The shipped blueprints already compare lower-cased and were unaffected.

## Known Limitations

- **Veeam Community Edition / unlicensed servers**: Not supported. The integration detects
  this and raises a repair warning, but keeps running — see [Licensing](#licensing).
- **API Version Compatibility**: Requires Veeam B&R 12.1 or newer
- **Stale Devices**: A job or repository deleted in Veeam is removed automatically on the next
  poll. If the server reports none of a kind at all — which is indistinguishable from a failed
  fetch — nothing is pruned automatically; use the device's **Delete** button instead.
- **Large Deployments**: Polling 100+ jobs may take several seconds per cycle
- **Real-time Updates**: Changes reflected every 60 seconds, not immediately
- **SSL Certificates**: Self-signed certificates require SSL verification to be disabled
- **Null Values**: Veeam's published API schema declares many properties non-nullable that the
  server nonetheless sends as `null` — `nextRun` for a job that is *Not scheduled*, `hostId` for
  a repository with no host, `instanceLicenseSummary` when it does not apply. The `veeam-br`
  models are generated from that schema, so each null rejects the entire response for that
  endpoint ([#83](https://github.com/Cenvora/ha-veeam-br/issues/83),
  [#82](https://github.com/Cenvora/ha-veeam-br/issues/82)). The integration patches the models
  at startup to read such nulls as absent values.
- **Multiple Servers**: supported, one config entry per server. Devices are named after the
  server they belong to; if two entries point at the *same* server, their job, repository and
  SOBR devices are shared, since those device identifiers come from Veeam's own object IDs.
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
- ✅ **Backup Proxies** - online state, enabled state, out-of-date components, enable/disable
- ✅ **WAN Accelerators** - cache configuration
- ✅ **High Availability Cluster** - cluster state, node roles and replication lag, with
  switchover and failover actions (Veeam B&R 13.1 and the `1.3-rev2` API version)

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
