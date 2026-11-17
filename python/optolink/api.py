"""REST API for the heating controller. The Swagger UI is served at /docs."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from contextlib import asynccontextmanager
from typing import Optional, Union

from fastapi import FastAPI, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from . import __version__
from .config import Channel, Config, Thing
from .datatypes import DataType, decode, encode
from .device import Optolink
from .protocol import DeviceError, OptolinkError

log = logging.getLogger(__name__)

_TIME_PATTERN = r"^\d{1,2}:\d{2}$"


class TimerSlot(BaseModel):
    on: str = Field(pattern=_TIME_PATTERN, examples=["06:00"])
    off: str = Field(pattern=_TIME_PATTERN, examples=["22:00"])


Value = Union[bool, int, float, str, list[TimerSlot], None]


class ChannelInfo(BaseModel):
    id: str
    description: str
    address: str = Field(description="Address on the controller (hex)", examples=["0800"])
    type: DataType
    divider: float
    writable: bool
    hidden: bool


class ThingInfo(BaseModel):
    id: str
    type: str
    description: str
    hidden: bool
    channels: list[ChannelInfo]


class AdapterInfo(BaseModel):
    adapter_id: str
    device: str
    protocol: str
    tty: str
    connected: bool
    version: str


class ChannelValue(BaseModel):
    thing: str
    channel: str
    value: Value


class WriteRequest(BaseModel):
    value: Union[bool, int, float, str, list[TimerSlot]] = Field(
        description="Number (divider is applied automatically), true/false for booleans, "
        "or a list of up to 4 on/off pairs for timers",
        examples=[21, True, [{"on": "06:00", "off": "22:00"}]],
    )


class RawValue(BaseModel):
    address: str
    raw: str = Field(description="Bytes as received from the controller (hex)", examples=["d2 00"])
    type: DataType
    value: Value


def create_app(config: Config, device: Optolink) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        device.close()

    app = FastAPI(
        title="Optolink API",
        version=__version__,
        description=f"Viessmann heating controller **{config.device or config.protocol}** via Optolink "
        f"(protocol {config.protocol}, adapter {config.adapter_id or '-'}).",
        lifespan=lifespan,
    )

    @app.exception_handler(DeviceError)
    async def device_error(request: Request, exc: DeviceError) -> JSONResponse:
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    @app.exception_handler(OptolinkError)
    async def optolink_error(request: Request, exc: OptolinkError) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    def find_thing(thing_id: str) -> Thing:
        try:
            return config.things[thing_id]
        except KeyError:
            raise HTTPException(404, f"unknown thing '{thing_id}'") from None

    def find_channel(thing: Thing, channel_id: str) -> Channel:
        try:
            return thing.channels[channel_id]
        except KeyError:
            raise HTTPException(404, f"unknown channel '{channel_id}' in thing '{thing.id}'") from None

    def read_channel(channel: Channel) -> Value:
        telegram = channel.telegram
        return decode(telegram.type, device.read(telegram.address, telegram.length), telegram.divider)

    def read_channels(channels: Iterable[Channel]) -> dict[str, Value]:
        # a rejected address only invalidates its channel, a broken connection fails the request
        values: dict[str, Value] = {}
        for channel in channels:
            try:
                values[channel.id] = read_channel(channel)
            except DeviceError as exc:
                log.warning("%s: %s", channel.id, exc)
                values[channel.id] = None
        return values

    def visible(items: Iterable, include_hidden: bool) -> list:
        return [item for item in items if include_hidden or not item.hidden]

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse("/docs")

    @app.get("/api/info", response_model=AdapterInfo, tags=["Adapter"])
    def info() -> AdapterInfo:
        """Configuration of the adapter and state of the connection."""
        return AdapterInfo(
            adapter_id=config.adapter_id,
            device=config.device,
            protocol=config.protocol,
            tty=config.tty,
            connected=device.connected,
            version=__version__,
        )

    @app.get("/api/things", response_model=list[ThingInfo], tags=["Definitions"])
    def list_things(include_hidden: bool = False) -> list[ThingInfo]:
        """All things and their channels as defined in the configuration."""
        return [thing_info(t, include_hidden) for t in visible(config.things.values(), include_hidden)]

    @app.get("/api/things/{thing_id}", response_model=ThingInfo, tags=["Definitions"])
    def get_thing(thing_id: str, include_hidden: bool = False) -> ThingInfo:
        return thing_info(find_thing(thing_id), include_hidden)

    @app.get("/api/values", response_model=dict[str, dict[str, Value]], tags=["Values"])
    def read_all(include_hidden: bool = False) -> dict[str, dict[str, Value]]:
        """Read all channels of all things. Channels the controller rejects have the value null."""
        return {
            thing.id: read_channels(visible(thing.channels.values(), include_hidden))
            for thing in visible(config.things.values(), include_hidden)
        }

    @app.get("/api/things/{thing_id}/values", response_model=dict[str, Value], tags=["Values"])
    def read_thing(
        thing_id: str,
        channels: Optional[str] = Query(None, description="Comma separated channel ids, default: all"),
        include_hidden: bool = False,
    ) -> dict[str, Value]:
        """Read several channels of a thing. Channels the controller rejects have the value null."""
        thing = find_thing(thing_id)
        if channels:
            selected = [find_channel(thing, c.strip()) for c in channels.split(",") if c.strip()]
        else:
            selected = visible(thing.channels.values(), include_hidden)
        return read_channels(selected)

    @app.get("/api/things/{thing_id}/channels/{channel_id}", response_model=ChannelValue, tags=["Values"])
    def read_value(thing_id: str, channel_id: str) -> ChannelValue:
        thing = find_thing(thing_id)
        channel = find_channel(thing, channel_id)
        return ChannelValue(thing=thing.id, channel=channel.id, value=read_channel(channel))

    @app.put(
        "/api/things/{thing_id}/channels/{channel_id}",
        response_model=ChannelValue,
        tags=["Values"],
        responses={405: {"description": "Channel is read only"}, 422: {"description": "Invalid value"}},
    )
    def write_value(thing_id: str, channel_id: str, request: WriteRequest) -> ChannelValue:
        """Write a value to the controller. The answer contains the value read back afterwards."""
        thing = find_thing(thing_id)
        channel = find_channel(thing, channel_id)
        telegram = channel.telegram
        if not telegram.writable:
            raise HTTPException(405, f"channel '{thing.id}.{channel.id}' is read only")
        value = request.value
        if isinstance(value, list):
            value = [slot.model_dump() for slot in value]
        try:
            data = encode(telegram.type, value, telegram.divider)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        log.info("Write %s.%s = %s", thing.id, channel.id, value)
        device.write(telegram.address, data)
        return ChannelValue(thing=thing.id, channel=channel.id, value=read_channel(channel))

    @app.get("/api/raw/{address}", response_model=RawValue, tags=["Expert"])
    def read_raw(
        address: str = Path(pattern=r"^[0-9A-Fa-f]{1,4}$", examples=["0800"]),
        data_type: DataType = Query(DataType.USHORT, alias="type"),
        divider: float = Query(1, gt=0),
        length: Optional[int] = Query(None, ge=1, le=64, description="Number of bytes, default: length of type"),
    ) -> RawValue:
        """Read any address, e.g. to find addresses which are not in the configuration yet."""
        data = device.read(int(address, 16), length or data_type.length)
        value = decode(data_type, data, divider) if len(data) >= data_type.length else None
        return RawValue(address=address.upper().zfill(4), raw=data.hex(" "), type=data_type, value=value)

    return app


def thing_info(thing: Thing, include_hidden: bool) -> ThingInfo:
    return ThingInfo(
        id=thing.id,
        type=thing.type,
        description=thing.description,
        hidden=thing.hidden,
        channels=[
            ChannelInfo(
                id=c.id,
                description=c.description,
                address=f"{c.telegram.address:04X}",
                type=c.telegram.type,
                divider=c.telegram.divider,
                writable=c.telegram.writable,
                hidden=c.hidden,
            )
            for c in thing.channels.values()
            if include_hidden or not c.hidden
        ],
    )
