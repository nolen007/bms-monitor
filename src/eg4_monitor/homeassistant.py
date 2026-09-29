"""
Minimal Home Assistant REST client.

Reads a handful of sensor states (PV power, grid power, electricity rate,
grid-charge mode) plus arbitrary extra entities ("circuits", e.g. an EV
charger or AC compressor CT clamp) so the energy ledger can attribute
cost/savings to solar, battery, and individual loads.

Uses only the standard library — no new dependency for a few GET requests.
"""

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class GridSnapshot:
    """A point-in-time read of PV power, grid power, rate, and charge mode."""
    pv_power: Optional[float] = None    # watts
    grid_power: Optional[float] = None  # watts
    rate: Optional[float] = None        # currency per kWh
    grid_charging: bool = False         # inverter is set to charge from AC/grid


class HomeAssistantClient:
    """Reads entity states from a Home Assistant instance's REST API."""

    def __init__(
        self,
        url: str,
        token: str,
        pv_entity: str,
        grid_entity: str,
        rate_entity: str,
        ac_charge_mode_entity: str = "",
        timeout: float = 5.0,
    ):
        self.base_url = url.rstrip("/")
        self.token = token
        self.pv_entity = pv_entity
        self.grid_entity = grid_entity
        self.rate_entity = rate_entity
        self.ac_charge_mode_entity = ac_charge_mode_entity
        self.timeout = timeout

    def _request(self, entity_id: str) -> Optional[dict]:
        """Fetch one entity's raw state payload, or None if unavailable/unreachable."""
        if not entity_id:
            return None

        url = f"{self.base_url}/api/states/{entity_id}"
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read())
        except (urllib.error.URLError, TimeoutError) as e:
            logger.warning(f"Home Assistant: could not reach {entity_id}: {e}")
        except json.JSONDecodeError as e:
            logger.warning(f"Home Assistant: malformed response for {entity_id}: {e}")
        return None

    def _get_state(self, entity_id: str) -> Optional[float]:
        """Fetch one entity's numeric state, or None if unavailable/non-numeric."""
        payload = self._request(entity_id)
        if payload is None:
            return None
        try:
            return float(payload["state"])
        except (ValueError, KeyError) as e:
            logger.debug(f"Home Assistant: {entity_id} has no numeric state: {e}")
            return None

    def _get_raw_state(self, entity_id: str) -> Optional[str]:
        """Fetch one entity's raw (string) state, e.g. 'on'/'off'."""
        payload = self._request(entity_id)
        if payload is None:
            return None
        return payload.get("state")

    def get_power(self, entity_id: str) -> Optional[float]:
        """Read any entity's numeric power state — used for arbitrary tracked circuits."""
        return self._get_state(entity_id)

    def get_snapshot(self) -> GridSnapshot:
        """Fetch PV power, grid power, electricity rate, and grid-charge mode."""
        return GridSnapshot(
            pv_power=self._get_state(self.pv_entity),
            grid_power=self._get_state(self.grid_entity),
            rate=self._get_state(self.rate_entity),
            grid_charging=self._get_raw_state(self.ac_charge_mode_entity) == "on",
        )
