"""
Energy ledger: turns PV/grid/battery power readings into running dollar
totals using the electricity rate in effect at the time, so the dashboard
can show at-a-glance solar/battery savings vs. grid cost — plus a generic
per-circuit tracker (EV charger, AC compressor, whole-home main panel, ...)
for cost breakdowns and a monthly bill estimate.

Rows are appended per poll interval to a small SQLite database — cheap to
query for "today"/"this month"/"lifetime" rollups, and survives restarts.
"""

import calendar
import logging
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional

logger = logging.getLogger(__name__)

# Sentinel upper bound for open-ended ranges ("since X, through now and beyond").
_FAR_FUTURE_UTC_ISO = "9999-12-31T23:59:59+00:00"
_FAR_PAST_UTC_ISO = "0000-01-01T00:00:00+00:00"

RANGE_PRESETS = [
    ("today", "Today"),
    ("yesterday", "Yesterday"),
    ("this_week", "This Week"),
    ("this_month", "This Month"),
    ("last_30_days", "Last 30 Days"),
    ("this_year", "This Year"),
    ("all_time", "All Time"),
]


def resolve_range(preset: str) -> tuple:
    """Resolve a range preset to (start_utc_iso, end_utc_iso), end exclusive."""
    now_local = datetime.now().astimezone()
    end_local = now_local
    midnight = now_local.replace(hour=0, minute=0, second=0, microsecond=0)

    if preset == "yesterday":
        end_local = midnight
        start_local = midnight - timedelta(days=1)
    elif preset == "this_week":
        start_local = midnight - timedelta(days=midnight.weekday())
    elif preset == "this_month":
        start_local = midnight.replace(day=1)
    elif preset == "last_30_days":
        start_local = midnight - timedelta(days=30)
    elif preset == "this_year":
        start_local = midnight.replace(month=1, day=1)
    elif preset == "all_time":
        return _FAR_PAST_UTC_ISO, _FAR_FUTURE_UTC_ISO
    else:  # "today" or unknown
        start_local = midnight

    return start_local.astimezone(timezone.utc).isoformat(), end_local.astimezone(timezone.utc).isoformat()


@dataclass
class EnergyTotals:
    """Accumulated energy (kWh) and cost/savings (currency) over a period."""
    grid_kwh: float = 0.0
    solar_kwh: float = 0.0
    battery_discharge_kwh: float = 0.0
    battery_charge_kwh: float = 0.0
    grid_cost: float = 0.0
    solar_savings: float = 0.0
    battery_savings: float = 0.0
    grid_charge_cost: float = 0.0

    def to_dict(self) -> dict:
        return {
            "grid_kwh": round(self.grid_kwh, 3),
            "solar_kwh": round(self.solar_kwh, 3),
            "battery_discharge_kwh": round(self.battery_discharge_kwh, 3),
            "battery_charge_kwh": round(self.battery_charge_kwh, 3),
            "grid_cost": round(self.grid_cost, 2),
            "solar_savings": round(self.solar_savings, 2),
            "battery_savings": round(self.battery_savings, 2),
            "grid_charge_cost": round(self.grid_charge_cost, 2),
            "total_savings": round(self.solar_savings + self.battery_savings, 2),
        }


@dataclass
class CircuitTotals:
    """Accumulated energy (kWh) and cost (currency) for one tracked circuit."""
    kwh: float = 0.0
    cost: float = 0.0

    def to_dict(self) -> dict:
        return {"kwh": round(self.kwh, 3), "cost": round(self.cost, 2)}


# Columns added after the initial release — migrated in via ALTER TABLE so
# an already-populated database keeps its history instead of being reset.
_ENERGY_LOG_MIGRATIONS = {
    "grid_charging": "INTEGER NOT NULL DEFAULT 0",
    "battery_charge_kwh": "REAL NOT NULL DEFAULT 0",
    "grid_charge_cost": "REAL NOT NULL DEFAULT 0",
}


class EnergyLedger:
    """Accumulates energy/cost totals in SQLite, one row per poll interval."""

    def __init__(self, db_path: str = "energy_ledger.db"):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._init_schema()

    def _init_schema(self):
        with self._lock:
            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS energy_log (
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
            existing = {row[1] for row in self._conn.execute("PRAGMA table_info(energy_log)")}
            for column, coltype in _ENERGY_LOG_MIGRATIONS.items():
                if column not in existing:
                    self._conn.execute(f"ALTER TABLE energy_log ADD COLUMN {column} {coltype}")

            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_energy_log_timestamp ON energy_log(timestamp)"
            )

            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS circuit_log (
                    timestamp TEXT NOT NULL,
                    circuit_name TEXT NOT NULL,
                    power REAL,
                    kwh REAL NOT NULL,
                    cost REAL NOT NULL
                )"""
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_circuit_log_name_ts ON circuit_log(circuit_name, timestamp)"
            )
            self._conn.commit()

    # -- PV / grid / battery ------------------------------------------------

    def record(
        self,
        pv_power: Optional[float],
        grid_power: Optional[float],
        battery_power: float,
        rate: Optional[float],
        interval_seconds: float,
        grid_charging: bool = False,
    ):
        """Record one poll interval's worth of energy flow and cost.

        battery_power follows this project's convention (positive =
        charging, negative = discharging). Discharging always counts as
        savings; charging only costs money when grid_charging is True —
        i.e. the inverter is actively drawing AC/grid power to charge the
        battery, as opposed to charging from solar surplus (which costs
        nothing extra, since PV savings already counts that production
        regardless of where it goes). Skipped entirely when the rate is
        unknown, since there's nothing meaningful to attribute cost to.
        """
        if rate is None:
            logger.debug("Energy ledger: no rate available, skipping interval")
            return

        hours = interval_seconds / 3600.0
        solar_kwh = (pv_power or 0.0) * hours / 1000.0
        grid_kwh = (grid_power or 0.0) * hours / 1000.0
        battery_discharge_kwh = max(0.0, -battery_power) * hours / 1000.0
        battery_charge_kwh = max(0.0, battery_power) * hours / 1000.0 if grid_charging else 0.0

        grid_cost = grid_kwh * rate
        solar_savings = solar_kwh * rate
        grid_charge_cost = battery_charge_kwh * rate
        battery_savings = (battery_discharge_kwh * rate) - grid_charge_cost

        with self._lock:
            self._conn.execute(
                """INSERT INTO energy_log (
                    timestamp, pv_power, grid_power, battery_power, rate,
                    solar_kwh, grid_kwh, battery_discharge_kwh,
                    grid_cost, solar_savings, battery_savings,
                    grid_charging, battery_charge_kwh, grid_charge_cost
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    datetime.now(timezone.utc).isoformat(),
                    pv_power, grid_power, battery_power, rate,
                    solar_kwh, grid_kwh, battery_discharge_kwh,
                    grid_cost, solar_savings, battery_savings,
                    int(grid_charging), battery_charge_kwh, grid_charge_cost,
                ),
            )
            self._conn.commit()

    def totals_between(self, start_utc_iso: str, end_utc_iso: str = _FAR_FUTURE_UTC_ISO) -> EnergyTotals:
        """Sum totals for rows with start_utc_iso <= timestamp < end_utc_iso."""
        with self._lock:
            row = self._conn.execute(
                """SELECT
                    COALESCE(SUM(grid_kwh), 0),
                    COALESCE(SUM(solar_kwh), 0),
                    COALESCE(SUM(battery_discharge_kwh), 0),
                    COALESCE(SUM(battery_charge_kwh), 0),
                    COALESCE(SUM(grid_cost), 0),
                    COALESCE(SUM(solar_savings), 0),
                    COALESCE(SUM(battery_savings), 0),
                    COALESCE(SUM(grid_charge_cost), 0)
                FROM energy_log WHERE timestamp >= ? AND timestamp < ?""",
                (start_utc_iso, end_utc_iso),
            ).fetchone()
        return EnergyTotals(
            grid_kwh=row[0], solar_kwh=row[1],
            battery_discharge_kwh=row[2], battery_charge_kwh=row[3],
            grid_cost=row[4], solar_savings=row[5],
            battery_savings=row[6], grid_charge_cost=row[7],
        )

    def totals_since(self, since_utc_iso: str) -> EnergyTotals:
        """Sum totals for rows with timestamp >= since_utc_iso (UTC ISO 8601)."""
        return self.totals_between(since_utc_iso)

    def totals_today(self) -> EnergyTotals:
        """Totals since local midnight."""
        return self.totals_since(_local_midnight_utc_iso())

    def totals_this_month(self) -> EnergyTotals:
        """Totals since the 1st of the current calendar month, local time."""
        return self.totals_since(_local_month_start_utc_iso())

    def totals_lifetime(self) -> EnergyTotals:
        """Totals across every recorded interval."""
        return self.totals_since(_FAR_PAST_UTC_ISO)

    def daily_totals(self, start_utc_iso: str, end_utc_iso: str) -> List[dict]:
        """Per-local-day breakdown of solar/battery/grid totals within [start, end)."""
        with self._lock:
            rows = self._conn.execute(
                """SELECT timestamp, solar_kwh, grid_kwh, battery_discharge_kwh, battery_charge_kwh,
                    grid_cost, solar_savings, battery_savings, grid_charge_cost
                FROM energy_log WHERE timestamp >= ? AND timestamp < ? ORDER BY timestamp""",
                (start_utc_iso, end_utc_iso),
            ).fetchall()

        buckets = {}
        for row in rows:
            local_date = datetime.fromisoformat(row[0]).astimezone().date().isoformat()
            b = buckets.setdefault(local_date, EnergyTotals())
            b.solar_kwh += row[1]
            b.grid_kwh += row[2]
            b.battery_discharge_kwh += row[3]
            b.battery_charge_kwh += row[4]
            b.grid_cost += row[5]
            b.solar_savings += row[6]
            b.battery_savings += row[7]
            b.grid_charge_cost += row[8]

        return [{"date": d, **buckets[d].to_dict()} for d in sorted(buckets)]

    def latest(self) -> Optional[dict]:
        """The most recently recorded interval's raw inputs, or None if empty."""
        with self._lock:
            row = self._conn.execute(
                """SELECT timestamp, pv_power, grid_power, battery_power, rate, grid_charging
                FROM energy_log ORDER BY rowid DESC LIMIT 1"""
            ).fetchone()
        if row is None:
            return None
        return {
            "timestamp": row[0],
            "pv_power": row[1],
            "grid_power": row[2],
            "battery_power": row[3],
            "rate": row[4],
            "grid_charging": bool(row[5]),
        }

    # -- Generic tracked circuits (EV charger, AC compressor, main panel, ...) --

    def record_circuit(
        self,
        name: str,
        power: Optional[float],
        rate: Optional[float],
        interval_seconds: float,
    ):
        """Record one poll interval's worth of energy/cost for a named circuit."""
        if rate is None:
            logger.debug(f"Energy ledger: no rate available, skipping circuit '{name}'")
            return

        hours = interval_seconds / 3600.0
        kwh = (power or 0.0) * hours / 1000.0
        cost = kwh * rate

        with self._lock:
            self._conn.execute(
                "INSERT INTO circuit_log (timestamp, circuit_name, power, kwh, cost) VALUES (?, ?, ?, ?, ?)",
                (datetime.now(timezone.utc).isoformat(), name, power, kwh, cost),
            )
            self._conn.commit()

    def circuit_totals_between(self, name: str, start_utc_iso: str, end_utc_iso: str = _FAR_FUTURE_UTC_ISO) -> CircuitTotals:
        with self._lock:
            row = self._conn.execute(
                """SELECT COALESCE(SUM(kwh), 0), COALESCE(SUM(cost), 0)
                FROM circuit_log WHERE circuit_name = ? AND timestamp >= ? AND timestamp < ?""",
                (name, start_utc_iso, end_utc_iso),
            ).fetchone()
        return CircuitTotals(kwh=row[0], cost=row[1])

    def circuit_totals_since(self, name: str, since_utc_iso: str) -> CircuitTotals:
        return self.circuit_totals_between(name, since_utc_iso)

    def circuit_totals_today(self, name: str) -> CircuitTotals:
        return self.circuit_totals_since(name, _local_midnight_utc_iso())

    def circuit_totals_this_month(self, name: str) -> CircuitTotals:
        return self.circuit_totals_since(name, _local_month_start_utc_iso())

    def circuit_totals_lifetime(self, name: str) -> CircuitTotals:
        return self.circuit_totals_since(name, _FAR_PAST_UTC_ISO)

    def circuit_daily_totals(self, name: str, start_utc_iso: str, end_utc_iso: str) -> List[dict]:
        """Per-local-day kWh/cost breakdown for a named circuit within [start, end)."""
        with self._lock:
            rows = self._conn.execute(
                """SELECT timestamp, kwh, cost FROM circuit_log
                WHERE circuit_name = ? AND timestamp >= ? AND timestamp < ? ORDER BY timestamp""",
                (name, start_utc_iso, end_utc_iso),
            ).fetchall()

        buckets = {}
        for ts, kwh, cost in rows:
            local_date = datetime.fromisoformat(ts).astimezone().date().isoformat()
            b = buckets.setdefault(local_date, CircuitTotals())
            b.kwh += kwh
            b.cost += cost

        return [{"date": d, **buckets[d].to_dict()} for d in sorted(buckets)]

    def circuit_latest(self, name: str) -> Optional[dict]:
        """The most recently recorded power reading for a named circuit."""
        with self._lock:
            row = self._conn.execute(
                """SELECT timestamp, power FROM circuit_log
                WHERE circuit_name = ? ORDER BY rowid DESC LIMIT 1""",
                (name,),
            ).fetchone()
        if row is None:
            return None
        return {"timestamp": row[0], "power": row[1]}

    def monthly_bill_estimate(self, name: str) -> Optional[float]:
        """Project this circuit's month-to-date cost across the full month.

        Assumes the rate mix seen so far this month (a blend of whatever
        peak/off-peak periods have already occurred) continues for the
        remaining days — a rough estimate, not a precise forecast.
        """
        month_totals = self.circuit_totals_this_month(name)
        if month_totals.kwh <= 0:
            return None

        now_local = datetime.now().astimezone()
        days_elapsed = max(1, now_local.day)
        days_in_month = calendar.monthrange(now_local.year, now_local.month)[1]
        return month_totals.cost / days_elapsed * days_in_month

    def close(self):
        with self._lock:
            self._conn.close()


def _local_midnight_utc_iso() -> str:
    midnight_local = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight_local.astimezone(timezone.utc).isoformat()


def _local_month_start_utc_iso() -> str:
    month_start_local = datetime.now().astimezone().replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    return month_start_local.astimezone(timezone.utc).isoformat()
