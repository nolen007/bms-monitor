# BMS Battery Monitor

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-MQTT-blue.svg)](https://www.home-assistant.io/)

A Python-based monitoring solution for **multiple BMS types** including EG4, ECO-WORTHY, and JK BMS batteries:
- 🌐 **Web GUI Dashboard** - Beautiful real-time monitoring in your browser
- 🏠 **Home Assistant Integration** - MQTT auto-discovery with all 16 cell voltages
- 📊 **Terminal UI** - Console-based monitoring option

![Web Dashboard](docs/images/web-dashboard.png)

## Features

- 🔋 **Real-time Battery Monitoring** - Poll battery data every 30 seconds (configurable)
- 🌐 **Web Dashboard** - Modern, responsive web UI accessible from any device
- 🏠 **Home Assistant Integration** - MQTT auto-discovery for all sensors including individual cell voltages
- ⚠️ **Alarm Detection** - Automatic alerts for over/under voltage, temperature, and cell imbalance
- 📈 **Cell-level Monitoring** - All 16 individual cell voltages with min/max highlighting
- 🔌 **Multi-BMS Support** - EG4, ECO-WORTHY/PACE, and JK BMS PB series

## Supported Hardware

- **EG4 Batteries**: WallMount Indoor 280Ah (14.3kWh) and compatible models
- **ECO-WORTHY Batteries**: 314Ah and other PACE BMS equipped batteries
- **JK BMS**: PB series Inverter BMS with touchscreen
- **Adapter**: Waveshare RS485 TO ETH (B) or similar RS485-to-Ethernet converters
- **Protocol**: Modbus RTU over TCP

---

## Installation

### Step 1: Clone the Repository

```bash
git clone https://github.com/nolen007/eg4-battery-monitor.git
cd eg4-battery-monitor
```

### Step 2: Create a Virtual Environment (Recommended)

```bash
python3 -m venv venv
source venv/bin/activate
```

### Step 3: Install the Package

```bash
pip install -e .
```

This installs the `eg4-monitor` command that you can run from anywhere.

### Step 4: Verify Installation

```bash
eg4-monitor --help
```

---

## Quick Start

### Run with Command-Line Arguments

```bash
eg4-monitor \
  --battery-ip 192.168.130.139 \
  --mqtt-broker 192.168.128.149 \
  --mqtt-user blake \
  --mqtt-pass YourPassword
```

Then open your browser to: **http://localhost:5000**

### Run with Config File

```bash
cp config.example.yaml config.yaml
nano config.yaml  # Edit with your settings
eg4-monitor --config config.yaml
```

---

## Web Dashboard

The web dashboard is automatically available at `http://your-server-ip:5000`

Features:
- **Real-time updates** every 5 seconds
- **SOC gauge** with color-coded status
- **Power flow** visualization (charging/discharging)
- **All 16 cell voltages** with min/max highlighting
- **Alarm display** when issues detected
- **Mobile responsive** design
- **Time-to-low-SOC countdown** — estimated runtime at the current draw until the pack (or each battery) hits a configurable cutoff (`monitor.low_soc_cutoff` in the config file, e.g. `15` for a 15% reserve; `0` for fully empty)
- **Solar & battery savings** — optional, reads PV/grid power and your electricity rate from Home Assistant (`home_assistant` section in the config file) each poll and logs it to a local SQLite file, showing today's and lifetime $ saved from solar/battery vs. $ spent on grid. Battery savings are net of any grid-charging cost (via `ac_charge_mode_entity`) — charging from solar surplus is free, charging from the grid isn't
- **Tracked circuits & monthly bill estimate** — track cost for any extra Home Assistant power sensor (EV charger, AC compressor, whole-home main panel, ...) via `home_assistant.circuits`; the circuit marked `is_main: true` also gets a projected monthly bill based on month-to-date cost
- **History page** (`/history`, linked from the dashboard) — the same solar/battery/grid/circuit breakdown, over Today/Yesterday/This Week/This Month/Last 30 Days/This Year/All Time, plus a day-by-day table for trend-spotting

### Disable Web GUI

If you only want terminal UI or headless mode:

```bash
eg4-monitor --no-web
```

### Change Web Port

```bash
eg4-monitor --web-port 8080
```

---

## Home Assistant Integration

The monitor publishes MQTT discovery messages automatically. All sensors appear in Home Assistant under **Settings → Devices & Services → MQTT**.

### Sensors Created

| Entity ID | Description |
|-----------|-------------|
| `sensor.eg4_wallmount_280ah_soc` | State of Charge (%) |
| `sensor.eg4_wallmount_280ah_voltage` | Pack Voltage (V) |
| `sensor.eg4_wallmount_280ah_current` | Current (A) |
| `sensor.eg4_wallmount_280ah_power` | Power (W) |
| `sensor.eg4_wallmount_280ah_temperature` | Temperature (°C) |
| `sensor.eg4_wallmount_280ah_remaining_kwh` | Remaining Energy (kWh) |
| `sensor.eg4_wallmount_280ah_cell_1_voltage` | Cell 1 Voltage (V) |
| `sensor.eg4_wallmount_280ah_cell_2_voltage` | Cell 2 Voltage (V) |
| ... | ... |
| `sensor.eg4_wallmount_280ah_cell_16_voltage` | Cell 16 Voltage (V) |
| `sensor.eg4_wallmount_280ah_cell_delta` | Cell Imbalance (mV) |
| `binary_sensor.eg4_wallmount_280ah_online` | Connection Status |

---

## Wiring

Connect your EG4 battery to the Waveshare RS485-to-ETH adapter:

| RJ45 Pin | Wire Color (T568B) | Waveshare Terminal |
|----------|-------------------|-------------------|
| Pin 1    | White/Orange      | 485A (A+)         |
| Pin 2    | Orange            | 485B (B-)         |

See [docs/WIRING.md](docs/WIRING.md) for detailed instructions.

---

## Command-Line Options

```
Usage: eg4-monitor [OPTIONS]

Battery Settings:
  --battery-ip IP        Battery/adapter IP [default: 192.168.130.139]
  --battery-port PORT    Modbus TCP port [default: 4196]
  --device-id ID         Modbus device ID [default: 1]

MQTT Settings:
  --mqtt-broker HOST     MQTT broker address [default: localhost]
  --mqtt-port PORT       MQTT broker port [default: 1883]
  --mqtt-user USER       MQTT username
  --mqtt-pass PASS       MQTT password

Web Server Settings:
  --web-port PORT        Web server port [default: 5000]
  --no-web               Disable web GUI

Monitor Settings:
  --interval SEC         Poll interval in seconds [default: 30]
  --no-ui                Disable terminal UI
  --json                 Output single JSON reading and exit

General:
  -c, --config FILE      Path to YAML config file
  --debug                Enable debug logging
  -V, --version          Show version and exit
```

---

## Running as a Service

### Using systemd (Linux)

```bash
sudo cp examples/eg4-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable eg4-monitor
sudo systemctl start eg4-monitor
```

### Using Docker

```bash
docker build -t eg4-monitor .
docker run -d \
  --name eg4-monitor \
  --restart unless-stopped \
  -p 5000:5000 \
  -e EG4_BATTERY_IP=192.168.130.139 \
  -e EG4_MQTT_BROKER=192.168.128.149 \
  -e EG4_MQTT_USER=blake \
  -e EG4_MQTT_PASS=YourPassword \
  eg4-monitor
```

---

## Configuration File

Create `config.yaml`:

```yaml
battery:
  ip: "192.168.130.139"
  port: 4196
  device_id: 1

mqtt:
  broker: "192.168.128.149"
  port: 1883
  username: "blake"
  password: "your-password"
  base_topic: "homeassistant"

web:
  enabled: true
  host: "0.0.0.0"
  port: 5000

monitor:
  interval: 30
  ui_enabled: false  # Disable terminal UI when running as service
```

---

## Troubleshooting

### "command not found: eg4-monitor"

Activate the virtual environment:
```bash
source venv/bin/activate
```

### Web GUI not accessible

- Check firewall allows port 5000
- Try `--web-port 8080` if 5000 is in use
- Ensure you're using the correct IP address

### No battery data

1. Check wiring: Pin 1 → 485A, Pin 2 → 485B
2. Try swapping A/B wires
3. Verify battery is set to RS485 mode, address 1

---

## License

MIT License - see [LICENSE](LICENSE) for details.

## Disclaimer

This project is not affiliated with or endorsed by EG4 Electronics. Use at your own risk.
