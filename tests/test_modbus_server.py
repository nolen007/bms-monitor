"""
Tests for the virtual Modbus TCP server (Solar Assistant gateway).
Covers data aggregation and register encoding — no network I/O required.
"""

import pytest
from eg4_monitor.battery import BatteryData
from eg4_monitor.modbus_server import aggregate, build_registers, _alarm_bitmask, ALARM_BIT


def _make_battery(
    name="Test",
    online=True,
    soc=80.0,
    soh=100.0,
    voltage=52.0,
    current=10.0,
    temperature=25.0,
    remaining_ah=224.0,
    design_capacity=280.0,
    full_capacity=280.0,
    remaining_kwh=11.65,
    max_voltage=57.6,
    max_current=150.0,
    cell_count=16,
    cell_min=3.240,
    cell_max=3.260,
    cell_delta=20.0,
    cell_voltages=None,
    alarms=None,
    alarm_count=0,
):
    b = BatteryData()
    b.name = name
    b.battery_id = name.lower().replace(" ", "_")
    b.online = online
    b.soc = soc
    b.soh = soh
    b.voltage = voltage
    b.current = current
    b.power = round(voltage * current, 1)
    b.temperature = temperature
    b.remaining_ah = remaining_ah
    b.design_capacity = design_capacity
    b.full_capacity = full_capacity
    b.remaining_kwh = remaining_kwh
    b.max_voltage = max_voltage
    b.max_current = max_current
    b.cell_count = cell_count
    b.cell_min = cell_min
    b.cell_max = cell_max
    b.cell_delta = cell_delta
    b.cell_voltages = cell_voltages or [3.250] * cell_count
    b.alarms = alarms or []
    b.alarm_count = alarm_count
    return b


# ---------------------------------------------------------------------------
# Aggregation tests
# ---------------------------------------------------------------------------

class TestAggregate:

    def test_single_battery(self):
        b = _make_battery(soc=75.0, voltage=52.0, current=10.0, remaining_ah=210.0)
        agg = aggregate([b])
        assert agg["soc"]          == pytest.approx(75.0)
        assert agg["voltage"]      == pytest.approx(52.0)
        assert agg["current"]      == pytest.approx(10.0)
        assert agg["remaining_ah"] == pytest.approx(210.0)
        assert agg["online_count"] == 1
        assert agg["battery_count"] == 1

    def test_offline_battery_excluded(self):
        online  = _make_battery(name="A", online=True,  soc=80.0, remaining_ah=224.0)
        offline = _make_battery(name="B", online=False, soc=40.0, remaining_ah=112.0)
        agg = aggregate([online, offline])
        assert agg["online_count"]  == 1
        assert agg["battery_count"] == 2
        assert agg["soc"]          == pytest.approx(80.0)
        assert agg["remaining_ah"] == pytest.approx(224.0)

    def test_all_offline_returns_empty(self):
        b = _make_battery(online=False)
        assert aggregate([b]) == {}

    def test_current_is_summed(self):
        b1 = _make_battery(name="A", current=10.0)
        b2 = _make_battery(name="B", current=15.0)
        agg = aggregate([b1, b2])
        assert agg["current"] == pytest.approx(25.0)

    def test_voltage_is_averaged(self):
        b1 = _make_battery(name="A", voltage=52.0)
        b2 = _make_battery(name="B", voltage=51.0)
        agg = aggregate([b1, b2])
        assert agg["voltage"] == pytest.approx(51.5)

    def test_capacity_is_summed(self):
        b1 = _make_battery(name="A", design_capacity=280.0, full_capacity=280.0, remaining_ah=224.0)
        b2 = _make_battery(name="B", design_capacity=280.0, full_capacity=280.0, remaining_ah=200.0)
        agg = aggregate([b1, b2])
        assert agg["design_capacity"] == pytest.approx(560.0)
        assert agg["remaining_ah"]    == pytest.approx(424.0)

    def test_soc_weighted_by_capacity(self):
        # b1: 280 Ah at 80% SOC; b2: 140 Ah at 40% SOC
        # weighted avg = (280*80 + 140*40) / 420 = (22400+5600)/420 = 66.67%
        b1 = _make_battery(name="A", full_capacity=280.0, soc=80.0)
        b2 = _make_battery(name="B", full_capacity=140.0, soc=40.0)
        agg = aggregate([b1, b2])
        assert agg["soc"] == pytest.approx(66.666, rel=1e-3)

    def test_soh_is_minimum(self):
        b1 = _make_battery(name="A", soh=98.0)
        b2 = _make_battery(name="B", soh=87.0)
        agg = aggregate([b1, b2])
        assert agg["soh"] == pytest.approx(87.0)

    def test_temperature_is_maximum(self):
        b1 = _make_battery(name="A", temperature=28.0)
        b2 = _make_battery(name="B", temperature=35.0)
        agg = aggregate([b1, b2])
        assert agg["temperature"] == pytest.approx(35.0)

    def test_max_voltage_is_minimum_limit(self):
        b1 = _make_battery(name="A", max_voltage=57.6)
        b2 = _make_battery(name="B", max_voltage=56.8)
        agg = aggregate([b1, b2])
        assert agg["max_voltage"] == pytest.approx(56.8)

    def test_max_current_is_summed(self):
        b1 = _make_battery(name="A", max_current=150.0)
        b2 = _make_battery(name="B", max_current=100.0)
        agg = aggregate([b1, b2])
        assert agg["max_current"] == pytest.approx(250.0)

    def test_alarms_are_union(self):
        b1 = _make_battery(name="A", alarms=["Cell Imbalance"])
        b2 = _make_battery(name="B", alarms=["High Temperature", "Cell Imbalance"])
        agg = aggregate([b1, b2])
        assert "Cell Imbalance"    in agg["alarms"]
        assert "High Temperature"  in agg["alarms"]
        assert len(agg["alarms"]) == 2   # no duplicates

    def test_time_to_low_default_target(self):
        # 200Ah remaining, 280Ah capacity, -20A total draw -> 10h to 0%
        b1 = _make_battery(name="A", full_capacity=140.0, remaining_ah=100.0, current=-10.0)
        b2 = _make_battery(name="B", full_capacity=140.0, remaining_ah=100.0, current=-10.0)
        agg = aggregate([b1, b2])
        assert agg["time_to_low_hours"] == pytest.approx(10.0)

    def test_time_to_low_with_cutoff(self):
        # 280Ah capacity total, target 15% = 42Ah; 200Ah remaining, -20A -> (200-42)/20 = 7.9h
        b1 = _make_battery(name="A", full_capacity=140.0, remaining_ah=100.0, current=-10.0)
        b2 = _make_battery(name="B", full_capacity=140.0, remaining_ah=100.0, current=-10.0)
        agg = aggregate([b1, b2], target_soc=15.0)
        assert agg["time_to_low_hours"] == pytest.approx(7.9)

    def test_time_to_low_none_while_charging(self):
        b1 = _make_battery(name="A", current=10.0)
        b2 = _make_battery(name="B", current=5.0)
        agg = aggregate([b1, b2])
        assert agg["time_to_low_hours"] is None

    def test_cell_voltages_concatenated(self):
        b1 = _make_battery(name="A", cell_count=4, cell_voltages=[3.2, 3.21, 3.19, 3.2])
        b2 = _make_battery(name="B", cell_count=4, cell_voltages=[3.22, 3.2, 3.18, 3.21])
        agg = aggregate([b1, b2])
        assert len(agg["cell_voltages"]) == 8
        assert agg["cell_voltages"][0] == pytest.approx(3.2)
        assert agg["cell_voltages"][4] == pytest.approx(3.22)


# ---------------------------------------------------------------------------
# Register encoding tests
# ---------------------------------------------------------------------------

class TestBuildRegisters:

    def test_no_batteries_returns_zeros(self):
        regs = build_registers([])
        assert all(r == 0 for r in regs)

    def test_offline_battery_returns_zeros(self):
        b = _make_battery(online=False)
        regs = build_registers([b])
        assert regs[1] == 0   # voltage register

    def test_voltage_encoded_correctly(self):
        b = _make_battery(voltage=52.0)
        regs = build_registers([b])
        # reg[1] = voltage * 100 = 5200
        assert regs[1] == 5200

    def test_soc_encoded_correctly(self):
        b = _make_battery(soc=75.0)
        regs = build_registers([b])
        assert regs[2] == 75

    def test_current_charging_positive(self):
        b = _make_battery(current=10.5)
        regs = build_registers([b])
        # reg[0] = int16(10.5 * 100) = 1050
        assert regs[0] == 1050

    def test_current_discharging_negative_int16(self):
        b = _make_battery(current=-20.0)
        regs = build_registers([b])
        # int16(-2000) encoded as uint16 = 65536 - 2000 = 63536
        assert regs[0] == 63536

    def test_temperature_negative_encoded(self):
        b = _make_battery(temperature=-5.0)
        regs = build_registers([b])
        # int16(-50) as uint16 = 65536 - 50 = 65486
        assert regs[10] == 65486

    def test_cell_voltages_in_register_block(self):
        cvs = [3.250, 3.260, 3.240, 3.255]
        b = _make_battery(cell_count=4, cell_voltages=cvs)
        regs = build_registers([b])
        assert regs[100] == 3250   # 3.250 * 1000
        assert regs[101] == 3260
        assert regs[102] == 3240
        assert regs[103] == 3255

    def test_alarm_bitmask_register(self):
        b = _make_battery(alarms=["Pack Over-Voltage", "Critical Low SOC"])
        regs = build_registers([b])
        expected = ALARM_BIT["Pack Over-Voltage"] | ALARM_BIT["Critical Low SOC"]
        assert regs[200] == expected

    def test_no_alarms_bitmask_zero(self):
        b = _make_battery(alarms=[])
        regs = build_registers([b])
        assert regs[200] == 0

    def test_online_and_battery_count(self):
        b1 = _make_battery(name="A", online=True)
        b2 = _make_battery(name="B", online=False)
        regs = build_registers([b1, b2])
        assert regs[19] == 1   # online_count
        assert regs[20] == 2   # battery_count


# ---------------------------------------------------------------------------
# Alarm bitmask helper
# ---------------------------------------------------------------------------

class TestAlarmBitmask:

    def test_no_alarms(self):
        assert _alarm_bitmask([]) == 0

    def test_known_alarm(self):
        mask = _alarm_bitmask(["Cell Over-Voltage"])
        assert mask == ALARM_BIT["Cell Over-Voltage"]

    def test_multiple_alarms(self):
        mask = _alarm_bitmask(["Cell Over-Voltage", "High Temperature"])
        assert mask & ALARM_BIT["Cell Over-Voltage"]
        assert mask & ALARM_BIT["High Temperature"]

    def test_unknown_alarm_ignored(self):
        mask = _alarm_bitmask(["Some Unknown Alarm"])
        assert mask == 0
