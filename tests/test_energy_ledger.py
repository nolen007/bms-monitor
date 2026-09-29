"""
Tests for the energy ledger: energy/cost accumulation and rollups.
"""

from datetime import datetime, timedelta, timezone

import pytest
from eg4_monitor.energy_ledger import EnergyLedger


@pytest.fixture
def ledger(tmp_path):
    db_path = tmp_path / "test_energy.db"
    led = EnergyLedger(db_path=str(db_path))
    yield led
    led.close()


class TestRecord:

    def test_basic_discharge_interval(self, ledger):
        # 1000W PV, 0W grid, -2000W battery (discharging) for 1 hour at $0.30/kWh
        ledger.record(pv_power=1000.0, grid_power=0.0, battery_power=-2000.0, rate=0.30, interval_seconds=3600)
        totals = ledger.totals_lifetime()
        assert totals.solar_kwh == pytest.approx(1.0)
        assert totals.battery_discharge_kwh == pytest.approx(2.0)
        assert totals.grid_kwh == pytest.approx(0.0)
        assert totals.solar_savings == pytest.approx(0.30)
        assert totals.battery_savings == pytest.approx(0.60)
        assert totals.grid_cost == pytest.approx(0.0)

    def test_charging_not_counted_as_savings(self, ledger):
        # Battery charging (positive power) shouldn't count as discharge savings
        ledger.record(pv_power=500.0, grid_power=0.0, battery_power=1500.0, rate=0.30, interval_seconds=3600)
        totals = ledger.totals_lifetime()
        assert totals.battery_discharge_kwh == 0.0
        assert totals.battery_savings == 0.0

    def test_grid_draw_costs_money(self, ledger):
        ledger.record(pv_power=0.0, grid_power=3000.0, battery_power=0.0, rate=0.10, interval_seconds=3600)
        totals = ledger.totals_lifetime()
        assert totals.grid_kwh == pytest.approx(3.0)
        assert totals.grid_cost == pytest.approx(0.30)

    def test_skipped_when_rate_unknown(self, ledger):
        ledger.record(pv_power=1000.0, grid_power=0.0, battery_power=-1000.0, rate=None, interval_seconds=3600)
        totals = ledger.totals_lifetime()
        assert totals.solar_kwh == 0.0
        assert totals.solar_savings == 0.0

    def test_missing_pv_or_grid_treated_as_zero(self, ledger):
        ledger.record(pv_power=None, grid_power=None, battery_power=-1000.0, rate=0.20, interval_seconds=3600)
        totals = ledger.totals_lifetime()
        assert totals.solar_kwh == 0.0
        assert totals.grid_kwh == 0.0
        assert totals.battery_discharge_kwh == pytest.approx(1.0)

    def test_multiple_intervals_accumulate(self, ledger):
        for _ in range(3):
            ledger.record(pv_power=1000.0, grid_power=0.0, battery_power=0.0, rate=0.30, interval_seconds=3600)
        totals = ledger.totals_lifetime()
        assert totals.solar_kwh == pytest.approx(3.0)
        assert totals.solar_savings == pytest.approx(0.90)


class TestTotalsSince:

    def test_excludes_rows_before_cutoff(self, ledger):
        ledger.record(pv_power=1000.0, grid_power=0.0, battery_power=0.0, rate=0.30, interval_seconds=3600)
        cutoff = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        totals = ledger.totals_since(cutoff)
        assert totals.solar_kwh == 0.0

    def test_includes_rows_after_cutoff(self, ledger):
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        ledger.record(pv_power=1000.0, grid_power=0.0, battery_power=0.0, rate=0.30, interval_seconds=3600)
        totals = ledger.totals_since(cutoff)
        assert totals.solar_kwh == pytest.approx(1.0)


class TestEnergyTotalsToDict:

    def test_total_savings_is_solar_plus_battery(self, ledger):
        ledger.record(pv_power=1000.0, grid_power=500.0, battery_power=-1000.0, rate=0.30, interval_seconds=3600)
        d = ledger.totals_lifetime().to_dict()
        assert d["total_savings"] == pytest.approx(d["solar_savings"] + d["battery_savings"])
        assert d["grid_cost"] == pytest.approx(0.15)


class TestLatest:

    def test_empty_ledger_returns_none(self, ledger):
        assert ledger.latest() is None

    def test_returns_most_recent_raw_inputs(self, ledger):
        ledger.record(pv_power=500.0, grid_power=100.0, battery_power=-200.0, rate=0.10, interval_seconds=30)
        ledger.record(pv_power=800.0, grid_power=0.0, battery_power=-900.0, rate=0.30, interval_seconds=30)
        latest = ledger.latest()
        assert latest["pv_power"] == 800.0
        assert latest["grid_power"] == 0.0
        assert latest["battery_power"] == -900.0
        assert latest["rate"] == 0.30
        assert "timestamp" in latest

    def test_skipped_interval_not_recorded_as_latest(self, ledger):
        ledger.record(pv_power=500.0, grid_power=0.0, battery_power=-200.0, rate=0.10, interval_seconds=30)
        ledger.record(pv_power=999.0, grid_power=0.0, battery_power=0.0, rate=None, interval_seconds=30)
        latest = ledger.latest()
        assert latest["pv_power"] == 500.0
