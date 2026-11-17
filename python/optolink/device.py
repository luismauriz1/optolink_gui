"""Thread safe access to the heating controller with automatic reconnect."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import TypeVar

import serial

from .protocol import PROTOCOLS, DeviceError, OptolinkError, Port, Protocol

log = logging.getLogger(__name__)

T = TypeVar("T")


def open_serial(url: str, timeout: float) -> Port:
    """Open a local serial port (/dev/ttyUSB0, COM3) or a pyserial URL (socket://host:port)."""
    return serial.serial_for_url(
        url,
        baudrate=4800,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_EVEN,
        stopbits=serial.STOPBITS_TWO,
        timeout=timeout,
    )


class Optolink:
    def __init__(
        self,
        url: str,
        protocol: str,
        timeout: float = 2.0,
        retries: int = 3,
        port_factory: Callable[[str, float], Port] = open_serial,
    ):
        if protocol not in PROTOCOLS:
            raise ValueError(f"unknown protocol {protocol!r}, supported: {', '.join(PROTOCOLS)}")
        self.url = url
        self.protocol_name = protocol
        self.timeout = timeout
        self.retries = retries
        self._port_factory = port_factory
        self._port: Port | None = None
        self._protocol: Protocol | None = None
        self._lock = threading.Lock()

    @property
    def connected(self) -> bool:
        return self._protocol is not None

    def read(self, address: int, length: int) -> bytes:
        return self._execute(lambda p: p.read(address, length))

    def write(self, address: int, data: bytes) -> None:
        self._execute(lambda p: p.write(address, data))

    def close(self) -> None:
        with self._lock:
            self._disconnect(graceful=True)

    def _execute(self, action: Callable[[Protocol], T]) -> T:
        with self._lock:
            error: Exception | None = None
            for attempt in range(1, self.retries + 1):
                try:
                    if self._protocol is None:
                        self._connect()
                    return action(self._protocol)
                except DeviceError:
                    raise
                except (OptolinkError, serial.SerialException, OSError) as exc:
                    error = exc
                    log.warning("Optolink communication failed (attempt %d/%d): %s", attempt, self.retries, exc)
                    self._disconnect(graceful=False)
            raise OptolinkError(f"communication with {self.url} failed: {error}") from error

    def _connect(self) -> None:
        log.info("Connecting to %s (protocol %s)", self.url, self.protocol_name)
        self._port = self._port_factory(self.url, self.timeout)
        protocol = PROTOCOLS[self.protocol_name](self._port)
        protocol.open()
        self._protocol = protocol

    def _disconnect(self, graceful: bool) -> None:
        if graceful and self._protocol is not None:
            try:
                self._protocol.close()
            except Exception as exc:  # best effort on shutdown
                log.debug("Closing session failed: %s", exc)
        if self._port is not None:
            try:
                self._port.close()
            except Exception as exc:
                log.debug("Closing port failed: %s", exc)
        self._port = None
        self._protocol = None
