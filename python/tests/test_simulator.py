from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from optolink.api import create_app
from optolink.config import load_config
from optolink.device import Optolink
from optolink.simulator import create_simulator

EXAMPLE = Path(__file__).resolve().parents[1] / "optolink.example.xml"


@pytest.fixture(params=["KW", "300"])
def client(request):
    config = load_config(EXAMPLE)
    config.protocol = request.param
    simulator = create_simulator(config)
    device = Optolink("simulator", config.protocol, port_factory=lambda url, timeout: simulator)
    with TestClient(create_app(config, device)) as client:
        yield client


def test_all_values_plausible(client):
    values = client.get("/api/values").json()
    assert -1 <= values["heating"]["outside_temp"] <= 11
    assert 40 <= values["hotwater"]["temp"] <= 50
    assert values["hotwater"]["set_temp"] == 50
    assert values["heating"]["malfunction"] is False
    assert values["heating"]["systemtime"][:4].isdigit()
    assert values["circuit2"]["room_set_temp"] == 20
    assert values["circuit2"]["timer_mo"] == [{"on": "06:00", "off": "22:00"}]


def test_write_is_kept(client):
    assert client.put("/api/things/hotwater/channels/set_temp", json={"value": 42}).json()["value"] == 42
    assert client.get("/api/things/hotwater/channels/set_temp").json()["value"] == 42
