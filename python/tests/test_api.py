import pytest
from fastapi.testclient import TestClient

from optolink.api import create_app
from optolink.config import Channel, Config, Telegram, Thing
from optolink.datatypes import DataType
from optolink.device import Optolink
from optolink.simulator import SimulatedP300


def channel(id, address, dtype, divider=1, hidden=False, readonly=False):
    return Channel(id=id, telegram=Telegram(address, dtype, divider, readonly), hidden=hidden)


@pytest.fixture
def controller():
    return SimulatedP300(
        {
            0x00F8: b"\x20\xcb",
            0x0800: b"\xd5\x00",
            0x0846: b"\x01",
            0x088E: bytes.fromhex("2026111406123059"),
            0x2306: b"\x14",
            0x2000: bytes([6 << 3, 22 << 3]) + b"\xff" * 6,
        }
    )


@pytest.fixture
def client(controller):
    heating = Thing(id="heating", type="heating", description="Heizung")
    for c in (
        channel("systemid", 0x00F8, DataType.USHORT, hidden=True),
        channel("outside_temp", 0x0800, DataType.SHORT, 10),
        channel("systemtime", 0x088E, DataType.DATE),
        channel("pump", 0x0846, DataType.BOOLEAN),
    ):
        heating.channels[c.id] = c
    circuit = Thing(id="circuit1", type="circuit")
    for c in (
        channel("room_temp", 0x2306, DataType.BYTE),
        channel("timer_mo", 0x2000, DataType.TIMER),
        channel("gradient", 0x2305, DataType.BYTE, 10, readonly=True),
    ):
        circuit.channels[c.id] = c
    config = Config(tty="fake", protocol="300", things={"heating": heating, "circuit1": circuit})
    device = Optolink("fake", "300", port_factory=lambda url, timeout: controller)
    with TestClient(create_app(config, device)) as client:
        yield client


def test_docs(client):
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").json()["info"]["title"] == "Optolink API"


def test_list_things(client):
    things = client.get("/api/things").json()
    assert [t["id"] for t in things] == ["heating", "circuit1"]
    heating = things[0]
    assert [c["id"] for c in heating["channels"]] == ["outside_temp", "systemtime", "pump"]
    assert heating["channels"][0] == {
        "id": "outside_temp",
        "description": "",
        "address": "0800",
        "type": "short",
        "divider": 10,
        "writable": True,
        "hidden": False,
    }
    assert heating["channels"][1]["writable"] is False
    hidden = client.get("/api/things/heating", params={"include_hidden": True}).json()
    assert "systemid" in [c["id"] for c in hidden["channels"]]


def test_read_value(client):
    assert client.get("/api/things/heating/channels/outside_temp").json() == {
        "thing": "heating",
        "channel": "outside_temp",
        "value": 21.3,
    }
    assert client.get("/api/things/heating/channels/systemid").json()["value"] == 0xCB20


def test_read_thing(client):
    assert client.get("/api/things/heating/values").json() == {
        "outside_temp": 21.3,
        "systemtime": "2026-11-14T12:30:59",
        "pump": True,
    }
    assert client.get("/api/things/heating/values", params={"channels": "pump,systemid"}).json() == {
        "pump": True,
        "systemid": 0xCB20,
    }


def test_read_all(client, controller):
    controller.invalid_addresses.add(0x2305)
    values = client.get("/api/values").json()
    assert values["circuit1"] == {
        "room_temp": 20,
        "timer_mo": [{"on": "06:00", "off": "22:00"}],
        "gradient": None,
    }


def test_unknown(client):
    assert client.get("/api/things/nothing/values").status_code == 404
    assert client.get("/api/things/heating/channels/nothing").status_code == 404


def test_write(client, controller):
    response = client.put("/api/things/circuit1/channels/room_temp", json={"value": 22})
    assert response.status_code == 200
    assert response.json()["value"] == 22
    assert controller.memory[0x2306] == 22

    response = client.put("/api/things/heating/channels/pump", json={"value": False})
    assert response.json()["value"] is False


def test_write_timer(client, controller):
    slots = [{"on": "05:30", "off": "08:00"}, {"on": "16:00", "off": "24:00"}]
    response = client.put("/api/things/circuit1/channels/timer_mo", json={"value": slots})
    assert response.status_code == 200
    assert response.json()["value"] == slots


def test_write_invalid(client):
    assert client.put("/api/things/circuit1/channels/room_temp", json={"value": 300}).status_code == 422
    assert client.put("/api/things/circuit1/channels/room_temp", json={"value": "warm"}).status_code == 422
    assert client.put("/api/things/circuit1/channels/gradient", json={"value": 1.2}).status_code == 405
    assert client.put("/api/things/heating/channels/systemtime", json={"value": "x"}).status_code == 405


def test_raw(client):
    response = client.get("/api/raw/800", params={"type": "short", "divider": 10})
    assert response.json() == {"address": "0800", "raw": "d5 00", "type": "short", "value": 21.3}
    response = client.get("/api/raw/088E", params={"length": 8})
    assert response.json()["raw"] == "20 26 11 14 06 12 30 59"
    assert client.get("/api/raw/xyz").status_code == 422


def test_device_errors(client, controller):
    controller.invalid_addresses.add(0x0800)
    assert client.get("/api/things/heating/channels/outside_temp").status_code == 502


def test_connection_error():
    def broken(url, timeout):
        raise OSError("no adapter")

    config = Config(tty="fake", protocol="300")
    device = Optolink("fake", "300", port_factory=broken)
    client = TestClient(create_app(config, device))
    response = client.get("/api/raw/0800")
    assert response.status_code == 503
    assert "no adapter" in response.json()["detail"]
