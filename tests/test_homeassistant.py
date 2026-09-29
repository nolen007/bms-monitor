"""
Tests for the minimal Home Assistant REST client.
"""

import json
import urllib.error
from io import BytesIO
from unittest.mock import patch

import pytest
from eg4_monitor.homeassistant import HomeAssistantClient


def _make_response(state):
    body = json.dumps({"state": state}).encode()
    return BytesIO(body)


@pytest.fixture
def client():
    return HomeAssistantClient(
        url="https://ha.example.com",
        token="test-token",
        pv_entity="sensor.pv_power",
        grid_entity="sensor.grid_power",
        rate_entity="input_number.electricity_rate",
    )


class TestGetSnapshot:

    def test_all_available(self, client):
        responses = {
            "sensor.pv_power": "1234.5",
            "sensor.grid_power": "0.0",
            "input_number.electricity_rate": "0.302554",
        }

        def fake_urlopen(request, timeout=None):
            entity_id = request.full_url.rsplit("/", 1)[-1]
            return _make_response(responses[entity_id])

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            snap = client.get_snapshot()

        assert snap.pv_power == pytest.approx(1234.5)
        assert snap.grid_power == pytest.approx(0.0)
        assert snap.rate == pytest.approx(0.302554)

    def test_unavailable_state_returns_none(self, client):
        with patch("urllib.request.urlopen", return_value=_make_response("unavailable")):
            value = client._get_state("sensor.pv_power")
        assert value is None

    def test_network_error_returns_none(self, client):
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("no route")):
            value = client._get_state("sensor.pv_power")
        assert value is None

    def test_empty_entity_id_returns_none(self, client):
        assert client._get_state("") is None

    def test_authorization_header_sent(self, client):
        captured = {}

        def fake_urlopen(request, timeout=None):
            captured["headers"] = dict(request.header_items())
            return _make_response("100")

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            client._get_state("sensor.pv_power")

        assert captured["headers"]["Authorization"] == "Bearer test-token"
