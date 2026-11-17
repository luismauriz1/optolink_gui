"""Simulated Vitotronic controller for testing without hardware (optolink-api --simulate)."""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable
from datetime import datetime

from .config import Channel, Config
from .datatypes import DataType, encode

log = logging.getLogger(__name__)


class SimulatedController:
    """The controller side of a protocol. Implements the Port interface, so it can replace the serial port."""

    def __init__(self, memory: dict[int, bytes] | None = None):
        self.memory = bytearray(0x10000)
        for address, data in (memory or {}).items():
            self.memory[address : address + len(data)] = data
        self.invalid_addresses: set[int] = set()
        self.received = bytearray()
        self.pending = bytearray()  # bytes waiting to be read by the client
        self.closed = False
        self._sensors: list[tuple[int, DataType, float, Callable[[float], float] | None]] = []

    # Port interface

    def write(self, data: bytes) -> int:
        self.received += data
        self.process()
        return len(data)

    def read(self, size: int = 1) -> bytes:
        chunk = bytes(self.pending[:size])
        del self.pending[:size]
        return chunk

    def reset_input_buffer(self) -> None:
        self.pending.clear()

    def close(self) -> None:
        self.closed = True

    # memory

    def add_channel(self, channel: Channel, thing_id: str = "") -> None:
        """Initialize the memory of a channel with a plausible value."""
        telegram, name = channel.telegram, f"{thing_id}.{channel.id}".lower()
        # 1 byte temperatures are set points, sensors have a divider and 2 bytes
        setpoint = "set" in name or telegram.type in (DataType.BYTE, DataType.UBYTE)
        if telegram.type is DataType.DATE:
            self._sensors.append((telegram.address, telegram.type, 1, None))
        elif telegram.type is DataType.TIMER:
            self._store(telegram.address, encode(DataType.TIMER, [{"on": "06:00", "off": "22:00"}]))
        elif telegram.type is DataType.BOOLEAN:
            self._store(telegram.address, encode(DataType.BOOLEAN, "pump" in name))
        elif "temp" in name and not setpoint:
            base, amplitude = (5, 6) if "outside" in name or "outsite" in name else (45, 5)
            phase = telegram.address % 100
            sensor = lambda now, b=base, a=amplitude, p=phase: b + a * math.sin(now / 300 + p)  # noqa: E731
            try:
                encode(telegram.type, base + amplitude, telegram.divider)
                encode(telegram.type, base - amplitude, telegram.divider)
            except ValueError:
                return
            self._sensors.append((telegram.address, telegram.type, telegram.divider, sensor))
        else:
            default = (50 if "hot" in name else 20) if "temp" in name else 1
            for value in (default, 0):
                try:
                    self._store(telegram.address, encode(telegram.type, value, telegram.divider))
                    break
                except ValueError:
                    continue

    def read_memory(self, address: int, count: int) -> bytes:
        now = time.time()
        for sensor_address, dtype, divider, sensor in self._sensors:
            if sensor is None:
                self._store(sensor_address, _bcd(datetime.now()))
            else:
                self._store(sensor_address, encode(dtype, sensor(now), divider))
        return bytes(self.memory[address : address + count])

    def write_memory(self, address: int, data: bytes) -> None:
        log.info("Simulator: write %04X = %s", address, data.hex(" "))
        self._store(address, data)

    def _store(self, address: int, data: bytes) -> None:
        self.memory[address : address + len(data)] = data

    def process(self) -> None:
        raise NotImplementedError


class SimulatedKW(SimulatedController):
    def read(self, size: int = 1) -> bytes:
        if not self.pending:
            self.pending.append(0x05)  # an idle controller sends 0x05 periodically
        return super().read(size)

    def process(self) -> None:
        buf = self.received
        while len(buf) >= 5:
            if buf[0] != 0x01:
                del buf[0]
                continue
            command, address, length = buf[1], buf[2] << 8 | buf[3], buf[4]
            if command == 0xF7:
                self.pending += self.read_memory(address, length)
                del buf[:5]
            elif command == 0xF4:
                if len(buf) < 5 + length:
                    return
                self.write_memory(address, bytes(buf[5 : 5 + length]))
                self.pending.append(0x00)
                del buf[: 5 + length]
            else:
                del buf[0]


class SimulatedP300(SimulatedController):
    def __init__(self, memory: dict[int, bytes] | None = None):
        super().__init__(memory)
        self.session = False
        self.drop_next_ack = False

    def process(self) -> None:
        buf = self.received
        while buf:
            if buf[0] == 0x04:
                self.session = False
                self.pending.append(0x05)
                del buf[0]
            elif buf[0] == 0x16:
                if len(buf) < 3:
                    return
                self.session = buf[:3] == b"\x16\x00\x00"
                if self.session:
                    self.pending.append(0x06)
                del buf[:3]
            elif buf[0] == 0x41:
                if len(buf) < 2 or len(buf) < buf[1] + 3:
                    return
                length = buf[1]
                payload = bytes(buf[2 : 2 + length])
                checksum = buf[2 + length]
                del buf[: 3 + length]
                if not self.session or checksum != (length + sum(payload)) & 0xFF:
                    self.pending.append(0x15)
                elif self.drop_next_ack:
                    self.drop_next_ack = False  # simulate a lost session
                    self.session = False
                    self.pending.append(0x05)
                else:
                    self.pending.append(0x06)
                    self.pending += self.answer(payload)
            else:
                del buf[0]

    def answer(self, payload: bytes) -> bytes:
        function, address, count = payload[1], payload[2] << 8 | payload[3], payload[4]
        header = bytes([function, payload[2], payload[3], count])
        if address in self.invalid_addresses:
            body = bytes([0x03]) + header
        elif function == 0x01:
            body = bytes([0x01]) + header + self.read_memory(address, count)
        else:
            self.write_memory(address, payload[5 : 5 + count])
            body = bytes([0x01]) + header
        return bytes([0x41, len(body)]) + body + bytes([(len(body) + sum(body)) & 0xFF])


SIMULATORS: dict[str, type[SimulatedController]] = {"KW": SimulatedKW, "300": SimulatedP300}


def create_simulator(config: Config) -> SimulatedController:
    simulator = SIMULATORS[config.protocol]()
    for thing in config.things.values():
        for channel in thing.channels.values():
            simulator.add_channel(channel, thing.id)
    return simulator


def _bcd(now: datetime) -> bytes:
    return bytes.fromhex(
        f"{now.year:04d}{now.month:02d}{now.day:02d}{now.isoweekday():02d}{now.hour:02d}{now.minute:02d}{now.second:02d}"
    )
