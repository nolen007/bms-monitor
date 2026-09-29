"""
Configuration management for EG4 Battery Monitor.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class BatteryConfig:
    """Configuration for a single battery."""
    name: str = "Battery 1"
    ip: str = "192.168.130.139"
    port: int = 4196
    device_id: int = 1
    protocol: str = "eg4"  # eg4, ecoworthy, jkbms, canip

    # CAN over IP options (only used when protocol == "canip")
    can_pack_address: int = 0x40        # Pack address byte (0x40 = pack 1)
    can_length_prefixed: bool = False   # True if bridge adds a 2-byte length prefix
    can_sniff_only: bool = False        # True to listen passively (no poll request)


@dataclass
class CircuitConfig:
    """A single Home Assistant entity tracked for cost (kWh x live rate)."""
    name: str
    entity: str
    # Marks the "whole home" circuit, if any — gets a monthly bill projection
    is_main: bool = False


@dataclass
class Config:
    """Configuration settings for the battery monitor."""
    
    # Battery Settings (list of batteries)
    batteries: list = None
    
    # MQTT Settings
    mqtt_broker: str = "localhost"
    mqtt_port: int = 1883
    mqtt_username: str = ""
    mqtt_password: str = ""
    mqtt_base_topic: str = "homeassistant"
    mqtt_client_id: str = ""
    
    # Web Server Settings
    web_enabled: bool = True
    web_host: str = "0.0.0.0"
    web_port: int = 5000
    
    # Virtual Modbus TCP Server (for Solar Assistant)
    modbus_server_enabled: bool = False
    modbus_server_host: str = "0.0.0.0"
    modbus_server_port: int = 5020
    
    # Monitor Settings
    poll_interval: int = 30
    ui_enabled: bool = True
    debug: bool = False
    # SOC% treated as "empty" for the time-remaining countdown
    # (e.g. 15 to stop at your inverter's low-voltage cutoff; 0 = fully empty)
    low_soc_cutoff: float = 0.0

    # Home Assistant integration (for solar/grid savings tracking)
    ha_enabled: bool = False
    ha_url: str = ""
    ha_token: str = ""
    ha_pv_entity: str = ""
    ha_grid_entity: str = ""
    ha_rate_entity: str = "input_number.electricity_rate"
    ha_ac_charge_mode_entity: str = ""
    ha_circuits: list = None
    energy_db_path: str = "energy_ledger.db"

    def __post_init__(self):
        if self.ha_circuits is None:
            self.ha_circuits = []
        if self.batteries is None:
            # Default single battery for backwards compatibility
            self.batteries = [BatteryConfig()]
    
    @classmethod
    def from_file(cls, path: str | Path) -> "Config":
        """Load configuration from a YAML file."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")
        
        with open(path) as f:
            data = yaml.safe_load(f)
        
        return cls.from_dict(data)
    
    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        """Create config from a dictionary."""
        # Create config without triggering __post_init__ default battery
        config = cls.__new__(cls)
        
        # Set all defaults first
        config.batteries = []
        config.mqtt_broker = "localhost"
        config.mqtt_port = 1883
        config.mqtt_username = ""
        config.mqtt_password = ""
        config.mqtt_base_topic = "homeassistant"
        config.mqtt_client_id = ""
        config.web_enabled = True
        config.web_host = "0.0.0.0"
        config.web_port = 5000
        config.modbus_server_enabled = False
        config.modbus_server_host = "0.0.0.0"
        config.modbus_server_port = 5020
        config.poll_interval = 30
        config.ui_enabled = True
        config.debug = False
        config.low_soc_cutoff = 0.0
        config.ha_enabled = False
        config.ha_url = ""
        config.ha_token = ""
        config.ha_pv_entity = ""
        config.ha_grid_entity = ""
        config.ha_rate_entity = "input_number.electricity_rate"
        config.ha_ac_charge_mode_entity = ""
        config.ha_circuits = []
        config.energy_db_path = "energy_ledger.db"
        
        # Battery settings - support both old single-battery and new multi-battery format
        if "batteries" in data:
            for batt in data["batteries"]:
                config.batteries.append(BatteryConfig(
                    name=batt.get("name", "Battery"),
                    ip=batt.get("ip", "192.168.130.139"),
                    port=batt.get("port", 4196),
                    device_id=batt.get("device_id", 1),
                    protocol=batt.get("protocol", "eg4"),
                    can_pack_address=int(batt.get("can_pack_address", 0x40)),
                    can_length_prefixed=bool(batt.get("can_length_prefixed", False)),
                    can_sniff_only=bool(batt.get("can_sniff_only", False)),
                ))
        elif "battery" in data:
            # Legacy single battery config
            battery = data["battery"]
            config.batteries.append(BatteryConfig(
                name=battery.get("name", "EG4 WallMount 280Ah"),
                ip=battery.get("ip", "192.168.130.139"),
                port=battery.get("port", 4196),
                device_id=battery.get("device_id", 1),
                protocol=battery.get("protocol", "eg4"),
            ))
        else:
            # No battery config, use default
            config.batteries.append(BatteryConfig())
        
        # MQTT settings
        if "mqtt" in data:
            mqtt = data["mqtt"]
            config.mqtt_broker = mqtt.get("broker", config.mqtt_broker)
            config.mqtt_port = mqtt.get("port", config.mqtt_port)
            config.mqtt_username = mqtt.get("username", config.mqtt_username)
            config.mqtt_password = mqtt.get("password", config.mqtt_password)
            config.mqtt_base_topic = mqtt.get("base_topic", config.mqtt_base_topic)
            config.mqtt_client_id = mqtt.get("client_id", config.mqtt_client_id)
        
        # Web settings
        if "web" in data:
            web = data["web"]
            config.web_enabled = web.get("enabled", config.web_enabled)
            config.web_host = web.get("host", config.web_host)
            config.web_port = web.get("port", config.web_port)
        
        # Virtual Modbus server settings (Solar Assistant gateway)
        if "modbus_server" in data:
            ms = data["modbus_server"]
            config.modbus_server_enabled = ms.get("enabled", config.modbus_server_enabled)
            config.modbus_server_host    = ms.get("host",    config.modbus_server_host)
            config.modbus_server_port    = ms.get("port",    config.modbus_server_port)
        
        # Monitor settings
        if "monitor" in data:
            monitor = data["monitor"]
            config.poll_interval = monitor.get("interval", config.poll_interval)
            config.ui_enabled = monitor.get("ui_enabled", config.ui_enabled)
            config.debug = monitor.get("debug", config.debug)
            config.low_soc_cutoff = monitor.get("low_soc_cutoff", config.low_soc_cutoff)

        # Home Assistant integration (solar/grid savings tracking)
        if "home_assistant" in data:
            ha = data["home_assistant"]
            config.ha_enabled = ha.get("enabled", config.ha_enabled)
            config.ha_url = ha.get("url", config.ha_url)
            config.ha_token = ha.get("token", config.ha_token)
            config.ha_pv_entity = ha.get("pv_entity", config.ha_pv_entity)
            config.ha_grid_entity = ha.get("grid_entity", config.ha_grid_entity)
            config.ha_rate_entity = ha.get("rate_entity", config.ha_rate_entity)
            config.ha_ac_charge_mode_entity = ha.get("ac_charge_mode_entity", config.ha_ac_charge_mode_entity)
            config.energy_db_path = ha.get("db_path", config.energy_db_path)
            config.ha_circuits = [
                CircuitConfig(
                    name=c["name"],
                    entity=c["entity"],
                    is_main=c.get("is_main", False),
                )
                for c in ha.get("circuits", [])
            ]

        return config
    
    @classmethod
    def from_env(cls) -> "Config":
        """Load configuration from environment variables."""
        config = cls()
        
        # Battery settings (single battery from env for simplicity)
        config.batteries = [BatteryConfig(
            name=os.getenv("EG4_BATTERY_NAME", "Battery 1"),
            ip=os.getenv("EG4_BATTERY_IP", "192.168.130.139"),
            port=int(os.getenv("EG4_BATTERY_PORT", 4196)),
            device_id=int(os.getenv("EG4_DEVICE_ID", 1)),
            protocol=os.getenv("EG4_PROTOCOL", "eg4"),
        )]
        
        # MQTT settings
        config.mqtt_broker = os.getenv("EG4_MQTT_BROKER", config.mqtt_broker)
        config.mqtt_port = int(os.getenv("EG4_MQTT_PORT", config.mqtt_port))
        config.mqtt_username = os.getenv("EG4_MQTT_USER", config.mqtt_username)
        config.mqtt_password = os.getenv("EG4_MQTT_PASS", config.mqtt_password)
        config.mqtt_base_topic = os.getenv("EG4_MQTT_TOPIC", config.mqtt_base_topic)
        
        # Monitor settings
        config.poll_interval = int(os.getenv("EG4_POLL_INTERVAL", config.poll_interval))
        config.debug = os.getenv("EG4_DEBUG", "").lower() in ("true", "1", "yes")
        config.low_soc_cutoff = float(os.getenv("EG4_LOW_SOC_CUTOFF", config.low_soc_cutoff))
        
        return config
    
    def to_dict(self) -> dict:
        """Export configuration as a dictionary."""
        return {
            "batteries": [
                {
                    "name": b.name,
                    "ip": b.ip,
                    "port": b.port,
                    "device_id": b.device_id,
                    "protocol": b.protocol,
                }
                for b in self.batteries
            ],
            "mqtt": {
                "broker": self.mqtt_broker,
                "port": self.mqtt_port,
                "username": self.mqtt_username,
                "password": "***" if self.mqtt_password else "",
                "base_topic": self.mqtt_base_topic,
            },
            "web": {
                "enabled": self.web_enabled,
                "host": self.web_host,
                "port": self.web_port,
            },
            "monitor": {
                "interval": self.poll_interval,
                "ui_enabled": self.ui_enabled,
                "debug": self.debug,
                "low_soc_cutoff": self.low_soc_cutoff,
            },
            "home_assistant": {
                "enabled": self.ha_enabled,
                "url": self.ha_url,
                "token": "***" if self.ha_token else "",
                "pv_entity": self.ha_pv_entity,
                "grid_entity": self.ha_grid_entity,
                "rate_entity": self.ha_rate_entity,
                "ac_charge_mode_entity": self.ha_ac_charge_mode_entity,
                "db_path": self.energy_db_path,
                "circuits": [
                    {"name": c.name, "entity": c.entity, "is_main": c.is_main}
                    for c in self.ha_circuits
                ],
            },
        }

    def save(self, path: str | Path):
        """Save configuration to a YAML file."""
        path = Path(path)
        data = self.to_dict()
        # Don't save masked password/token
        data["mqtt"]["password"] = self.mqtt_password
        data["home_assistant"]["token"] = self.ha_token
        
        with open(path, "w") as f:
            yaml.dump(data, f, default_flow_style=False)
