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

> **Note**: The required `veeam-br` Python library is automatically installed by Home Assistant when you add this integration. No manual package installation is needed.

### HACS (Recommended)

1. Open HACS in your Home Assistant instance
2. Click on "Integrations"
3. Click the three dots in the top right corner
4. Select "Custom repositories"
5. Add this repository URL: `https://github.com/Cenvora/ha-veeam-br`
6. Select category: "Integration"
7. Click "Add"
8. Click "Install" on the Veeam Backup & Replication card
9. Restart Home Assistant

### Manual Installation

1. Copy the `custom_components/veeam_br` directory to your Home Assistant's `custom_components` directory
2. Restart Home Assistant

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

The integration creates sensor entities for each backup job:

### Sensor Entity

- **Entity ID**: `sensor.veeam_<job_name>`
- **State**: Current job status (`success`, `running`, `failed`, `warning`, `unknown`)
- **Attributes**:
  - `job_id`: Unique job identifier
  - `job_name`: Display name of the job
  - `job_type`: Type of backup job
  - `last_run`: Timestamp of the last job execution
  - `next_run`: Timestamp of the next scheduled run
  - `last_result`: Result of the last job execution

## Example Automations

### Notify on Backup Failure

```yaml
automation:
  - alias: "Notify on Veeam Backup Failure"
    trigger:
      - platform: state
        entity_id: sensor.veeam_my_backup_job
        to: "failed"
    action:
      - service: notify.notify
        data:
          title: "Veeam Backup Failed"
          message: "Backup job {{ trigger.to_state.attributes.job_name }} has failed!"
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
            {% for state in states.sensor | selectattr('entity_id', 'search', 'veeam_') %}
              {{ state.attributes.job_name }}: {{ state.state }}
            {% endfor %}
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
