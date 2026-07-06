"""Runtime configuration for the WSD scan receiver."""

from __future__ import annotations

import os
import socket
import uuid
from dataclasses import dataclass
from pathlib import Path


def parse_bool(value: str | None, *, default: bool = False) -> bool:
    """Parse common environment-style boolean values."""
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


def _env_positive_int(name: str, default: int) -> int:
    value = _env_int(name, default)
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero, got {value!r}")
    return value


def _env_port(name: str, default: int) -> int:
    value = _env_int(name, default)
    if not 1 <= value <= 65535:
        raise ValueError(f"{name} must be a TCP port from 1 to 65535, got {value!r}")
    return value


def _env_text(name: str, default: str) -> str:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def detect_host_ip() -> str:
    """Best-effort local address detection for XAddrs in discovery responses."""
    override = os.getenv("WSD_HOST")
    if override:
        return override

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return socket.gethostbyname(socket.gethostname())
    finally:
        sock.close()


def configured_scanner_ip() -> str | None:
    """Return an optional scanner IP used for directed WSD probing."""
    return os.getenv("WSD_SCANNER_IP") or os.getenv("EPSON_PRINTER_IP") or None


def normalize_endpoint_uuid(value: str) -> str:
    """Return a DPWS endpoint identifier in the common urn:uuid form."""
    stripped = value.strip()
    if stripped.startswith("urn:uuid:"):
        return stripped
    if stripped.startswith("uuid:"):
        return f"urn:{stripped}"
    return f"urn:uuid:{stripped}"


def load_or_create_uuid(uuid_file: Path) -> str:
    """Return the configured UUID or persist a generated UUID if possible."""
    explicit = os.getenv("WSD_UUID")
    if explicit:
        return normalize_endpoint_uuid(explicit)

    try:
        if uuid_file.exists():
            value = uuid_file.read_text(encoding="utf-8").strip()
            if value:
                normalized = normalize_endpoint_uuid(value)
                if normalized != value:
                    uuid_file.write_text(normalized + "\n", encoding="utf-8")
                return normalized
        uuid_file.parent.mkdir(parents=True, exist_ok=True)
        value = f"urn:uuid:{uuid.uuid4()}"
        uuid_file.write_text(value + "\n", encoding="utf-8")
        return value
    except OSError:
        return f"urn:uuid:{uuid.uuid4()}"


@dataclass(frozen=True)
class ScanTicketConfig:
    """Configurable WS-Scan ticket values sent with CreateScanJob."""

    format: str
    input_source: str
    content_type: str
    color_processing: str
    resolution: int
    compression_quality: int
    images_to_transfer: int
    width: int
    height: int
    region_x: int
    region_y: int
    region_width: int
    region_height: int
    brightness: int
    contrast: int
    sharpness: int
    rotation: int
    scaling_width: int
    scaling_height: int


@dataclass(frozen=True)
class Config:
    """Application configuration derived from environment variables."""

    device_name: str
    endpoint_uuid: str
    http_port: int
    output_dir: Path
    debug: bool
    raw_dump_dir: Path
    log_level: str
    host_ip: str
    interface: str | None
    scanner_ip: str | None
    wsd_subscribe_enabled: bool
    wsd_subscribe_interval_seconds: int
    max_post_bytes: int
    scan_ticket: ScanTicketConfig
    uuid_file: Path

    @property
    def metadata_url(self) -> str:
        return f"http://{self.host_ip}:{self.http_port}/metadata"

    @property
    def scanner_url(self) -> str:
        return f"http://{self.host_ip}:{self.http_port}/scanner"

    @classmethod
    def from_env(cls) -> Config:
        uuid_file = Path(os.getenv("WSD_UUID_FILE", "/data/wsd-uuid"))
        device_name = os.getenv("WSD_DEVICE_NAME", "Paperless WSD Scanner").strip()
        if not device_name:
            raise ValueError("WSD_DEVICE_NAME must not be empty")
        return cls(
            device_name=device_name,
            endpoint_uuid=load_or_create_uuid(uuid_file),
            http_port=_env_port("WSD_HTTP_PORT", 5357),
            output_dir=Path(os.getenv("OUTPUT_DIR", "/consume")),
            debug=parse_bool(os.getenv("DEBUG"), default=False),
            raw_dump_dir=Path(os.getenv("RAW_DUMP_DIR", "/debug-dumps")),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            host_ip=detect_host_ip(),
            interface=os.getenv("WSD_INTERFACE") or None,
            scanner_ip=configured_scanner_ip(),
            wsd_subscribe_enabled=parse_bool(
                os.getenv("WSD_SUBSCRIBE_ENABLED"),
                default=False,
            ),
            wsd_subscribe_interval_seconds=_env_positive_int(
                "WSD_SUBSCRIBE_INTERVAL_SECONDS",
                60,
            ),
            max_post_bytes=_env_positive_int("MAX_POST_BYTES", 100 * 1024 * 1024),
            scan_ticket=ScanTicketConfig(
                format=_env_text("SCAN_FORMAT", "exif"),
                input_source=_env_text("SCAN_INPUT_SOURCE", "Auto"),
                content_type=_env_text("SCAN_CONTENT_TYPE", "Text"),
                color_processing=_env_text("SCAN_COLOR_PROCESSING", "RGB24"),
                resolution=_env_positive_int("SCAN_RESOLUTION", 100),
                compression_quality=_env_int("SCAN_COMPRESSION_QUALITY", 50),
                images_to_transfer=_env_positive_int("SCAN_IMAGES_TO_TRANSFER", 1),
                width=_env_positive_int("SCAN_WIDTH", 8500),
                height=_env_positive_int("SCAN_HEIGHT", 11700),
                region_x=_env_int("SCAN_REGION_X", 0),
                region_y=_env_int("SCAN_REGION_Y", 0),
                region_width=_env_positive_int("SCAN_REGION_WIDTH", 8500),
                region_height=_env_positive_int("SCAN_REGION_HEIGHT", 11700),
                brightness=_env_int("SCAN_BRIGHTNESS", 0),
                contrast=_env_int("SCAN_CONTRAST", 0),
                sharpness=_env_int("SCAN_SHARPNESS", 0),
                rotation=_env_int("SCAN_ROTATION", 0),
                scaling_width=_env_positive_int("SCAN_SCALING_WIDTH", 100),
                scaling_height=_env_positive_int("SCAN_SCALING_HEIGHT", 100),
            ),
            uuid_file=uuid_file,
        )
