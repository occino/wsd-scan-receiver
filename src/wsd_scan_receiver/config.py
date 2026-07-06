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
    epson_printer_ip: str | None
    epsonscan2_enabled: bool
    epsonscan2_helper: str
    epsonscan2_lib_path: str | None
    epsonscan2_lib_dir: str | None
    epsonscan2_keepalive: bool
    epsonscan2_refresh_seconds: int
    epson_debug_enabled: bool
    wsd_subscribe_enabled: bool
    wsd_subscribe_interval_seconds: int
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
        return cls(
            device_name=os.getenv("WSD_DEVICE_NAME", "Paperless WSD Scanner"),
            endpoint_uuid=load_or_create_uuid(uuid_file),
            http_port=_env_int("WSD_HTTP_PORT", 5357),
            output_dir=Path(os.getenv("OUTPUT_DIR", "/consume")),
            debug=parse_bool(os.getenv("DEBUG"), default=False),
            raw_dump_dir=Path(os.getenv("RAW_DUMP_DIR", "/debug-dumps")),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            host_ip=detect_host_ip(),
            interface=os.getenv("WSD_INTERFACE") or None,
            epson_printer_ip=os.getenv("EPSON_PRINTER_IP") or None,
            epsonscan2_enabled=parse_bool(os.getenv("EPSONSCAN2_ENABLED"), default=False),
            epsonscan2_helper=os.getenv(
                "EPSONSCAN2_HELPER",
                "/usr/local/bin/epsonscan2-push-ready",
            ),
            epsonscan2_lib_path=os.getenv("EPSONSCAN2_LIB_PATH") or None,
            epsonscan2_lib_dir=os.getenv("EPSONSCAN2_LIB_DIR") or None,
            epsonscan2_keepalive=parse_bool(os.getenv("EPSONSCAN2_KEEPALIVE"), default=True),
            epsonscan2_refresh_seconds=_env_int("EPSONSCAN2_REFRESH_SECONDS", 0),
            epson_debug_enabled=parse_bool(os.getenv("EPSON_DEBUG_ENABLED"), default=False),
            wsd_subscribe_enabled=parse_bool(
                os.getenv("WSD_SUBSCRIBE_ENABLED"),
                default=False,
            ),
            wsd_subscribe_interval_seconds=_env_int("WSD_SUBSCRIBE_INTERVAL_SECONDS", 60),
            uuid_file=uuid_file,
        )
