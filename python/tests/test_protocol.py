import pytest

from optolink.device import Optolink
from optolink.protocol import DeviceError, KWProtocol, OptolinkError, P300Protocol
from optolink.simulator import SimulatedKW, SimulatedP300


def test_p300_read_and_write():
    controller = SimulatedP300({0x0800: b"\xd5\x00"})
    protocol = P300Protocol(controller)
    protocol.open()
    assert protocol.read(0x0800, 2) == b"\xd5\x00"
    protocol.write(0x2306, b"\x16")
    assert controller.memory[0x2306] == 0x16


def test_p300_telegram_format():
    controller = SimulatedP300()
    protocol = P300Protocol(controller)
    protocol.open()
    controller.received.clear()
    sent = []
    original = controller.write
    controller.write = lambda data: (sent.append(bytes(data)), original(data))[1]
    protocol.read(0x0800, 2)
    assert sent == [bytes.fromhex("41 05 00 01 08 00 02 10")]


def test_p300_error_answer():
    controller = SimulatedP300()
    controller.invalid_addresses.add(0x1234)
    protocol = P300Protocol(controller)
    protocol.open()
    with pytest.raises(DeviceError):
        protocol.read(0x1234, 1)


def test_p300_without_session():
    protocol = P300Protocol(SimulatedP300())
    with pytest.raises(OptolinkError):
        protocol.read(0x0800, 2)


def test_kw_read_and_write():
    controller = SimulatedKW({0x0800: b"\xd5\x00"})
    protocol = KWProtocol(controller)
    assert protocol.read(0x0800, 2) == b"\xd5\x00"
    protocol.write(0x2000, bytes(range(8)))
    assert controller.memory[0x2000:0x2008] == bytes(range(8))


def test_device_reconnects_after_lost_session():
    controller = SimulatedP300({0x0800: b"\x01\x02"})
    device = Optolink("fake", "300", port_factory=lambda url, timeout: controller)
    assert device.read(0x0800, 2) == b"\x01\x02"
    controller.drop_next_ack = True
    assert device.read(0x0800, 2) == b"\x01\x02"
    assert device.connected


def test_device_gives_up():
    def broken(url, timeout):
        raise OSError("adapter not found")

    device = Optolink("fake", "300", port_factory=broken)
    with pytest.raises(OptolinkError, match="adapter not found"):
        device.read(0x0800, 2)
    assert not device.connected


def test_device_does_not_retry_device_errors():
    controller = SimulatedP300()
    controller.invalid_addresses.add(0x1234)
    connects = []

    def factory(url, timeout):
        connects.append(url)
        return controller

    device = Optolink("fake", "300", port_factory=factory)
    with pytest.raises(DeviceError):
        device.read(0x1234, 1)
    assert len(connects) == 1


def test_device_close_ends_session():
    controller = SimulatedP300()
    device = Optolink("fake", "300", port_factory=lambda url, timeout: controller)
    device.read(0x0800, 2)
    device.close()
    assert not controller.session
    assert controller.closed
    assert not device.connected
