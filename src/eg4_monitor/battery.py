"""
BMS Battery data models and Modbus communication.
Supports: EG4, ECO-WORTHY/PACE, JK BMS PB series
"""

import logging
import socket
import struct
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List

from pymodbus.client import ModbusTcpClient

logger = logging.getLogger(__name__)


# =============================================================================
# EG4 WALLMOUNT 280Ah REGISTER MAP
# =============================================================================

# Format: register -> (name, divisor, unit, description)
EG4_REGISTER_MAP = {
    19: ('soc', 1, '%', 'State of Charge'),
    21: ('soh', 1, '%', 'State of Health'),
    22: ('pack_voltage', 100, 'V', 'Pack Voltage'),
    23: ('remaining_kwh', 100, 'kWh', 'Remaining Energy'),
    24: ('current', 100, 'A', 'Current'),
    25: ('full_energy_kwh', 1000, 'kWh', 'Full Energy Capacity'),
    26: ('remaining_ah', 100, 'Ah', 'Remaining Capacity'),
    27: ('design_capacity', 100, 'Ah', 'Design Capacity'),
    28: ('unknown_28', 1, '', 'Unknown'),
    30: ('temperature', 10, '°C', 'Battery Temperature'),
    32: ('soh_2', 1, '%', 'State of Health (alt)'),
    33: ('max_charge_voltage', 100, 'V', 'Max Charge Voltage'),
    35: ('max_current', 100, 'A', 'Max Current Rating'),
    37: ('cell_max_voltage', 1000, 'V', 'Highest Cell Voltage'),
    38: ('cell_min_voltage', 1000, 'V', 'Lowest Cell Voltage'),
    39: ('cycle_count', 1, '', 'Charge Cycles'),
    40: ('status_flags', 1, '', 'Status Flags'),
    41: ('cell_count', 1, '', 'Number of Cells'),
}

# EG4 Cell voltage registers
EG4_CELL_VOLTAGE_START = 113
EG4_CELL_VOLTAGE_COUNT = 16

# =============================================================================
# ECO-WORTHY 314Ah REGISTER MAP (PACE BMS)
# =============================================================================

ECOWORTHY_REGISTER_MAP = {
    0: ('current', 100, 'A', 'Current (signed)'),
    1: ('pack_voltage', 100, 'V', 'Pack Voltage'),
    2: ('soc', 1, '%', 'State of Charge'),
    3: ('soh', 1, '%', 'State of Health'),
    4: ('remaining_ah', 100, 'Ah', 'Remaining Capacity'),
    5: ('design_capacity', 100, 'Ah', 'Design Capacity'),
    6: ('full_capacity', 100, 'Ah', 'Full Capacity'),
    7: ('cycle_count', 1, '', 'Charge Cycles'),
}

# ECO-WORTHY Cell voltage registers (15-30 for 16 cells)
ECOWORTHY_CELL_VOLTAGE_START = 15
ECOWORTHY_CELL_VOLTAGE_COUNT = 16
# Temperature registers (31-36)
ECOWORTHY_TEMP_START = 31

# =============================================================================
# ECO-WORTHY / PACE BMS REGISTER MAP
# =============================================================================

ECOWORTHY_REGISTER_MAP = {
    "voltage": 1,           # ÷100
    "soc": 2,               # direct
    "soh": 3,               # direct
    "design_capacity": 4,   # ÷100 (in Ah)
    "cell_voltage_start": 15,
    "cell_voltage_count": 16,
    "temperature": 31,      # ÷10 (first temp sensor)
    "max_voltage": 107,     # ÷100 (from extended registers)
}

# =============================================================================
# JK BMS PB SERIES (INVERTER BMS) REGISTER MAP
# =============================================================================
# Base address is 0x1000, uses Modbus RTU
# Protocol: "001-JK BMS RS485 Modbus V1.0"
# Note: JK Modbus returns packed data (no gaps between values)

JK_REGISTER_MAP = {
    # Cell voltages: 0x1200-0x121F (16 cells, UINT16, mV)
    "cell_voltage_start": 0x1200,
    "cell_voltage_count": 16,
    
    # Pack data
    "avg_cell_voltage": 0x1280,    # UINT16, mV
    "delta_cell_voltage": 0x1282,  # UINT16, mV
    "max_cell_voltage": 0x1284,    # UINT16, mV
    "min_cell_voltage": 0x1286,    # UINT16, mV
    "max_voltage_cell": 0x128A,    # UINT16, cell number
    "min_voltage_cell": 0x128C,    # UINT16, cell number
    
    "pack_voltage": 0x1290,        # UINT32, mV (2 registers)
    "pack_power": 0x1294,          # INT32, W (2 registers)
    "pack_current": 0x1298,        # INT32, mA (2 registers)
    
    "alarm_bitmask": 0x12A0,       # UINT32 (2 registers)
    "balance_current": 0x12A4,     # INT16, mA
    "balance_soc": 0x12A6,         # High byte: balance status, Low byte: SOC%
    
    "remaining_capacity": 0x12AA,  # UINT32, mAh (2 registers)
    "nominal_capacity": 0x12AE,    # UINT32, mAh (2 registers)
    "cycle_count": 0x12B2,         # UINT32 (2 registers)
    "cycle_capacity": 0x12B6,      # UINT32, mAh (2 registers)
    
    "runtime": 0x12BA,             # UINT32, seconds (2 registers)
    "charging_status": 0x12BE,     # UINT16
    
    # Temperatures (0x12F2-0x12FA, INT16, 0.1°C)
    "temp_mos": 0x12F2,            # MOS temperature
    "temp_start": 0x12F4,          # Battery temp sensors start
    "temp_count": 4,               # Number of temp sensors
}

# Alarm thresholds
ALARM_THRESHOLDS = {
    "pack_overvoltage": 57.6,
    "pack_undervoltage": 44.8,
    "cell_overvoltage": 3.65,
    "cell_undervoltage": 2.5,
    "cell_imbalance_mv": 50,
    "high_temp": 55,
    "low_temp": -20,
    "critical_soc": 10,
    "overcurrent": 200,
}


# =============================================================================
# DATA MODEL
# =============================================================================

@dataclass
class BatteryData:
    """Battery telemetry data."""
    
    # Identity
    name: str = ""
    battery_id: str = ""
    
    timestamp: str = ""
    online: bool = False
    
    # State
    soc: float = 0.0
    soh: float = 0.0
    status: int = 0
    
    # Electrical
    voltage: float = 0.0
    current: float = 0.0
    power: float = 0.0
    temperature: float = 0.0
    
    # Capacity
    design_capacity: float = 0.0
    full_capacity: float = 0.0
    remaining_ah: float = 0.0
    remaining_kwh: float = 0.0

    # Runtime estimate: hours left at the current draw before hitting the
    # configured low-SOC cutoff. None while charging/idle or capacity unknown.
    time_to_low_hours: Optional[float] = None
    
    # Limits
    max_voltage: float = 0.0
    max_current: float = 0.0
    
    # Cell data
    cell_count: int = 0
    cell_min: float = 0.0
    cell_max: float = 0.0
    cell_delta: float = 0.0
    cell_voltages: List[float] = field(default_factory=list)
    
    # Alarms
    alarm_count: int = 0
    alarms: List[str] = field(default_factory=list)
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "name": self.name,
            "battery_id": self.battery_id,
            "timestamp": self.timestamp,
            "online": self.online,
            "soc": self.soc,
            "soh": self.soh,
            "status": self.status,
            "voltage": round(self.voltage, 2),
            "current": round(self.current, 2),
            "power": round(self.power, 1),
            "temperature": round(self.temperature, 1),
            "design_capacity": self.design_capacity,
            "full_capacity": self.full_capacity,
            "remaining_ah": round(self.remaining_ah, 1),
            "remaining_kwh": round(self.remaining_kwh, 2),
            "time_to_low_hours": round(self.time_to_low_hours, 2) if self.time_to_low_hours is not None else None,
            "time_to_low": format_duration(self.time_to_low_hours),
            "max_voltage": self.max_voltage,
            "max_current": self.max_current,
            "cell_count": self.cell_count,
            "cell_min": round(self.cell_min, 3),
            "cell_max": round(self.cell_max, 3),
            "cell_delta": round(self.cell_delta, 1),
            "cell_voltages": [round(v, 3) for v in self.cell_voltages],
            "alarm_count": self.alarm_count,
            "alarms": self.alarms,
        }


# =============================================================================
# MODBUS READER
# =============================================================================

def slugify(name: str) -> str:
    """Convert a name to a slug for IDs."""
    import re
    slug = name.lower()
    slug = re.sub(r'[^a-z0-9]+', '_', slug)
    slug = slug.strip('_')
    return slug


def estimate_hours_to_target(
    remaining_ah: float,
    full_capacity: float,
    current: float,
    target_soc: float = 0.0,
) -> Optional[float]:
    """Estimate hours until capacity decays to target_soc at the current draw.

    Current follows this project's convention: positive = charging,
    negative = discharging. Returns None while charging/idle or when
    capacity is unknown (nothing to count down).
    """
    if current >= 0 or full_capacity <= 0:
        return None

    target_ah = full_capacity * (target_soc / 100.0)
    usable_ah = remaining_ah - target_ah
    if usable_ah <= 0:
        return 0.0

    return usable_ah / abs(current)


def format_duration(hours: Optional[float]) -> str:
    """Format an hours value as 'Xh YYm', or '--' if unavailable."""
    if hours is None:
        return "--"

    total_minutes = round(hours * 60)
    h, m = divmod(total_minutes, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


class EG4ModbusReader:
    """Reads data from EG4/ECO-WORTHY battery via Modbus TCP."""
    
    def __init__(self, battery_config):
        """Initialize with a BatteryConfig object."""
        self.config = battery_config
        self.name = battery_config.name
        self.battery_id = slugify(battery_config.name)
        self.protocol = battery_config.protocol
        self.client: Optional[ModbusTcpClient] = None
        self._connected = False
    
    @property
    def connected(self) -> bool:
        return self._connected and self.client and self.client.is_socket_open()
    
    def connect(self) -> bool:
        """Connect to the battery via Modbus TCP."""
        try:
            self.client = ModbusTcpClient(
                host=self.config.ip,
                port=self.config.port,
                timeout=10,
            )
            self._connected = self.client.connect()
            
            if self._connected:
                logger.info(f"Connected to {self.name} at {self.config.ip}:{self.config.port}")
            else:
                logger.warning(f"Failed to connect to {self.name} at {self.config.ip}")
            
            return self._connected
            
        except Exception as e:
            logger.error(f"Modbus connection error for {self.name}: {e}")
            self._connected = False
            return False
    
    def disconnect(self):
        """Close the Modbus connection."""
        if self.client:
            self.client.close()
        self._connected = False
        logger.info(f"Disconnected from {self.name}")
    
    def _read_registers(self, address: int, count: int) -> Optional[List[int]]:
        """Read holding registers from the battery."""
        try:
            result = self.client.read_holding_registers(
                address=address,
                count=count,
                device_id=self.config.device_id,
            )
            
            if result.isError():
                logger.warning(f"Modbus read error at 0x{address:04X} for {self.name}: {result}")
                return None
            
            return result.registers
            
        except Exception as e:
            logger.error(f"Modbus read exception for {self.name}: {e}")
            return None
    
    @staticmethod
    def _signed16(value: int) -> int:
        """Convert unsigned 16-bit to signed."""
        return value - 65536 if value > 32767 else value
    
    @staticmethod
    def _crc16_modbus(data: bytes) -> int:
        """Calculate Modbus CRC16."""
        crc = 0xFFFF
        for byte in data:
            crc ^= byte
            for _ in range(8):
                if crc & 0x0001:
                    crc = (crc >> 1) ^ 0xA001
                else:
                    crc >>= 1
        return crc
    
    def _read_registers_raw(self, address: int, count: int) -> Optional[List[int]]:
        """Read holding registers using raw Modbus RTU over TCP (for JK BMS)."""
        try:
            # Build Modbus RTU frame
            frame = bytes([self.config.device_id, 0x03])  # Device ID + Function 03
            frame += struct.pack('>HH', address, count)   # Start address + count (big-endian)
            crc = self._crc16_modbus(frame)
            frame += struct.pack('<H', crc)               # CRC (little-endian)
            
            # Create socket and send
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            sock.connect((self.config.ip, self.config.port))
            sock.send(frame)
            
            # Receive response
            import time
            time.sleep(0.3)
            response = sock.recv(1024)
            sock.close()
            
            if not response or len(response) < 5:
                logger.warning(f"JK BMS: No/short response for 0x{address:04X}")
                return None
            
            # Check for error response
            if response[1] & 0x80:
                logger.warning(f"JK BMS: Modbus error 0x{response[2]:02X} at 0x{address:04X}")
                return None
            
            # Parse response
            byte_count = response[2]
            registers = []
            for i in range(3, 3 + byte_count, 2):
                if i + 1 < len(response):
                    reg_value = (response[i] << 8) | response[i + 1]
                    registers.append(reg_value)
            
            return registers
            
        except socket.timeout:
            logger.warning(f"JK BMS: Timeout reading 0x{address:04X}")
            return None
        except Exception as e:
            logger.error(f"JK BMS: Raw read error at 0x{address:04X}: {e}")
            return None
    
    def poll(self) -> BatteryData:
        """Poll the battery and return current data."""
        data = BatteryData(
            name=self.name,
            battery_id=self.battery_id,
            timestamp=datetime.now().isoformat(),
        )
        
        # Ensure connection
        if not self.connected:
            if not self.connect():
                return data
        
        # Use appropriate protocol
        if self.protocol == "ecoworthy":
            return self._poll_ecoworthy(data)
        elif self.protocol == "jkbms":
            return self._poll_jkbms(data)
        elif self.protocol == "canip":
            # CAN over IP is handled by a dedicated reader; this path should
            # not normally be reached because the monitor instantiates a
            # CANIPReader directly.  Fall back gracefully just in case.
            logger.warning(
                f"{self.name}: protocol='canip' but using Modbus reader; "
                "instantiate CANIPReader instead."
            )
            return data
        else:
            return self._poll_eg4(data)
    
    def _poll_eg4(self, data: BatteryData) -> BatteryData:
        """Poll using EG4 register map."""
        # Read main registers (0-60)
        regs = self._read_registers(0, 60)
        if not regs:
            self._connected = False
            return data
        
        data.online = True
        
        # Parse registers using corrected EG4 format
        # Reg 21: SOC, Reg 32: SOH
        data.soc = float(regs[21])
        data.soh = float(regs[32])
        data.voltage = regs[22] / 100.0
        
        # Current at reg 23 (signed, ÷100) - NOT reg 24
        data.current = self._signed16(regs[23]) / 100.0
        data.power = data.voltage * data.current
        
        # Energy and capacity
        data.remaining_ah = regs[26] / 100.0    # Reg 26 ÷ 100
        data.design_capacity = regs[27] / 100.0
        data.full_capacity = regs[27] / 100.0
        
        # Calculate remaining_kwh from Ah and voltage (more reliable than reg 25)
        data.remaining_kwh = (data.remaining_ah * data.voltage) / 1000.0
        
        data.temperature = regs[30] / 10.0
        data.max_voltage = regs[33] / 100.0
        data.max_current = regs[35] / 100.0
        data.cell_max = regs[37] / 1000.0
        data.cell_min = regs[38] / 1000.0
        data.status = regs[40]
        data.cell_count = regs[41]
        data.cell_delta = (data.cell_max - data.cell_min) * 1000
        
        # Read cell voltages (registers 113-128)
        cell_regs = self._read_registers(EG4_CELL_VOLTAGE_START, EG4_CELL_VOLTAGE_COUNT)
        if cell_regs:
            data.cell_voltages = [v / 1000.0 for v in cell_regs]
        
        # Check for alarms
        data.alarms = self._check_alarms(data)
        data.alarm_count = len(data.alarms)
        
        logger.debug(f"Polled {self.name}: SOC={data.soc}%, V={data.voltage}V, I={data.current}A")
        
        return data
    
    def _poll_ecoworthy(self, data: BatteryData) -> BatteryData:
        """Poll using ECO-WORTHY/PACE BMS register map."""
        # Read main registers (0-40)
        regs = self._read_registers(0, 40)
        if not regs:
            self._connected = False
            return data
        
        data.online = True
        
        # Parse ECO-WORTHY registers
        data.current = self._signed16(regs[0]) / 100.0
        data.voltage = regs[1] / 100.0
        data.soc = float(regs[2])
        data.soh = float(regs[3])
        data.remaining_ah = regs[4] / 100.0
        data.design_capacity = regs[5] / 100.0
        data.full_capacity = regs[6] / 100.0
        data.power = data.voltage * data.current
        data.remaining_kwh = (data.remaining_ah * data.voltage) / 1000.0
        
        # Cell voltages are in registers 15-30
        data.cell_count = 16
        data.cell_voltages = []
        for i in range(ECOWORTHY_CELL_VOLTAGE_START, ECOWORTHY_CELL_VOLTAGE_START + ECOWORTHY_CELL_VOLTAGE_COUNT):
            voltage = regs[i] / 1000.0
            # Filter out invalid readings (65535 = 0xFFFF)
            if regs[i] != 65535 and 2.0 <= voltage <= 4.0:
                data.cell_voltages.append(voltage)
        
        if data.cell_voltages:
            data.cell_min = min(data.cell_voltages)
            data.cell_max = max(data.cell_voltages)
            data.cell_delta = (data.cell_max - data.cell_min) * 1000
            data.cell_count = len(data.cell_voltages)
        
        # Temperature from register 31 (first temp sensor)
        if regs[31] != 65535 and regs[31] < 1000:
            data.temperature = regs[31] / 10.0
        
        # Check for alarms
        data.alarms = self._check_alarms(data)
        data.alarm_count = len(data.alarms)
        
        logger.debug(f"Polled {self.name}: SOC={data.soc}%, V={data.voltage}V, I={data.current}A")
        
        return data
    
    def _poll_jkbms(self, data: BatteryData) -> BatteryData:
        """Poll using JK BMS PB series (Inverter BMS) Modbus protocol.
        
        Note: JK BMS with Waveshare in TCP Server mode requires raw Modbus RTU
        frames (not Modbus TCP), so we use _read_registers_raw() instead of
        the pymodbus client.
        """
        logger.debug(f"JK BMS: Starting poll for {self.name}")
        
        # Read cell voltages (0x1200, 16 registers)
        logger.debug(f"JK BMS: Reading cell voltages at 0x1200")
        cell_regs = self._read_registers_raw(JK_REGISTER_MAP["cell_voltage_start"], 16)
        if not cell_regs:
            logger.warning(f"JK BMS: Failed to read cell voltages")
            self._connected = False
            return data
        
        data.online = True
        
        # Parse cell voltages (mV)
        data.cell_voltages = []
        for mv in cell_regs:
            if mv > 0 and mv < 5000:  # Valid range
                data.cell_voltages.append(mv / 1000.0)
        
        if data.cell_voltages:
            data.cell_min = min(data.cell_voltages)
            data.cell_max = max(data.cell_voltages)
            data.cell_delta = (data.cell_max - data.cell_min) * 1000
            data.cell_count = len(data.cell_voltages)
        
        # Read SOC (0x12A6) - SOC is in low byte
        logger.debug(f"JK BMS: Reading SOC at 0x12A6")
        soc_regs = self._read_registers_raw(JK_REGISTER_MAP["balance_soc"], 1)
        if soc_regs:
            data.soc = float(soc_regs[0] & 0xFF)
        
        # Read pack voltage, power, current (0x1290, 6 registers = 3 x UINT32/INT32)
        logger.debug(f"JK BMS: Reading pack data at 0x1290")
        pack_regs = self._read_registers_raw(JK_REGISTER_MAP["pack_voltage"], 6)
        if pack_regs and len(pack_regs) >= 6:
            # Pack Voltage: UINT32 in mV (registers 0-1, but JK packs them)
            # JK returns data without gaps, so read as sequential 16-bit values
            pack_v_raw = (pack_regs[0] << 16) | pack_regs[1]
            data.voltage = pack_v_raw / 1000.0
            
            # Pack Current: INT32 in mA (registers 4-5)
            pack_i_raw = (pack_regs[4] << 16) | pack_regs[5]
            if pack_i_raw > 0x7FFFFFFF:
                pack_i_raw -= 0x100000000
            data.current = pack_i_raw / 1000.0
            
            # Calculate power from V * I (more reliable than power register)
            data.power = data.voltage * data.current
        
        # Read capacity (0x12AA, 4 registers - skip cycles)
        logger.debug(f"JK BMS: Reading capacity at 0x12AA")
        cap_regs = self._read_registers_raw(JK_REGISTER_MAP["remaining_capacity"], 4)
        if cap_regs and len(cap_regs) >= 4:
            # Remaining capacity: UINT32 in mAh (swap byte order for JK)
            remaining_raw = (cap_regs[1] << 16) | cap_regs[0]
            data.remaining_ah = remaining_raw / 1000.0
            
            # Nominal capacity: UINT32 in mAh
            nominal_raw = (cap_regs[3] << 16) | cap_regs[2]
            data.design_capacity = nominal_raw / 1000.0
            data.full_capacity = data.design_capacity
        
        # Calculate remaining kWh
        if data.remaining_ah and data.voltage:
            data.remaining_kwh = (data.remaining_ah * data.voltage) / 1000.0
        
        # Read temperatures (0x12F2, 5 registers)
        logger.debug(f"JK BMS: Reading temperatures at 0x12F2")
        temp_regs = self._read_registers_raw(JK_REGISTER_MAP["temp_mos"], 5)
        if temp_regs:
            # Find first valid temperature (skip MOS temp if it looks wrong)
            for i, val in enumerate(temp_regs):
                if val != 0 and val != 0xFFFF:
                    temp_signed = self._signed16(val)
                    temp_c = temp_signed / 10.0
                    # Use battery temp (not MOS) if reasonable
                    if -40 <= temp_c <= 80:
                        data.temperature = temp_c
                        break
        
        # Default SOH to 100 if not available
        if data.soh == 0:
            data.soh = 100.0
        
        # Check for alarms
        data.alarms = self._check_alarms(data)
        data.alarm_count = len(data.alarms)
        
        logger.debug(f"Polled {self.name}: SOC={data.soc}%, V={data.voltage}V, I={data.current}A")
        
        return data
    
    def _check_alarms(self, data: BatteryData) -> List[str]:
        """Check for alarm conditions."""
        alarms = []
        thresholds = ALARM_THRESHOLDS
        
        # Voltage alarms
        if data.voltage > thresholds["pack_overvoltage"]:
            alarms.append("Pack Over-Voltage")
        if data.voltage < thresholds["pack_undervoltage"]:
            alarms.append("Pack Under-Voltage")
        
        # Cell voltage alarms
        if data.cell_max > thresholds["cell_overvoltage"]:
            alarms.append("Cell Over-Voltage")
        if data.cell_min < thresholds["cell_undervoltage"]:
            alarms.append("Cell Under-Voltage")
        
        # Cell imbalance
        if data.cell_delta > thresholds["cell_imbalance_mv"]:
            alarms.append("Cell Imbalance")
        
        # Temperature alarms
        if data.temperature > thresholds["high_temp"]:
            alarms.append("High Temperature")
        if data.temperature < thresholds["low_temp"]:
            alarms.append("Low Temperature")
        
        # SOC alarms
        if data.soc < thresholds["critical_soc"]:
            alarms.append("Critical Low SOC")
        
        # Current alarms
        if abs(data.current) > thresholds["overcurrent"]:
            alarms.append("Over Current")
        
        return alarms
