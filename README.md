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

## License

This project is licensed under the terms included in the LICENSE file.

## Credits

This integration uses the [veeam-br](https://github.com/Cenvora/veeam-br) Python library for communication with Veeam Backup & Replication servers. The library is automatically installed by Home Assistant when you add this integration - no manual installation required.
