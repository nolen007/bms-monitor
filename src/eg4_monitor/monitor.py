"""
Main battery monitor coordinator.
"""

import logging
import time
from typing import Optional, List, Dict

from .config import Config
from .battery import BatteryData, EG4ModbusReader, estimate_hours_to_target
from .canip import CANIPReader
from .modbus_server import VirtualModbusServer, aggregate
from .mqtt import MQTTPublisher
from .ui import TerminalUI, HeadlessUI
from .web import WebServer

logger = logging.getLogger(__name__)


class BatteryMonitor:
    """Main application coordinator for multiple batteries."""
    
    def __init__(self, config: Config):
        self.config = config
        
        # Create readers for each battery
        self.readers = []
        for batt_config in config.batteries:
            if batt_config.protocol == "canip":
                self.readers.append(CANIPReader(batt_config))
            else:
                self.readers.append(EG4ModbusReader(batt_config))
        
        self.mqtt = MQTTPublisher(config)
        self.ui = TerminalUI() if config.ui_enabled else HeadlessUI()
        self.web: Optional[WebServer] = None
        self.modbus_server: Optional[VirtualModbusServer] = None
        self.running = False
        
        # Store data for all batteries
        self.battery_data: Dict[str, BatteryData] = {}
        
        # Initialize web server if enabled
        if config.web_enabled:
            self.web = WebServer(
                host=config.web_host,
                port=config.web_port,
                low_soc_cutoff=config.low_soc_cutoff,
            )
        
        # Initialize virtual Modbus server if enabled
        if config.modbus_server_enabled:
            self.modbus_server = VirtualModbusServer(
                host=config.modbus_server_host,
                port=config.modbus_server_port,
            )
    
    def start(self):
        """Start the battery monitor."""
        self.running = True
        
        logger.info("Starting BMS Battery Monitor")
        logger.info(f"Monitoring {len(self.readers)} battery(ies):")
        for reader in self.readers:
            logger.info(f"  - {reader.name} at {reader.config.ip}:{reader.config.port}")
        logger.info(f"MQTT: {self.config.mqtt_broker}:{self.config.mqtt_port}")
        logger.info(f"Poll interval: {self.config.poll_interval}s")
        
        # Start web server if enabled
        if self.web:
            self.web.start()
            logger.info(f"Web GUI: http://{self.config.web_host}:{self.config.web_port}")
        
        # Start virtual Modbus server if enabled
        if self.modbus_server:
            self.modbus_server.start()
            logger.info(
                f"Virtual Modbus server: {self.config.modbus_server_host}:{self.config.modbus_server_port} "
                f"(Solar Assistant → Pylontech / TCP / Device ID 1)"
            )
        
        # Initial connections
        for reader in self.readers:
            if not reader.connect():
                logger.warning(f"Could not connect to {reader.name}. Will retry...")
        
        if not self.mqtt.connect():
            logger.warning("Could not connect to MQTT. Will retry...")
        
        # Give connections time to establish
        time.sleep(1)
        
        # Main loop
        try:
            while self.running:
                self._poll_cycle()
                time.sleep(self.config.poll_interval)
                
        except KeyboardInterrupt:
            logger.info("Interrupted by user")
        finally:
            self.stop()
    
    def _poll_cycle(self):
        """Execute one poll cycle for all batteries."""
        all_data = []
        
        for reader in self.readers:
            # Poll battery
            data = reader.poll()
            data.time_to_low_hours = estimate_hours_to_target(
                data.remaining_ah, data.full_capacity, data.current, self.config.low_soc_cutoff
            )
            self.battery_data[data.battery_id] = data
            all_data.append(data)

            # Publish individual battery data to MQTT
            self.mqtt.publish(data)

        # Publish aggregated data to its own MQTT topic
        self.mqtt.publish_aggregate(all_data)

        pack_time_to_low_hours = aggregate(all_data, self.config.low_soc_cutoff).get("time_to_low_hours")

        # Update web server data
        if self.web:
            self.web.update_data(all_data, self.mqtt.connected, pack_time_to_low_hours)

        # Update virtual Modbus server registers
        if self.modbus_server:
            self.modbus_server.update(all_data)

        # Update terminal UI
        if self.config.ui_enabled:
            self.ui.render(all_data, self.mqtt.connected, pack_time_to_low_hours)
    
    def stop(self):
        """Stop the battery monitor."""
        self.running = False
        for reader in self.readers:
            reader.disconnect()
        self.mqtt.disconnect()
        if self.web:
            self.web.stop()
        if self.modbus_server:
            self.modbus_server.stop()
        logger.info("Battery monitor stopped")
    
    def poll_once(self) -> List[BatteryData]:
        """Poll all batteries once and return data (for external use)."""
        results = []
        for reader in self.readers:
            if not reader.connected:
                reader.connect()
            results.append(reader.poll())
        return results
