"""
MQTT Publisher with Home Assistant auto-discovery support.
"""

import json
import logging
import time
from typing import Optional, Set, List

import paho.mqtt.client as mqtt

from .config import Config
from .battery import BatteryData
from .modbus_server import aggregate

logger = logging.getLogger(__name__)

AGGREGATE_ID   = "battery_aggregate"
AGGREGATE_NAME = "Battery Bank"


class MQTTPublisher:
    """Publishes battery data to MQTT with Home Assistant auto-discovery."""
    
    def __init__(self, config: Config):
        self.config = config
        self.client: Optional[mqtt.Client] = None
        self._connected = False
        self._discovered_batteries: Set[str] = set()
    
    @property
    def connected(self) -> bool:
        return self._connected
    
    def connect(self) -> bool:
        """Connect to the MQTT broker."""
        try:
            client_id = self.config.mqtt_client_id or f"eg4_monitor_{int(time.time())}"
            
            self.client = mqtt.Client(
                client_id=client_id,
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            )
            
            # Set credentials if provided
            if self.config.mqtt_username:
                self.client.username_pw_set(
                    self.config.mqtt_username,
                    self.config.mqtt_password,
                )
            
            # Set callbacks
            self.client.on_connect = self._on_connect
            self.client.on_disconnect = self._on_disconnect
            
            # Connect
            self.client.connect(
                self.config.mqtt_broker,
                self.config.mqtt_port,
                keepalive=60,
            )
            
            # Start network loop
            self.client.loop_start()
            
            # Wait for connection
            for _ in range(50):  # 5 second timeout
                if self._connected:
                    return True
                time.sleep(0.1)
            
            logger.warning("MQTT connection timeout")
            return False
            
        except Exception as e:
            logger.error(f"MQTT connection error: {e}")
            return False
    
    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        """Handle MQTT connection."""
        if reason_code == 0:
            self._connected = True
            logger.info(f"Connected to MQTT broker at {self.config.mqtt_broker}")
        else:
            logger.error(f"MQTT connection failed: {reason_code}")
    
    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        """Handle MQTT disconnection."""
        self._connected = False
        self._discovered_batteries.clear()
        logger.warning(f"Disconnected from MQTT broker: {reason_code}")
    
    def disconnect(self):
        """Disconnect from the MQTT broker."""
        if self.client:
            self.client.loop_stop()
            self.client.disconnect()
        self._connected = False
        logger.info("Disconnected from MQTT broker")
    
    def _send_discovery(self, battery_id: str, battery_name: str):
        """Send Home Assistant MQTT discovery messages for a battery."""
        base = self.config.mqtt_base_topic
        
        device_info = {
            "identifiers": [battery_id],
            "name": battery_name,
            "manufacturer": "EG4 Electronics",
            "model": "LiFePO4 Battery",
            "sw_version": "1.0",
        }
        
        state_topic = f"{base}/sensor/{battery_id}/state"
        
        # Define sensors: (id, name, unit, device_class, state_class)
        sensors = [
            ("soc", "State of Charge", "%", "battery", "measurement"),
            ("soh", "State of Health", "%", None, "measurement"),
            ("voltage", "Pack Voltage", "V", "voltage", "measurement"),
            ("current", "Current", "A", "current", "measurement"),
            ("power", "Power", "W", "power", "measurement"),
            ("temperature", "Temperature", "°C", "temperature", "measurement"),
            ("remaining_kwh", "Remaining Energy", "kWh", "energy", "measurement"),
            ("remaining_ah", "Remaining Capacity", "Ah", None, "measurement"),
            ("time_to_low_hours", "Time To Low SOC", "h", None, "measurement"),
            ("cell_min", "Cell Min Voltage", "V", "voltage", "measurement"),
            ("cell_max", "Cell Max Voltage", "V", "voltage", "measurement"),
            ("cell_delta", "Cell Delta", "mV", None, "measurement"),
            ("alarm_count", "Alarm Count", "", None, "measurement"),
        ]
        
        # Add individual cell voltage sensors
        for i in range(1, 17):
            sensors.append((f"cell_{i}_voltage", f"Cell {i} Voltage", "V", "voltage", "measurement"))
        
        # Publish sensor discovery
        for sensor_id, name, unit, device_class, state_class in sensors:
            config_topic = f"{base}/sensor/{battery_id}_{sensor_id}/config"
            
            payload = {
                "name": f"{battery_name} {name}",
                "unique_id": f"{battery_id}_{sensor_id}",
                "state_topic": state_topic,
                "value_template": f"{{{{ value_json.{sensor_id} }}}}",
                "device": device_info,
            }
            
            if unit:
                payload["unit_of_measurement"] = unit
            if device_class:
                payload["device_class"] = device_class
            if state_class:
                payload["state_class"] = state_class
            
            self.client.publish(config_topic, json.dumps(payload), retain=True)
        
        # Binary sensors
        binary_sensors = [
            ("online", "Online", "connectivity"),
        ]
        
        for sensor_id, name, device_class in binary_sensors:
            config_topic = f"{base}/binary_sensor/{battery_id}_{sensor_id}/config"
            
            payload = {
                "name": f"{battery_name} {name}",
                "unique_id": f"{battery_id}_{sensor_id}",
                "state_topic": state_topic,
                "value_template": f"{{{{ 'ON' if value_json.{sensor_id} else 'OFF' }}}}",
                "device_class": device_class,
                "device": device_info,
            }
            
            self.client.publish(config_topic, json.dumps(payload), retain=True)
        
        self._discovered_batteries.add(battery_id)
        logger.info(f"MQTT discovery messages sent for {battery_name}")
    
    def _send_aggregate_discovery(self):
        """Send Home Assistant MQTT discovery messages for the aggregate battery bank."""
        base = self.config.mqtt_base_topic

        device_info = {
            "identifiers": [AGGREGATE_ID],
            "name": AGGREGATE_NAME,
            "manufacturer": "EG4 Electronics",
            "model": "Virtual Battery Bank",
            "sw_version": "1.0",
        }

        state_topic = f"{base}/sensor/{AGGREGATE_ID}/state"

        sensors = [
            ("soc",            "State of Charge",   "%",   "battery",     "measurement"),
            ("soh",            "State of Health",    "%",   None,          "measurement"),
            ("voltage",        "Average Voltage",    "V",   "voltage",     "measurement"),
            ("current",        "Total Current",      "A",   "current",     "measurement"),
            ("power",          "Total Power",        "W",   "power",       "measurement"),
            ("temperature",    "Max Temperature",    "°C", "temperature", "measurement"),
            ("remaining_kwh",  "Total Remaining Energy",   "kWh", "energy", "measurement"),
            ("remaining_ah",   "Total Remaining Capacity", "Ah",  None,     "measurement"),
            ("time_to_low_hours", "Time To Low SOC",        "h",   None,     "measurement"),
            ("full_capacity",  "Total Full Capacity",      "Ah",  None,     "measurement"),
            ("design_capacity","Total Design Capacity",    "Ah",  None,     "measurement"),
            ("cell_min",       "Cell Min Voltage",   "V",   "voltage",     "measurement"),
            ("cell_max",       "Cell Max Voltage",   "V",   "voltage",     "measurement"),
            ("cell_delta",     "Max Cell Delta",     "mV",  None,          "measurement"),
            ("alarm_count",    "Total Alarm Count",  "",    None,          "measurement"),
            ("online_count",   "Batteries Online",   "",    None,          "measurement"),
            ("battery_count",  "Total Batteries",    "",    None,          "measurement"),
        ]

        for sensor_id, name, unit, device_class, state_class in sensors:
            config_topic = f"{base}/sensor/{AGGREGATE_ID}_{sensor_id}/config"
            payload = {
                "name": f"{AGGREGATE_NAME} {name}",
                "unique_id": f"{AGGREGATE_ID}_{sensor_id}",
                "state_topic": state_topic,
                "value_template": f"{{{{ value_json.{sensor_id} }}}}",
                "device": device_info,
            }
            if unit:
                payload["unit_of_measurement"] = unit
            if device_class:
                payload["device_class"] = device_class
            if state_class:
                payload["state_class"] = state_class
            self.client.publish(config_topic, json.dumps(payload), retain=True)

        self._discovered_batteries.add(AGGREGATE_ID)
        logger.info(f"MQTT discovery messages sent for {AGGREGATE_NAME}")

    def publish(self, data: BatteryData):
        """Publish battery data to MQTT."""
        if not self._connected:
            if not self.connect():
                return
        
        battery_id = data.battery_id
        battery_name = data.name
        
        if battery_id not in self._discovered_batteries:
            self._send_discovery(battery_id, battery_name)
        
        base = self.config.mqtt_base_topic
        
        # Publish state with all cell voltages
        state_topic = f"{base}/sensor/{battery_id}/state"
        state_payload = {
            "soc": data.soc,
            "soh": data.soh,
            "voltage": round(data.voltage, 2),
            "current": round(data.current, 2),
            "power": round(data.power, 1),
            "temperature": round(data.temperature, 1),
            "remaining_kwh": round(data.remaining_kwh, 2),
            "remaining_ah": round(data.remaining_ah, 1),
            "time_to_low_hours": round(data.time_to_low_hours, 2) if data.time_to_low_hours is not None else None,
            "cell_min": round(data.cell_min, 3),
            "cell_max": round(data.cell_max, 3),
            "cell_delta": round(data.cell_delta, 1),
            "alarm_count": data.alarm_count,
            "online": data.online,
            "timestamp": data.timestamp,
        }
        
        # Add individual cell voltages to state payload
        for i, voltage in enumerate(data.cell_voltages, 1):
            state_payload[f"cell_{i}_voltage"] = round(voltage, 3)
        
        self.client.publish(state_topic, json.dumps(state_payload), retain=True)
        
        # Publish attributes
        attr_topic = f"{base}/sensor/{battery_id}/attributes"
        attr_payload = {
            "alarms": data.alarms,
            "cell_voltages": [round(v, 3) for v in data.cell_voltages],
            "status_raw": data.status,
            "design_capacity": data.design_capacity,
            "max_voltage": data.max_voltage,
            "max_current": data.max_current,
        }
        
        self.client.publish(attr_topic, json.dumps(attr_payload), retain=True)
        
        logger.debug(f"Published data for {battery_name} to MQTT")

    def publish_aggregate(self, batteries: List[BatteryData]):
        """Compute and publish aggregated data for all batteries to its own MQTT topic."""
        if not self._connected:
            if not self.connect():
                return

        if AGGREGATE_ID not in self._discovered_batteries:
            self._send_aggregate_discovery()

        agg = aggregate(batteries, self.config.low_soc_cutoff)
        if not agg:
            logger.debug("No online batteries — skipping aggregate publish")
            return

        base = self.config.mqtt_base_topic
        state_topic = f"{base}/sensor/{AGGREGATE_ID}/state"

        state_payload = {
            "soc":             round(agg["soc"], 1),
            "soh":             round(agg["soh"], 1),
            "voltage":         round(agg["voltage"], 2),
            "current":         round(agg["current"], 2),
            "power":           round(agg["power"], 1),
            "temperature":     round(agg["temperature"], 1),
            "remaining_kwh":   round(agg["remaining_kwh"], 2),
            "remaining_ah":    round(agg["remaining_ah"], 1),
            "time_to_low_hours": round(agg["time_to_low_hours"], 2) if agg["time_to_low_hours"] is not None else None,
            "full_capacity":   round(agg["full_capacity"], 1),
            "design_capacity": round(agg["design_capacity"], 1),
            "cell_min":        round(agg["cell_min"], 3),
            "cell_max":        round(agg["cell_max"], 3),
            "cell_delta":      round(agg["cell_delta"], 1),
            "alarm_count":     agg["alarm_count"],
            "online_count":    agg["online_count"],
            "battery_count":   agg["battery_count"],
            "alarms":          agg["alarms"],
        }

        self.client.publish(state_topic, json.dumps(state_payload), retain=True)
        logger.debug(f"Published aggregate data to {state_topic}")
