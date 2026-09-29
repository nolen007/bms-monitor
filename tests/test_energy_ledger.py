"""
Tests for the energy ledger: energy/cost accumulation and rollups.
"""

import calendar
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from eg4_monitor.energy_ledger import EnergyLedger, resolve_range, RANGE_PRESETS


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


class TestGridChargingDeduction:

    def test_charging_while_grid_charging_costs_money(self, ledger):
        # 2000W charging for 1 hour at $0.30/kWh, sourced from grid
        ledger.record(
            pv_power=0.0, grid_power=2000.0, battery_power=2000.0,
            rate=0.30, interval_seconds=3600, grid_charging=True,
        )
        totals = ledger.totals_lifetime()
        assert totals.battery_charge_kwh == pytest.approx(2.0)
        assert totals.grid_charge_cost == pytest.approx(0.60)
        assert totals.battery_savings == pytest.approx(-0.60)

    def test_charging_from_solar_surplus_costs_nothing(self, ledger):
        # Same charge power, but NOT in grid-charge mode -> no deduction
        ledger.record(
            pv_power=2000.0, grid_power=0.0, battery_power=1500.0,
            rate=0.30, interval_seconds=3600, grid_charging=False,
        )
        totals = ledger.totals_lifetime()
        assert totals.battery_charge_kwh == 0.0
        assert totals.grid_charge_cost == 0.0
        assert totals.battery_savings == 0.0
        # Solar savings already fully captures the PV production regardless
        assert totals.solar_savings == pytest.approx(0.60)

    def test_net_battery_savings_over_a_day(self, ledger):
        # Discharge 1000W for 1h (saves $0.30), then grid-charge 1000W for 1h (costs $0.30)
        ledger.record(pv_power=0.0, grid_power=0.0, battery_power=-1000.0, rate=0.30, interval_seconds=3600)
        ledger.record(pv_power=0.0, grid_power=1000.0, battery_power=1000.0, rate=0.30, interval_seconds=3600, grid_charging=True)
        totals = ledger.totals_lifetime()
        assert totals.battery_savings == pytest.approx(0.0)

    def test_discharge_unaffected_by_grid_charging_flag(self, ledger):
        # grid_charging=True shouldn't matter while actually discharging
        ledger.record(
            pv_power=0.0, grid_power=0.0, battery_power=-1000.0,
            rate=0.30, interval_seconds=3600, grid_charging=True,
        )
        totals = ledger.totals_lifetime()
        assert totals.battery_charge_kwh == 0.0
        assert totals.battery_savings == pytest.approx(0.30)


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
        ledger.record(pv_power=800.0, grid_power=0.0, battery_power=-900.0, rate=0.30, interval_seconds=30, grid_charging=True)
        latest = ledger.latest()
        assert latest["pv_power"] == 800.0
        assert latest["grid_power"] == 0.0
        assert latest["battery_power"] == -900.0
        assert latest["rate"] == 0.30
        assert latest["grid_charging"] is True
        assert "timestamp" in latest

    def test_skipped_interval_not_recorded_as_latest(self, ledger):
        ledger.record(pv_power=500.0, grid_power=0.0, battery_power=-200.0, rate=0.10, interval_seconds=30)
        ledger.record(pv_power=999.0, grid_power=0.0, battery_power=0.0, rate=None, interval_seconds=30)
        latest = ledger.latest()
        assert latest["pv_power"] == 500.0


class TestCircuits:

    def test_record_and_totals(self, ledger):
        ledger.record_circuit(name="EV Charger", power=7000.0, rate=0.30, interval_seconds=3600)
        totals = ledger.circuit_totals_lifetime("EV Charger")
        assert totals.kwh == pytest.approx(7.0)
        assert totals.cost == pytest.approx(2.10)

    def test_circuits_are_independent(self, ledger):
        ledger.record_circuit(name="EV Charger", power=7000.0, rate=0.30, interval_seconds=3600)
        ledger.record_circuit(name="AC Compressor", power=3000.0, rate=0.30, interval_seconds=3600)
        ev = ledger.circuit_totals_lifetime("EV Charger")
        ac = ledger.circuit_totals_lifetime("AC Compressor")
        assert ev.kwh == pytest.approx(7.0)
        assert ac.kwh == pytest.approx(3.0)

    def test_skipped_when_rate_unknown(self, ledger):
        ledger.record_circuit(name="EV Charger", power=7000.0, rate=None, interval_seconds=3600)
        totals = ledger.circuit_totals_lifetime("EV Charger")
        assert totals.kwh == 0.0

    def test_missing_power_treated_as_zero(self, ledger):
        ledger.record_circuit(name="EV Charger", power=None, rate=0.30, interval_seconds=3600)
        totals = ledger.circuit_totals_lifetime("EV Charger")
        assert totals.kwh == 0.0

    def test_unknown_circuit_returns_zero_totals(self, ledger):
        ledger.record_circuit(name="EV Charger", power=7000.0, rate=0.30, interval_seconds=3600)
        totals = ledger.circuit_totals_lifetime("Nonexistent")
        assert totals.kwh == 0.0
        assert totals.cost == 0.0

    def test_circuit_latest(self, ledger):
        ledger.record_circuit(name="EV Charger", power=1000.0, rate=0.30, interval_seconds=30)
        ledger.record_circuit(name="EV Charger", power=7000.0, rate=0.30, interval_seconds=30)
        latest = ledger.circuit_latest("EV Charger")
        assert latest["power"] == 7000.0

    def test_circuit_latest_none_when_empty(self, ledger):
        assert ledger.circuit_latest("EV Charger") is None


class TestMonthlyBillEstimate:

    def test_none_when_no_data(self, ledger):
        assert ledger.monthly_bill_estimate("Whole Home") is None

    def test_projects_using_days_elapsed_and_days_in_month(self, ledger):
        ledger.record_circuit(name="Whole Home", power=1000.0, rate=0.30, interval_seconds=3600)
        estimate = ledger.monthly_bill_estimate("Whole Home")

        now = datetime.now().astimezone()
        days_elapsed = max(1, now.day)
        days_in_month = calendar.monthrange(now.year, now.month)[1]
        expected = (1.0 * 0.30) / days_elapsed * days_in_month

        assert estimate == pytest.approx(expected)

    def test_different_circuits_estimated_independently(self, ledger):
        ledger.record_circuit(name="Whole Home", power=1000.0, rate=0.30, interval_seconds=3600)
        assert ledger.monthly_bill_estimate("EV Charger") is None


class TestSchemaMigration:

    def test_opens_pre_migration_database_without_error(self, tmp_path):
        db_path = str(tmp_path / "legacy.db")
        # Simulate a database created before grid_charging/battery_charge_kwh/
        # grid_charge_cost existed.
        conn = sqlite3.connect(db_path)
        conn.execute(
            """CREATE TABLE energy_log (
                timestamp TEXT NOT NULL,
                pv_power REAL,
                grid_power REAL,
                battery_power REAL,
                rate REAL,
                solar_kwh REAL NOT NULL,
                grid_kwh REAL NOT NULL,
                battery_discharge_kwh REAL NOT NULL,
                grid_cost REAL NOT NULL,
                solar_savings REAL NOT NULL,
                battery_savings REAL NOT NULL
            )"""
        )
        conn.execute(
            """INSERT INTO energy_log (
                timestamp, pv_power, grid_power, battery_power, rate,
                solar_kwh, grid_kwh, battery_discharge_kwh,
                grid_cost, solar_savings, battery_savings
            ) VALUES ('2026-01-01T00:00:00+00:00', 100, 0, -50, 0.3, 1.0, 0.0, 0.5, 0.0, 0.3, 0.15)"""
        )
        conn.commit()
        conn.close()

        ledger = EnergyLedger(db_path=db_path)
        try:
            totals = ledger.totals_lifetime()
            assert totals.solar_savings == pytest.approx(0.3)
            assert totals.battery_charge_kwh == 0.0  # migrated column defaults to 0

            # New writes after migration should work normally
            ledger.record(pv_power=100.0, grid_power=100.0, battery_power=100.0, rate=0.3, interval_seconds=3600, grid_charging=True)
            totals = ledger.totals_lifetime()
            assert totals.battery_charge_kwh == pytest.approx(0.1)
        finally:
            ledger.close()


class TestResolveRange:

    def test_returns_all_known_presets(self):
        for key, _ in RANGE_PRESETS:
            start, end = resolve_range(key)
            assert isinstance(start, str)
            assert isinstance(end, str)
            assert start <= end

    def test_today_starts_at_local_midnight(self):
        start, _ = resolve_range("today")
        start_dt = datetime.fromisoformat(start).astimezone()
        assert (start_dt.hour, start_dt.minute, start_dt.second) == (0, 0, 0)

    def test_yesterday_ends_where_today_starts(self):
        today_start, _ = resolve_range("today")
        _, yesterday_end = resolve_range("yesterday")
        assert yesterday_end == today_start

    def test_yesterday_spans_exactly_one_day(self):
        start, end = resolve_range("yesterday")
        assert datetime.fromisoformat(end) - datetime.fromisoformat(start) == timedelta(days=1)

    def test_this_month_starts_on_the_first(self):
        start, _ = resolve_range("this_month")
        assert datetime.fromisoformat(start).astimezone().day == 1

    def test_this_week_starts_on_monday(self):
        start, _ = resolve_range("this_week")
        assert datetime.fromisoformat(start).astimezone().weekday() == 0

    def test_all_time_spans_everything(self):
        start, end = resolve_range("all_time")
        assert start < "1000-01-01"
        assert end > "9000-01-01"

    def test_unknown_preset_falls_back_to_today(self):
        # Compare start only — "end" is "now" and may drift a few microseconds between calls
        assert resolve_range("bogus")[0] == resolve_range("today")[0]


class TestTotalsBetween:

    def test_excludes_data_outside_range(self, ledger):
        ledger.record(pv_power=1000.0, grid_power=0.0, battery_power=0.0, rate=0.30, interval_seconds=3600)
        future_start = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        future_end = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
        totals = ledger.totals_between(future_start, future_end)
        assert totals.solar_kwh == 0.0

    def test_includes_data_inside_range(self, ledger):
        ledger.record(pv_power=1000.0, grid_power=0.0, battery_power=0.0, rate=0.30, interval_seconds=3600)
        start = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        end = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        totals = ledger.totals_between(start, end)
        assert totals.solar_kwh == pytest.approx(1.0)

    def test_end_boundary_excludes_later_rows(self, ledger):
        ledger.record(pv_power=1000.0, grid_power=0.0, battery_power=0.0, rate=0.30, interval_seconds=3600)
        start = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        end = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        totals = ledger.totals_between(start, end)
        assert totals.solar_kwh == 0.0


class TestDailyTotals:

    def test_single_day_bucket(self, ledger):
        ledger.record(pv_power=1000.0, grid_power=0.0, battery_power=-500.0, rate=0.30, interval_seconds=3600)
        start = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        end = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        daily = ledger.daily_totals(start, end)
        assert len(daily) == 1
        assert daily[0]["date"] == datetime.now().astimezone().date().isoformat()
        assert daily[0]["solar_savings"] == pytest.approx(0.30)

    def test_empty_range_returns_empty_list(self, ledger):
        start = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        end = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
        assert ledger.daily_totals(start, end) == []

    def test_multiple_intervals_same_day_combine(self, ledger):
        ledger.record(pv_power=1000.0, grid_power=0.0, battery_power=0.0, rate=0.30, interval_seconds=3600)
        ledger.record(pv_power=500.0, grid_power=0.0, battery_power=0.0, rate=0.30, interval_seconds=3600)
        start = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        end = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        daily = ledger.daily_totals(start, end)
        assert len(daily) == 1
        assert daily[0]["solar_kwh"] == pytest.approx(1.5)


class TestCircuitDailyTotals:

    def test_single_day_bucket(self, ledger):
        ledger.record_circuit(name="EV Charger", power=7000.0, rate=0.30, interval_seconds=3600)
        start = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        end = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        daily = ledger.circuit_daily_totals("EV Charger", start, end)
        assert len(daily) == 1
        assert daily[0]["kwh"] == pytest.approx(7.0)

    def test_empty_range_returns_empty_list(self, ledger):
        start = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        end = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
        assert ledger.circuit_daily_totals("EV Charger", start, end) == []
