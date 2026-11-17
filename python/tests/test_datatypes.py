import pytest

from optolink.datatypes import DataType, decode, encode


@pytest.mark.parametrize(
    "dtype, data, divider, expected",
    [
        (DataType.BOOLEAN, b"\x00", 1, False),
        (DataType.BOOLEAN, b"\x01", 1, True),
        (DataType.BYTE, b"\xff", 1, -1),
        (DataType.UBYTE, b"\xff", 1, 255),
        (DataType.SHORT, b"\xd5\x00", 10, 21.3),
        (DataType.SHORT, b"\xf6\xff", 10, -1.0),
        (DataType.USHORT, b"\xf6\xff", 1, 65526),
        (DataType.INT, b"\xfe\xff\xff\xff", 1, -2),
        (DataType.UINT, b"\x10\x0e\x00\x00", 3600, 1.0),
        (DataType.DATE, bytes.fromhex("2026111406123059"), 1, "2026-11-14T12:30:59"),
    ],
)
def test_decode(dtype, data, divider, expected):
    assert decode(dtype, data, divider) == expected


def test_decode_too_short():
    with pytest.raises(ValueError):
        decode(DataType.SHORT, b"\x01")


@pytest.mark.parametrize(
    "dtype, value, divider, expected",
    [
        (DataType.BOOLEAN, True, 1, b"\x01"),
        (DataType.BOOLEAN, "OFF", 1, b"\x00"),
        (DataType.BOOLEAN, "on", 1, b"\x01"),
        (DataType.BYTE, -5, 1, b"\xfb"),
        (DataType.UBYTE, "22", 1, b"\x16"),
        (DataType.BYTE, 1.3, 10, b"\x0d"),
        (DataType.SHORT, 21.3, 10, b"\xd5\x00"),
        (DataType.UINT, 70000, 1, b"\x70\x11\x01\x00"),
    ],
)
def test_encode(dtype, value, divider, expected):
    assert encode(dtype, value, divider) == expected


@pytest.mark.parametrize(
    "dtype, value",
    [
        (DataType.UBYTE, 256),
        (DataType.UBYTE, -1),
        (DataType.BYTE, 128),
        (DataType.SHORT, "abc"),
        (DataType.SHORT, float("inf")),
        (DataType.BOOLEAN, "maybe"),
        (DataType.BOOLEAN, 2),
        (DataType.DATE, "2026-11-14T12:00:00"),
    ],
)
def test_encode_invalid(dtype, value):
    with pytest.raises(ValueError):
        encode(dtype, value)


def test_timer_roundtrip():
    slots = [{"on": "05:30", "off": "08:00"}, {"on": "16:00", "off": "24:00"}]
    data = encode(DataType.TIMER, slots)
    assert data == bytes([5 << 3 | 3, 8 << 3, 16 << 3, 24 << 3, 0xFF, 0xFF, 0xFF, 0xFF])
    assert decode(DataType.TIMER, data) == slots


def test_timer_legacy_string():
    data = encode(DataType.TIMER, "On:06:00Off:22:10On:--:--Off:--:--On:--:--Off:--:--On:--:--Off:--:--")
    assert decode(DataType.TIMER, data) == [{"on": "06:00", "off": "22:10"}]


def test_timer_empty():
    assert encode(DataType.TIMER, []) == b"\xff" * 8
    assert decode(DataType.TIMER, b"\xff" * 8) == []


@pytest.mark.parametrize(
    "value",
    [
        [{"on": "22:00", "off": "06:00"}],  # on after off
        [{"on": "06:05", "off": "07:00"}],  # not a multiple of 10 minutes
        [{"on": "24:10", "off": "24:10"}],
        [{"on": "25:00", "off": "25:00"}],
        [{"on": "06:00"}],
        [{"on": "01:00", "off": "02:00"}] * 5,
        "On:06:00",
    ],
)
def test_timer_invalid(value):
    with pytest.raises(ValueError):
        encode(DataType.TIMER, value)
