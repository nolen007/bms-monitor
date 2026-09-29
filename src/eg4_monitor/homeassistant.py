"""
Minimal Home Assistant REST client.

Reads a handful of sensor states (PV power, grid power, electricity rate)
so the energy ledger can attribute cost/savings to solar and battery use.
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
    """A point-in-time read of PV power, grid power, and electricity rate."""
    pv_power: Optional[float] = None    # watts
    grid_power: Optional[float] = None  # watts
    rate: Optional[float] = None        # currency per kWh


class HomeAssistantClient:
    """Reads entity states from a Home Assistant instance's REST API."""

    def __init__(
        self,
        url: str,
        token: str,
        pv_entity: str,
        grid_entity: str,
        rate_entity: str,
        timeout: float = 5.0,
    ):
        self.base_url = url.rstrip("/")
        self.token = token
        self.pv_entity = pv_entity
        self.grid_entity = grid_entity
        self.rate_entity = rate_entity
        self.timeout = timeout

    def _get_state(self, entity_id: str) -> Optional[float]:
        """Fetch one entity's numeric state, or None if unavailable/unreachable."""
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
                payload = json.loads(response.read())
            return float(payload["state"])
        except (urllib.error.URLError, TimeoutError) as e:
            logger.warning(f"Home Assistant: could not reach {entity_id}: {e}")
        except (ValueError, KeyError) as e:
            logger.debug(f"Home Assistant: {entity_id} has no numeric state: {e}")
        return None

    def get_snapshot(self) -> GridSnapshot:
        """Fetch PV power, grid power, and electricity rate in one call."""
        return GridSnapshot(
            pv_power=self._get_state(self.pv_entity),
            grid_power=self._get_state(self.grid_entity),
            rate=self._get_state(self.rate_entity),
        )
