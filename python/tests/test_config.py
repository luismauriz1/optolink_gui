from pathlib import Path

import pytest

from optolink.config import ConfigError, load_config
from optolink.datatypes import DataType

REPO = Path(__file__).resolve().parents[2]

XML = """<?xml version="1.0" encoding="ISO-8859-1"?>
<optolink device="20cb" protocol="KW">
  <adapterID>TEST</adapterID>
  <tty>192.168.178.25:10001</tty>
  <ttytimeout>3000</ttytimeout>
  <!-- Adressen f\xfcr diese Anlage -->
  <thing type="heating" id="heating">
    <description>Heizung</description>
    <channel id="*systemid"><telegram address="00F8" type="short"/></channel>
    <channel id="outside_temp">
      <description>Outside temperature</description>
      <telegram address="0800" type="short" divider="10"/>
    </channel>
    <channel id="mode"><telegram address="2301" type="Byte" readonly="true"/></channel>
  </thing>
</optolink>
"""


def write(tmp_path, text, encoding="latin-1"):
    path = tmp_path / "optolink.xml"
    path.write_bytes(text.encode(encoding))
    return path


def test_load(tmp_path):
    config = load_config(write(tmp_path, XML))
    assert config.protocol == "KW"
    assert config.device == "20cb"
    assert config.adapter_id == "TEST"
    assert config.timeout == 3.0
    assert config.url == "socket://192.168.178.25:10001"

    thing = config.things["heating"]
    assert thing.description == "Heizung"
    assert thing.channels["systemid"].hidden
    outside = thing.channels["outside_temp"].telegram
    assert (outside.address, outside.type, outside.divider) == (0x0800, DataType.SHORT, 10)
    assert outside.writable
    assert not thing.channels["mode"].telegram.writable


@pytest.mark.parametrize("tty, url", [("/dev/ttyAMA0", "/dev/ttyAMA0"), ("COM3", "COM3"), ("rfc2217://h:1", "rfc2217://h:1")])
def test_tty_url(tmp_path, tty, url):
    config = load_config(write(tmp_path, XML.replace("192.168.178.25:10001", tty)))
    assert config.url == url


@pytest.mark.parametrize(
    "old, new",
    [
        ('protocol="KW"', 'protocol="GWG"'),
        ('address="0800"', 'address="xyz"'),
        ('type="short" divider', 'type="float" divider'),
        ('divider="10"', 'divider="0"'),
        ("<tty>192.168.178.25:10001</tty>", ""),
    ],
)
def test_invalid(tmp_path, old, new):
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, XML.replace(old, new)))


@pytest.mark.parametrize(
    "path",
    [
        "src/main/resources/optolink.xml",
        "src/main/resources/optolink-ob.xml",
        "python/optolink.example.xml",
    ],
)
def test_shipped_configs(path):
    config = load_config(REPO / path)
    assert config.things
