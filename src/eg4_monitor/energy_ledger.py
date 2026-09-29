"""
Energy ledger: turns PV/grid/battery power readings into running dollar
totals using the electricity rate in effect at the time, so the dashboard
can show at-a-glance solar/battery savings vs. grid cost.

One row is appended per poll interval to a small SQLite database — cheap
to query for "today" and "lifetime" rollups, and survives restarts.
"""

import logging
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class EnergyTotals:
    """Accumulated energy (kWh) and cost/savings (currency) over a period."""
    grid_kwh: float = 0.0
    solar_kwh: float = 0.0
    battery_discharge_kwh: float = 0.0
    grid_cost: float = 0.0
    solar_savings: float = 0.0
    battery_savings: float = 0.0

    def to_dict(self) -> dict:
        return {
            "grid_kwh": round(self.grid_kwh, 3),
            "solar_kwh": round(self.solar_kwh, 3),
            "battery_discharge_kwh": round(self.battery_discharge_kwh, 3),
            "grid_cost": round(self.grid_cost, 2),
            "solar_savings": round(self.solar_savings, 2),
            "battery_savings": round(self.battery_savings, 2),
            "total_savings": round(self.solar_savings + self.battery_savings, 2),
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
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_energy_log_timestamp ON energy_log(timestamp)"
            )
            self._conn.commit()

    def record(
        self,
        pv_power: Optional[float],
        grid_power: Optional[float],
        battery_power: float,
        rate: Optional[float],
        interval_seconds: float,
    ):
        """Record one poll interval's worth of energy flow and cost.

        battery_power follows this project's convention (positive =
        charging, negative = discharging); only discharge counts as
        money the battery saved you. Skipped entirely when the rate is
        unknown, since there's nothing meaningful to attribute cost to.
        """
        if rate is None:
            logger.debug("Energy ledger: no rate available, skipping interval")
            return

        hours = interval_seconds / 3600.0
        solar_kwh = (pv_power or 0.0) * hours / 1000.0
        grid_kwh = (grid_power or 0.0) * hours / 1000.0
        battery_discharge_kwh = max(0.0, -battery_power) * hours / 1000.0

        grid_cost = grid_kwh * rate
        solar_savings = solar_kwh * rate
        battery_savings = battery_discharge_kwh * rate

        with self._lock:
            self._conn.execute(
                """INSERT INTO energy_log (
                    timestamp, pv_power, grid_power, battery_power, rate,
                    solar_kwh, grid_kwh, battery_discharge_kwh,
                    grid_cost, solar_savings, battery_savings
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    datetime.now(timezone.utc).isoformat(),
                    pv_power, grid_power, battery_power, rate,
                    solar_kwh, grid_kwh, battery_discharge_kwh,
                    grid_cost, solar_savings, battery_savings,
                ),
            )
            self._conn.commit()

    def totals_since(self, since_utc_iso: str) -> EnergyTotals:
        """Sum totals for rows with timestamp >= since_utc_iso (UTC ISO 8601)."""
        with self._lock:
            row = self._conn.execute(
                """SELECT
                    COALESCE(SUM(grid_kwh), 0),
                    COALESCE(SUM(solar_kwh), 0),
                    COALESCE(SUM(battery_discharge_kwh), 0),
                    COALESCE(SUM(grid_cost), 0),
                    COALESCE(SUM(solar_savings), 0),
                    COALESCE(SUM(battery_savings), 0)
                FROM energy_log WHERE timestamp >= ?""",
                (since_utc_iso,),
            ).fetchone()
        return EnergyTotals(
            grid_kwh=row[0], solar_kwh=row[1], battery_discharge_kwh=row[2],
            grid_cost=row[3], solar_savings=row[4], battery_savings=row[5],
        )

    def totals_today(self) -> EnergyTotals:
        """Totals since local midnight."""
        midnight_local = datetime.now().astimezone().replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return self.totals_since(midnight_local.astimezone(timezone.utc).isoformat())

    def totals_lifetime(self) -> EnergyTotals:
        """Totals across every recorded interval."""
        return self.totals_since("0000-01-01T00:00:00+00:00")

    def latest(self) -> Optional[dict]:
        """The most recently recorded interval's raw inputs, or None if empty."""
        with self._lock:
            row = self._conn.execute(
                """SELECT timestamp, pv_power, grid_power, battery_power, rate
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
        }

    def close(self):
        with self._lock:
            self._conn.close()
