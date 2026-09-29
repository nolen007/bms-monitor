"""
Tests for battery data helpers: runtime estimation and duration formatting.
"""

import pytest
from eg4_monitor.battery import estimate_hours_to_target, format_duration, BatteryData


class TestEstimateHoursToTarget:

    def test_discharging_to_empty(self):
        # 100Ah remaining, 10A discharge, target 0% of 200Ah capacity -> 10h
        hours = estimate_hours_to_target(remaining_ah=100.0, full_capacity=200.0, current=-10.0)
        assert hours == pytest.approx(10.0)

    def test_discharging_to_nonzero_target(self):
        # 100Ah remaining, 10A discharge, target 15% of 200Ah = 30Ah -> (100-30)/10 = 7h
        hours = estimate_hours_to_target(remaining_ah=100.0, full_capacity=200.0, current=-10.0, target_soc=15.0)
        assert hours == pytest.approx(7.0)

    def test_charging_returns_none(self):
        assert estimate_hours_to_target(remaining_ah=100.0, full_capacity=200.0, current=10.0) is None

    def test_idle_returns_none(self):
        assert estimate_hours_to_target(remaining_ah=100.0, full_capacity=200.0, current=0.0) is None

    def test_unknown_capacity_returns_none(self):
        assert estimate_hours_to_target(remaining_ah=100.0, full_capacity=0.0, current=-10.0) is None

    def test_already_below_target_returns_zero(self):
        # 20Ah remaining, target 15% of 200Ah = 30Ah -> already below target
        hours = estimate_hours_to_target(remaining_ah=20.0, full_capacity=200.0, current=-5.0, target_soc=15.0)
        assert hours == 0.0


class TestFormatDuration:

    def test_none_is_dashes(self):
        assert format_duration(None) == "--"

    def test_zero_hours(self):
        assert format_duration(0.0) == "0m"

    def test_minutes_only(self):
        assert format_duration(0.5) == "30m"

    def test_hours_and_minutes(self):
        assert format_duration(3.75) == "3h 45m"

    def test_rounds_to_nearest_minute(self):
        assert format_duration(1.0 / 60 * 1.4) == "1m"


class TestBatteryDataTimeToLow:

    def test_to_dict_includes_time_to_low(self):
        data = BatteryData(time_to_low_hours=2.5)
        result = data.to_dict()
        assert result["time_to_low_hours"] == 2.5
        assert result["time_to_low"] == "2h 30m"

    def test_to_dict_none_when_unset(self):
        data = BatteryData()
        result = data.to_dict()
        assert result["time_to_low_hours"] is None
        assert result["time_to_low"] == "--"
