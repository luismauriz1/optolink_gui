"""Reading the XML configuration (same format as the optolink.xml of the Java version)."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from .datatypes import DataType
from .protocol import PROTOCOLS

_HOST_PORT = re.compile(r"^[\w.-]+:\d{1,5}$")


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Telegram:
    address: int
    type: DataType
    divider: float = 1
    readonly: bool = False

    @property
    def length(self) -> int:
        return self.type.length

    @property
    def writable(self) -> bool:
        return self.type.writable and not self.readonly


@dataclass
class Channel:
    id: str
    telegram: Telegram
    description: str = ""
    hidden: bool = False  # id starts with '*': not listed, but readable on request


@dataclass
class Thing:
    id: str
    type: str
    description: str = ""
    hidden: bool = False
    channels: dict[str, Channel] = field(default_factory=dict)


@dataclass
class Config:
    tty: str
    protocol: str
    device: str = ""
    adapter_id: str = ""
    timeout: float = 2.0
    things: dict[str, Thing] = field(default_factory=dict)

    @property
    def url(self) -> str:
        """The tty as pyserial URL: "192.168.1.10:10001" becomes "socket://192.168.1.10:10001"."""
        if "://" not in self.tty and _HOST_PORT.match(self.tty):
            return f"socket://{self.tty}"
        return self.tty


def load_config(path: str | Path) -> Config:
    raw = Path(path).read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    # the declared encoding is already handled above
    text = re.sub(r"^\s*<\?xml[^>]*\?>", "", text)
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ConfigError(f"{path}: {exc}") from None
    return parse_config(root)


def parse_config(root: ET.Element) -> Config:
    if root.tag != "optolink":
        raise ConfigError(f"root element must be <optolink>, not <{root.tag}>")
    protocol = (root.get("protocol") or "").upper()
    if protocol not in PROTOCOLS:
        raise ConfigError(f"protocol must be one of {', '.join(PROTOCOLS)}, not {root.get('protocol')!r}")
    tty = _text(root, "tty")
    if not tty:
        raise ConfigError("<tty> is missing")

    config = Config(
        tty=tty,
        protocol=protocol,
        device=root.get("device", ""),
        adapter_id=_text(root, "adapterID"),
        timeout=_number(_text(root, "ttytimeout") or "2000", "ttytimeout") / 1000,
    )
    for element in root.findall("thing"):
        thing = _parse_thing(element)
        config.things[thing.id] = thing
    return config


def _parse_thing(element: ET.Element) -> Thing:
    thing_id, hidden = _id(element, "thing")
    thing = Thing(
        id=thing_id,
        type=element.get("type", ""),
        description=_text(element, "description"),
        hidden=hidden,
    )
    for channel_element in element.findall("channel"):
        channel_id, channel_hidden = _id(channel_element, f"channel in thing '{thing_id}'")
        where = f"{thing_id}.{channel_id}"
        telegram = channel_element.find("telegram")
        if telegram is None:
            raise ConfigError(f"{where}: <telegram> is missing")
        thing.channels[channel_id] = Channel(
            id=channel_id,
            telegram=_parse_telegram(telegram, where),
            description=_text(channel_element, "description"),
            hidden=channel_hidden,
        )
    return thing


def _parse_telegram(element: ET.Element, where: str) -> Telegram:
    try:
        address = int(element.get("address", ""), 16)
    except ValueError:
        raise ConfigError(f"{where}: invalid address {element.get('address')!r}") from None
    if not 0 <= address <= 0xFFFF:
        raise ConfigError(f"{where}: address {element.get('address')} out of range")
    try:
        dtype = DataType((element.get("type") or "").lower())
    except ValueError:
        types = ", ".join(t.value for t in DataType)
        raise ConfigError(f"{where}: unknown type {element.get('type')!r}, supported: {types}") from None
    divider = _number(element.get("divider") or "1", f"{where}: divider")
    if divider == 0:
        raise ConfigError(f"{where}: divider must not be 0")
    return Telegram(
        address=address,
        type=dtype,
        divider=divider,
        readonly=element.get("readonly", "false").lower() == "true",
    )


def _id(element: ET.Element, what: str) -> tuple[str, bool]:
    raw = (element.get("id") or "").strip()
    if not raw.lstrip("*"):
        raise ConfigError(f"{what} without id")
    return raw.lstrip("*"), raw.startswith("*")


def _text(element: ET.Element, tag: str) -> str:
    return (element.findtext(tag) or "").strip()


def _number(text: str, what: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise ConfigError(f"{what}: {text!r} is not a number") from None
    return int(value) if value.is_integer() else value
