"""Command line entry point: python -m optolink --config optolink.xml"""

from __future__ import annotations

import argparse
import logging

import uvicorn

from .api import create_app
from .config import ConfigError, load_config
from .device import Optolink
from .simulator import create_simulator


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="optolink-api",
        description="REST API with Swagger UI for Viessmann heating controllers via Optolink",
    )
    parser.add_argument("-c", "--config", default="optolink.xml", help="XML configuration (default: %(default)s)")
    parser.add_argument("--host", default="0.0.0.0", help="address to listen on (default: %(default)s)")
    parser.add_argument("--port", type=int, default=8000, help="HTTP port (default: %(default)s)")
    parser.add_argument("--tty", help="overrides <tty> of the configuration, e.g. /dev/ttyUSB0, COM3 or 192.168.1.10:10001")
    parser.add_argument("--simulate", action="store_true", help="use a simulated controller instead of the adapter")
    parser.add_argument("--log-level", default="info", choices=["debug", "info", "warning", "error"])
    args = parser.parse_args(argv)

    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    try:
        config = load_config(args.config)
    except (OSError, ConfigError) as exc:
        parser.error(f"cannot load configuration: {exc}")
    if args.tty:
        config.tty = args.tty

    if args.simulate:
        simulator = create_simulator(config)
        config.tty = "simulator"
        device = Optolink(config.tty, config.protocol, config.timeout, port_factory=lambda url, timeout: simulator)
        logging.getLogger(__name__).warning("Simulation mode: no real heating controller is used")
    else:
        device = Optolink(config.url, config.protocol, config.timeout)
    uvicorn.run(create_app(config, device), host=args.host, port=args.port, log_level=args.log_level)


if __name__ == "__main__":
    main()
