"""Encoding and decoding of the Viessmann data types."""

from __future__ import annotations

import re
from collections.abc import Mapping
from enum import Enum
from typing import Any


class DataType(str, Enum):
    BOOLEAN = "boolean"
    BYTE = "byte"
    UBYTE = "ubyte"
    SHORT = "short"
    USHORT = "ushort"
    INT = "int"
    UINT = "uint"
    DATE = "date"  # 8 byte BCD, read only
    TIMER = "timer"  # 4 on/off pairs

    @property
    def length(self) -> int:
        return _LENGTH[self]

    @property
    def writable(self) -> bool:
        return self is not DataType.DATE


# (number of bytes, signed) - Viessmann uses little endian
_INTEGER = {
    DataType.BYTE: (1, True),
    DataType.UBYTE: (1, False),
    DataType.SHORT: (2, True),
    DataType.USHORT: (2, False),
    DataType.INT: (4, True),
    DataType.UINT: (4, False),
}
_LENGTH = {t: n for t, (n, _) in _INTEGER.items()}
_LENGTH.update({DataType.BOOLEAN: 1, DataType.DATE: 8, DataType.TIMER: 8})

_TIME = re.compile(r"(\d{1,2}):(\d{2})")
_TIMER_UNSET = 0xFF


def decode(dtype: DataType, data: bytes, divider: float = 1) -> Any:
    """Convert raw bytes from the controller to a JSON friendly value."""
    if len(data) < dtype.length:
        raise ValueError(f"{dtype.value} needs {dtype.length} bytes, got {len(data)}")
    if dtype is DataType.BOOLEAN:
        return data[0] != 0
    if dtype is DataType.DATE:
        return _decode_date(data)
    if dtype is DataType.TIMER:
        return _decode_timer(data)
    length, signed = _INTEGER[dtype]
    raw = int.from_bytes(data[:length], "little", signed=signed)
    return raw if divider == 1 else raw / divider


def encode(dtype: DataType, value: Any, divider: float = 1) -> bytes:
    """Convert a value (as received by the API) to raw bytes for the controller."""
    if dtype is DataType.BOOLEAN:
        return bytes([1 if _to_bool(value) else 0])
    if dtype is DataType.TIMER:
        return _encode_timer(value)
    if dtype is DataType.DATE:
        raise ValueError("writing date values is not supported")
    length, signed = _INTEGER[dtype]
    try:
        raw = round(float(value) * divider)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{value!r} is not a number") from None
    try:
        return raw.to_bytes(length, "little", signed=signed)
    except OverflowError:
        raise ValueError(f"{value} is out of range for type {dtype.value} (divider {divider})") from None


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("on", "true", "1"):
            return True
        if text in ("off", "false", "0"):
            return False
    raise ValueError(f"{value!r} is not a boolean (use true/false or ON/OFF)")


def _decode_date(data: bytes) -> str:
    # BCD: year (2 bytes), month, day, weekday, hour, minute, second
    h = data[:8].hex()
    return f"{h[0:4]}-{h[4:6]}-{h[6:8]}T{h[10:12]}:{h[12:14]}:{h[14:16]}"


def _decode_timer(data: bytes) -> list[dict[str, str]]:
    slots = []
    for on, off in zip(data[0:8:2], data[1:8:2]):
        if on != _TIMER_UNSET and off != _TIMER_UNSET:
            slots.append({"on": _format_time(on), "off": _format_time(off)})
    return slots


def _format_time(value: int) -> str:
    # bits 7-3: hour, bits 2-0: minute / 10
    return f"{value >> 3:02d}:{(value & 7) * 10:02d}"


def _encode_timer(value: Any) -> bytes:
    if isinstance(value, str):
        # legacy format of the Java version: "On:06:00Off:22:00On:--:--Off:--:--..."
        times = [f"{h}:{m}" for h, m in _TIME.findall(value)]
        if len(times) % 2:
            raise ValueError("switch times have to be on/off pairs")
        slots = list(zip(times[0::2], times[1::2]))
    else:
        try:
            slots = [(s["on"], s["off"]) if isinstance(s, Mapping) else tuple(s) for s in value]
        except (TypeError, KeyError):
            raise ValueError("timer must be a list of {\"on\": \"HH:MM\", \"off\": \"HH:MM\"}") from None
    if len(slots) > 4:
        raise ValueError("a timer has at most 4 switching periods")

    data = bytearray([_TIMER_UNSET] * 8)
    for i, slot in enumerate(slots):
        if len(slot) != 2:
            raise ValueError("every switching period needs an on and an off time")
        on, off = _parse_time(slot[0]), _parse_time(slot[1])
        if on > off:
            raise ValueError(f"switch on time {slot[0]} is after switch off time {slot[1]}")
        data[2 * i], data[2 * i + 1] = on, off
    return bytes(data)


def _parse_time(text: Any) -> int:
    match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(text))
    if not match:
        raise ValueError(f"invalid time {text!r}, expected HH:MM")
    hour, minute = int(match[1]), int(match[2])
    if minute % 10 or minute > 50 or hour > 24 or (hour == 24 and minute):
        raise ValueError(f"invalid time {text!r}: 00:00-24:00 in steps of 10 minutes")
    return hour << 3 | minute // 10
