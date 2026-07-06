"""Entrypoint for the WSD scan receiver service."""

from __future__ import annotations

import logging
import signal
import time

from .admin import AdminService
from .config import Config
from .discovery import DiscoveryService
from .logging_config import configure_logging
from .receiver import ReceiverService
from .ws_scan_client import WsScanClientService

LOGGER = logging.getLogger(__name__)


def run() -> None:
    config = Config.from_env()
    configure_logging(config.log_level)
    config.output_dir.mkdir(parents=True, exist_ok=True)
    if config.debug:
        config.raw_dump_dir.mkdir(parents=True, exist_ok=True)

    LOGGER.info(
        "starting WSD scan receiver",
        extra={
            "device_name": config.device_name,
            "endpoint_uuid": config.endpoint_uuid,
            "http_port": config.http_port,
            "output_dir": str(config.output_dir),
            "debug": config.debug,
            "raw_dump_dir": str(config.raw_dump_dir),
            "host_ip": config.host_ip,
            "metadata_url": config.metadata_url,
            "max_post_bytes": config.max_post_bytes,
        },
    )
    LOGGER.warning(
        "WS-Scan push support is experimental and may require scanner-specific packet captures"
    )

    scan_ticket_store = config.scan_ticket_store
    service_settings_store = config.service_settings_store
    post_processing_store = config.post_processing_store
    ui_settings_store = config.ui_settings_store
    if (
        scan_ticket_store is None
        or service_settings_store is None
        or post_processing_store is None
        or ui_settings_store is None
    ):
        raise RuntimeError("Config.from_env must provide admin config stores")
    ws_scan_client = WsScanClientService(config)
    discovery = DiscoveryService(config, ws_scan_client.observe_discovery_payload)
    receiver = ReceiverService(config, ws_scan_client.handle_scan_available_event)
    admin = AdminService(
        service_settings_store,
        post_processing_store,
        scan_ticket_store,
        ui_settings_store,
    )
    stop = False

    def request_stop(signum: int, _frame: object) -> None:
        nonlocal stop
        LOGGER.info("received shutdown signal", extra={"signal": signum})
        stop = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    receiver.start()
    admin.start()
    discovery.start()
    ws_scan_client.start()
    try:
        while not stop:
            time.sleep(0.5)
    finally:
        ws_scan_client.stop()
        discovery.stop()
        admin.stop()
        receiver.stop()
        LOGGER.info("WSD scan receiver stopped")


def main() -> None:
    run()


if __name__ == "__main__":
    main()
