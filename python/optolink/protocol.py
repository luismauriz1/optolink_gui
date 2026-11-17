"""The Optolink protocols of the Vitotronic controllers ("KW" and "300")."""

from __future__ import annotations

import logging
from typing import Protocol as _Interface

log = logging.getLogger(__name__)

EOT = 0x04  # end of transmission, resets the controller to KW mode
ENQ = 0x05  # sync byte the controller sends every ~2 seconds in KW mode
ACK = 0x06
SYNC_300 = b"\x16\x00\x00"
START_300 = 0x41


class OptolinkError(Exception):
    """Communication with the Optolink adapter failed."""


class DeviceError(OptolinkError):
    """The heating controller rejected the request (e.g. unknown address)."""


class Port(_Interface):
    """The subset of pyserial's Serial used by the protocols."""

    def read(self, size: int = 1) -> bytes: ...
    def write(self, data: bytes) -> int | None: ...
    def reset_input_buffer(self) -> None: ...
    def close(self) -> None: ...


class Protocol:
    name = ""

    def __init__(self, port: Port):
        self.port = port

    def open(self) -> None:
        """Prepare the controller for requests."""

    def close(self) -> None:
        """End the session with the controller."""

    def read(self, address: int, length: int) -> bytes:
        raise NotImplementedError

    def write(self, address: int, data: bytes) -> None:
        raise NotImplementedError

    def _send(self, data: bytes) -> None:
        log.debug("TxD %s", data.hex(" "))
        self.port.write(data)

    def _recv(self, size: int) -> bytes:
        data = self.port.read(size)
        log.debug("RxD %s", data.hex(" "))
        if len(data) < size:
            raise OptolinkError(f"timeout: expected {size} bytes, got {len(data)}")
        return data


class KWProtocol(Protocol):
    """Protocol of older controllers: every request has to follow a 0x05 of the controller."""

    name = "KW"

    def read(self, address: int, length: int) -> bytes:
        self._wait_for_enq()
        self._send(bytes([0x01, 0xF7, address >> 8, address & 0xFF, length]))
        return self._recv(length)

    def write(self, address: int, data: bytes) -> None:
        self._wait_for_enq()
        self._send(bytes([0x01, 0xF4, address >> 8, address & 0xFF, len(data)]) + data)
        if self._recv(1)[0] != 0x00:
            raise DeviceError(f"write to address {address:04X} was not acknowledged")

    def _wait_for_enq(self) -> None:
        self.port.reset_input_buffer()
        for _ in range(10):
            if self.port.read(1) == bytes([ENQ]):
                return
        raise OptolinkError("no sync byte 0x05 received from the controller")


class P300Protocol(Protocol):
    """Telegram based protocol of newer controllers (VS2 / "300")."""

    name = "300"

    def open(self) -> None:
        self.port.reset_input_buffer()
        self._send(bytes([EOT]))  # end a session that might still be open
        self.port.read(1)  # 0x05 or 0x06, depending on the previous state
        for _ in range(5):
            self.port.reset_input_buffer()
            self._send(SYNC_300)
            if self.port.read(1) == bytes([ACK]):
                log.debug("300 session started")
                return
        raise OptolinkError("could not start 300 session (no ACK on sync)")

    def close(self) -> None:
        self._send(bytes([EOT]))
        self.port.read(1)
        log.debug("300 session closed")

    def read(self, address: int, length: int) -> bytes:
        body = self._request(bytes([0x00, 0x01, address >> 8, address & 0xFF, length]))
        self._check(body, 0x01, address)
        data = body[5 : 5 + body[4]]
        if len(data) != length:
            raise OptolinkError(f"expected {length} data bytes, got {len(data)}")
        return data

    def write(self, address: int, data: bytes) -> None:
        body = self._request(bytes([0x00, 0x02, address >> 8, address & 0xFF, len(data)]) + data)
        self._check(body, 0x02, address)

    def _request(self, payload: bytes) -> bytes:
        """Send a telegram and return the payload of the answer."""
        self._send(bytes([START_300, len(payload)]) + payload + bytes([_checksum(len(payload), payload)]))
        ack = self._recv(1)[0]
        if ack != ACK:
            raise OptolinkError(f"expected ACK 0x06, got 0x{ack:02X} (session lost?)")
        start = self._recv(1)[0]
        if start != START_300:
            raise OptolinkError(f"expected start byte 0x41, got 0x{start:02X}")
        length = self._recv(1)[0]
        body = self._recv(length)
        checksum = self._recv(1)[0]
        if checksum != _checksum(length, body):
            raise OptolinkError("checksum of the answer is wrong")
        return body

    @staticmethod
    def _check(body: bytes, function: int, address: int) -> None:
        if len(body) < 5:
            raise OptolinkError(f"answer too short: {body.hex(' ')}")
        if body[0] == 0x03:
            raise DeviceError(f"controller returned an error for address {address:04X}")
        if body[0] != 0x01 or body[1] != function or (body[2] << 8 | body[3]) != address:
            raise OptolinkError(f"unexpected answer: {body.hex(' ')}")


def _checksum(length: int, payload: bytes) -> int:
    return (length + sum(payload)) & 0xFF


PROTOCOLS: dict[str, type[Protocol]] = {p.name: p for p in (KWProtocol, P300Protocol)}
