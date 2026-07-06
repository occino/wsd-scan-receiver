"""Runtime configuration for the WSD scan receiver."""

from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
import uuid
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

SCAN_CONFIG_FILE = Path("/data/config.json")
SCAN_DEFAULTS_FILE = Path(__file__).with_name("scan_defaults.json")


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


def _load_optional_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return _load_json_object(path)


def _config_section(config_file: Path, name: str) -> dict[str, Any]:
    data = _load_optional_json_object(config_file)
    section = data.get(name)
    if section is None:
        return {}
    if not isinstance(section, dict):
        raise ValueError(f"{config_file} field {name!r} must contain a JSON object")
    return section


def _write_config_section(config_file: Path, name: str, values: Mapping[str, Any]) -> None:
    data = _load_optional_json_object(config_file)
    if name == "scan" and "scan" not in data and any(key in SCAN_TICKET_FIELDS for key in data):
        data = {key: value for key, value in data.items() if key not in SCAN_TICKET_FIELDS}
    elif name != "scan" and "scan" not in data and any(key in SCAN_TICKET_FIELDS for key in data):
        scan_values = {key: data[key] for key in data if key in SCAN_TICKET_FIELDS}
        data = {key: value for key, value in data.items() if key not in SCAN_TICKET_FIELDS}
        data["scan"] = scan_values
    data[name] = dict(values)
    config_file.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(data, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile(
        "w",
        dir=config_file.parent,
        prefix=f".{config_file.name}.",
        suffix=".tmp",
        encoding="utf-8",
        delete=False,
    ) as handle:
        handle.write(serialized)
        tmp_path = Path(handle.name)
    tmp_path.replace(config_file)


def detect_host_ip(override: str | None = None) -> str:
    """Best-effort local address detection for XAddrs in discovery responses."""
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


@dataclass(frozen=True)
class ServiceSettings:
    """Configurable service parameters exposed in the admin UI."""

    wsd_device_name: str
    wsd_host: str
    wsd_interface: str
    wsd_scanner_ip: str
    keep_original: bool
    debug: bool
    log_level: str


@dataclass(frozen=True)
class PostProcessingSettings:
    """Configurable scan post-processing settings."""

    enabled: bool = True
    crop_mode: str = "DIN-A4"
    background_threshold: int = 220
    document_contrast: int = 35
    min_document_width_percent: int = 50
    min_document_height_percent: int = 50
    crop_side_padding: int = 20
    crop_bottom_padding: int = 20


@dataclass(frozen=True)
class UiSettings:
    """Configurable admin UI preferences."""

    show_fixed_scan_parameters: bool = False


SERVICE_SETTINGS_FIELDS = {
    "WSD_DEVICE_NAME",
    "WSD_HOST",
    "WSD_INTERFACE",
    "WSD_SCANNER_IP",
    "KEEP_ORIGINAL",
    "DEBUG",
    "LOG_LEVEL",
}
LOG_LEVELS = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"}
POST_PROCESSING_CROP_MODES = {"none", "auto", "DIN-A4"}
POST_PROCESSING_FIELDS = {
    "enabled",
    "crop_mode",
    "background_threshold",
    "document_contrast",
    "min_document_width_percent",
    "min_document_height_percent",
    "crop_side_padding",
    "crop_bottom_padding",
}
UI_SETTINGS_FIELDS = {"show_fixed_scan_parameters"}


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


SCAN_TICKET_TEXT_FIELDS = {
    "format",
    "input_source",
    "content_type",
    "color_processing",
}
SCAN_TICKET_INT_FIELDS = {
    "resolution",
    "compression_quality",
    "images_to_transfer",
    "width",
    "height",
    "region_x",
    "region_y",
    "region_width",
    "region_height",
    "brightness",
    "contrast",
    "sharpness",
    "rotation",
    "scaling_width",
    "scaling_height",
}
SCAN_TICKET_POSITIVE_INT_FIELDS = {
    "resolution",
    "images_to_transfer",
    "width",
    "height",
    "region_width",
    "region_height",
    "scaling_width",
    "scaling_height",
}
SCAN_TICKET_ALLOWED_VALUES = {
    "format": {"exif", "tiff-single-uncompressed"},
    "input_source": {"Auto", "Platen"},
    "content_type": {"Text", "Photo", "Mixed"},
    "color_processing": {"RGB24", "Grayscale8", "BlackAndWhite1"},
    "rotation": {0, 90, 180, 270},
}
SCAN_TICKET_FIELDS = SCAN_TICKET_TEXT_FIELDS | SCAN_TICKET_INT_FIELDS


def service_settings_to_dict(settings: ServiceSettings) -> dict[str, str | bool]:
    """Return service settings using public environment-style field names."""
    return {
        "WSD_DEVICE_NAME": settings.wsd_device_name,
        "WSD_HOST": settings.wsd_host,
        "WSD_INTERFACE": settings.wsd_interface,
        "WSD_SCANNER_IP": settings.wsd_scanner_ip,
        "KEEP_ORIGINAL": settings.keep_original,
        "DEBUG": settings.debug,
        "LOG_LEVEL": settings.log_level,
    }


def _service_settings_from_env() -> ServiceSettings:
    return ServiceSettings(
        wsd_device_name=os.getenv("WSD_DEVICE_NAME", "Paperless WSD Scanner").strip(),
        wsd_host=os.getenv("WSD_HOST", "").strip(),
        wsd_interface=os.getenv("WSD_INTERFACE", "").strip(),
        wsd_scanner_ip=(configured_scanner_ip() or "").strip(),
        keep_original=parse_bool(os.getenv("KEEP_ORIGINAL"), default=False),
        debug=parse_bool(os.getenv("DEBUG"), default=False),
        log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper(),
    )


def load_service_settings_defaults() -> ServiceSettings:
    """Return service defaults before persisted config overrides are applied."""
    return _service_settings_from_env()


def _coerce_service_bool(name: str, value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return parse_bool(value, default=False)
    raise ValueError(f"{name} must be a boolean")


def _coerce_range_int(name: str, value: Any, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = int(value.strip())
        except ValueError as exc:
            raise ValueError(f"{name} must be an integer") from exc
    else:
        raise ValueError(f"{name} must be an integer")
    if not minimum <= parsed <= maximum:
        raise ValueError(f"{name} must be from {minimum} to {maximum}")
    return parsed


def service_settings_from_mapping(
    values: Mapping[str, Any],
    *,
    base: ServiceSettings | None = None,
) -> ServiceSettings:
    """Build service settings from JSON/form values, optionally merging over a base."""
    merged: dict[str, Any] = service_settings_to_dict(base) if base else {}
    merged.update(values)

    missing = sorted(SERVICE_SETTINGS_FIELDS - merged.keys())
    if missing:
        raise ValueError(f"missing service config fields: {', '.join(missing)}")

    device_name = str(merged["WSD_DEVICE_NAME"]).strip()
    if not device_name:
        raise ValueError("WSD_DEVICE_NAME must not be empty")

    log_level = str(merged["LOG_LEVEL"]).strip().upper()
    if log_level not in LOG_LEVELS:
        allowed = ", ".join(sorted(LOG_LEVELS))
        raise ValueError(f"LOG_LEVEL must be one of: {allowed}")

    return ServiceSettings(
        wsd_device_name=device_name,
        wsd_host=str(merged["WSD_HOST"]).strip(),
        wsd_interface=str(merged["WSD_INTERFACE"]).strip(),
        wsd_scanner_ip=str(merged["WSD_SCANNER_IP"]).strip(),
        keep_original=_coerce_service_bool("KEEP_ORIGINAL", merged["KEEP_ORIGINAL"]),
        debug=_coerce_service_bool("DEBUG", merged["DEBUG"]),
        log_level=log_level,
    )


def load_service_settings(config_file: Path = SCAN_CONFIG_FILE) -> ServiceSettings:
    """Load service settings from environment and persisted overrides."""
    return service_settings_from_mapping(
        _config_section(config_file, "service"),
        base=_service_settings_from_env(),
    )


def save_service_settings(config_file: Path, settings: ServiceSettings) -> None:
    """Persist service settings as the service section of config.json."""
    _write_config_section(config_file, "service", service_settings_to_dict(settings))


class ServiceSettingsStore:
    """Thread-safe service settings store backed by config.json."""

    def __init__(self, config_file: Path = SCAN_CONFIG_FILE) -> None:
        self.config_file = config_file
        self._lock = threading.Lock()
        self._settings = load_service_settings(config_file)

    def get(self) -> ServiceSettings:
        with self._lock:
            return self._settings

    def as_dict(self) -> dict[str, str | bool]:
        return service_settings_to_dict(self.get())

    def update(self, values: Mapping[str, Any]) -> ServiceSettings:
        with self._lock:
            settings = service_settings_from_mapping(values, base=self._settings)
            save_service_settings(self.config_file, settings)
            self._settings = settings
            return settings


def post_processing_settings_to_dict(
    settings: PostProcessingSettings,
) -> dict[str, bool | int | str]:
    """Return post-processing settings using JSON field names."""
    return {
        "enabled": settings.enabled,
        "crop_mode": settings.crop_mode,
        "background_threshold": settings.background_threshold,
        "document_contrast": settings.document_contrast,
        "min_document_width_percent": settings.min_document_width_percent,
        "min_document_height_percent": settings.min_document_height_percent,
        "crop_side_padding": settings.crop_side_padding,
        "crop_bottom_padding": settings.crop_bottom_padding,
    }


def post_processing_settings_from_mapping(
    values: Mapping[str, Any],
    *,
    base: PostProcessingSettings | None = None,
) -> PostProcessingSettings:
    """Build post-processing settings from JSON/form values."""
    merged: dict[str, Any] = post_processing_settings_to_dict(
        base or PostProcessingSettings()
    )
    merged.update(values)
    if "crop_padding" in values:
        if "crop_side_padding" not in values:
            merged["crop_side_padding"] = values["crop_padding"]
        if "crop_bottom_padding" not in values:
            merged["crop_bottom_padding"] = values["crop_padding"]
    if (
        "enabled" in values
        and "crop_mode" not in values
        and not _coerce_service_bool("post_processing.enabled", values["enabled"])
    ):
        merged["crop_mode"] = "none"
        merged["enabled"] = True
    missing = sorted(POST_PROCESSING_FIELDS - merged.keys())
    if missing:
        raise ValueError(f"missing post-processing config fields: {', '.join(missing)}")
    crop_mode = str(merged["crop_mode"]).strip()
    if crop_mode not in POST_PROCESSING_CROP_MODES:
        allowed = ", ".join(sorted(POST_PROCESSING_CROP_MODES))
        raise ValueError(f"post_processing.crop_mode must be one of: {allowed}")
    return PostProcessingSettings(
        enabled=True,
        crop_mode=crop_mode,
        background_threshold=_coerce_range_int(
            "post_processing.background_threshold",
            merged["background_threshold"],
            minimum=0,
            maximum=255,
        ),
        document_contrast=_coerce_range_int(
            "post_processing.document_contrast",
            merged["document_contrast"],
            minimum=0,
            maximum=255,
        ),
        min_document_width_percent=_coerce_range_int(
            "post_processing.min_document_width_percent",
            merged["min_document_width_percent"],
            minimum=1,
            maximum=100,
        ),
        min_document_height_percent=_coerce_range_int(
            "post_processing.min_document_height_percent",
            merged["min_document_height_percent"],
            minimum=1,
            maximum=100,
        ),
        crop_side_padding=_coerce_range_int(
            "post_processing.crop_side_padding",
            merged["crop_side_padding"],
            minimum=0,
            maximum=500,
        ),
        crop_bottom_padding=_coerce_range_int(
            "post_processing.crop_bottom_padding",
            merged["crop_bottom_padding"],
            minimum=0,
            maximum=500,
        ),
    )


def load_post_processing_settings(config_file: Path = SCAN_CONFIG_FILE) -> PostProcessingSettings:
    """Load post-processing settings from persisted config."""
    return post_processing_settings_from_mapping(
        _config_section(config_file, "post_processing"),
        base=PostProcessingSettings(),
    )


def save_post_processing_settings(
    config_file: Path,
    settings: PostProcessingSettings,
) -> None:
    """Persist post-processing settings as the post_processing section."""
    _write_config_section(
        config_file,
        "post_processing",
        post_processing_settings_to_dict(settings),
    )


class PostProcessingSettingsStore:
    """Thread-safe post-processing settings store backed by config.json."""

    def __init__(self, config_file: Path = SCAN_CONFIG_FILE) -> None:
        self.config_file = config_file
        self._lock = threading.Lock()
        self._settings = load_post_processing_settings(config_file)

    def get(self) -> PostProcessingSettings:
        with self._lock:
            return self._settings

    def as_dict(self) -> dict[str, bool | int | str]:
        return post_processing_settings_to_dict(self.get())

    def update(self, values: Mapping[str, Any]) -> PostProcessingSettings:
        with self._lock:
            settings = post_processing_settings_from_mapping(values, base=self._settings)
            save_post_processing_settings(self.config_file, settings)
            self._settings = settings
            return settings


def ui_settings_to_dict(settings: UiSettings) -> dict[str, bool]:
    """Return admin UI settings using JSON field names."""
    return {"show_fixed_scan_parameters": settings.show_fixed_scan_parameters}


def ui_settings_from_mapping(
    values: Mapping[str, Any],
    *,
    base: UiSettings | None = None,
) -> UiSettings:
    """Build admin UI settings from JSON/form values."""
    merged: dict[str, Any] = ui_settings_to_dict(base or UiSettings())
    merged.update(values)
    missing = sorted(UI_SETTINGS_FIELDS - merged.keys())
    if missing:
        raise ValueError(f"missing UI config fields: {', '.join(missing)}")
    return UiSettings(
        show_fixed_scan_parameters=_coerce_service_bool(
            "ui.show_fixed_scan_parameters",
            merged["show_fixed_scan_parameters"],
        )
    )


def load_ui_settings(config_file: Path = SCAN_CONFIG_FILE) -> UiSettings:
    """Load admin UI settings from persisted config."""
    return ui_settings_from_mapping(_config_section(config_file, "ui"), base=UiSettings())


def save_ui_settings(config_file: Path, settings: UiSettings) -> None:
    """Persist admin UI settings as the ui section."""
    _write_config_section(config_file, "ui", ui_settings_to_dict(settings))


class UiSettingsStore:
    """Thread-safe admin UI settings store backed by config.json."""

    def __init__(self, config_file: Path = SCAN_CONFIG_FILE) -> None:
        self.config_file = config_file
        self._lock = threading.Lock()
        self._settings = load_ui_settings(config_file)

    def get(self) -> UiSettings:
        with self._lock:
            return self._settings

    def as_dict(self) -> dict[str, bool]:
        return ui_settings_to_dict(self.get())

    def update(self, values: Mapping[str, Any]) -> UiSettings:
        with self._lock:
            settings = ui_settings_from_mapping(values, base=self._settings)
            save_ui_settings(self.config_file, settings)
            self._settings = settings
            return settings


def scan_ticket_to_dict(scan_ticket: ScanTicketConfig) -> dict[str, str | int]:
    """Return a JSON-serializable scan ticket mapping."""
    return asdict(scan_ticket)


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} must contain a JSON object") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def load_scan_ticket_defaults(defaults_file: Path = SCAN_DEFAULTS_FILE) -> ScanTicketConfig:
    """Load the packaged default WS-Scan ticket."""
    return scan_ticket_from_mapping(_load_json_object(defaults_file))


def _coerce_text(name: str, value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{name} must not be empty")
    allowed = SCAN_TICKET_ALLOWED_VALUES.get(name)
    if allowed is not None and stripped not in allowed:
        allowed_text = ", ".join(str(item) for item in sorted(allowed))
        raise ValueError(f"{name} must be one of: {allowed_text}")
    return stripped


def _coerce_int(name: str, value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = int(value.strip())
        except ValueError as exc:
            raise ValueError(f"{name} must be an integer") from exc
    else:
        raise ValueError(f"{name} must be an integer")
    if name in SCAN_TICKET_POSITIVE_INT_FIELDS and parsed <= 0:
        raise ValueError(f"{name} must be greater than zero")
    allowed = SCAN_TICKET_ALLOWED_VALUES.get(name)
    if allowed is not None and parsed not in allowed:
        allowed_text = ", ".join(str(item) for item in sorted(allowed))
        raise ValueError(f"{name} must be one of: {allowed_text}")
    if name == "compression_quality" and not 1 <= parsed <= 100:
        raise ValueError("compression_quality must be from 1 to 100")
    return parsed


def scan_ticket_from_mapping(
    values: Mapping[str, Any],
    *,
    base: ScanTicketConfig | None = None,
) -> ScanTicketConfig:
    """Build a scan ticket from JSON/form values, optionally merging over a base."""
    merged: dict[str, Any] = scan_ticket_to_dict(base) if base is not None else {}
    merged.update(values)

    required = SCAN_TICKET_TEXT_FIELDS | SCAN_TICKET_INT_FIELDS
    missing = sorted(required - merged.keys())
    if missing:
        raise ValueError(f"missing scan config fields: {', '.join(missing)}")

    normalized: dict[str, str | int] = {}
    for name in sorted(SCAN_TICKET_TEXT_FIELDS):
        normalized[name] = _coerce_text(name, merged[name])
    for name in sorted(SCAN_TICKET_INT_FIELDS):
        normalized[name] = _coerce_int(name, merged[name])

    return ScanTicketConfig(
        format=str(normalized["format"]),
        input_source=str(normalized["input_source"]),
        content_type=str(normalized["content_type"]),
        color_processing=str(normalized["color_processing"]),
        resolution=int(normalized["resolution"]),
        compression_quality=int(normalized["compression_quality"]),
        images_to_transfer=int(normalized["images_to_transfer"]),
        width=int(normalized["width"]),
        height=int(normalized["height"]),
        region_x=int(normalized["region_x"]),
        region_y=int(normalized["region_y"]),
        region_width=int(normalized["region_width"]),
        region_height=int(normalized["region_height"]),
        brightness=int(normalized["brightness"]),
        contrast=int(normalized["contrast"]),
        sharpness=int(normalized["sharpness"]),
        rotation=int(normalized["rotation"]),
        scaling_width=int(normalized["scaling_width"]),
        scaling_height=int(normalized["scaling_height"]),
    )


def load_scan_ticket_config(
    config_file: Path = SCAN_CONFIG_FILE,
    defaults_file: Path = SCAN_DEFAULTS_FILE,
) -> ScanTicketConfig:
    """Load defaults and merge persisted scan ticket overrides if present."""
    defaults = load_scan_ticket_defaults(defaults_file)
    if not config_file.exists():
        return defaults
    data = _load_json_object(config_file)
    scan_data = data.get("scan")
    if isinstance(scan_data, dict):
        return scan_ticket_from_mapping(scan_data, base=defaults)
    if scan_data is not None:
        raise ValueError(f"{config_file} field 'scan' must contain a JSON object")
    return scan_ticket_from_mapping(data, base=defaults)


def save_scan_ticket_config(config_file: Path, scan_ticket: ScanTicketConfig) -> None:
    """Atomically persist a scan ticket as JSON."""
    _write_config_section(config_file, "scan", scan_ticket_to_dict(scan_ticket))


class ScanTicketStore:
    """Thread-safe scan ticket store backed by a JSON file."""

    def __init__(
        self,
        config_file: Path = SCAN_CONFIG_FILE,
        defaults_file: Path = SCAN_DEFAULTS_FILE,
    ) -> None:
        self.config_file = config_file
        self.defaults_file = defaults_file
        self._lock = threading.Lock()
        self._scan_ticket = load_scan_ticket_config(config_file, defaults_file)

    def get(self) -> ScanTicketConfig:
        with self._lock:
            return self._scan_ticket

    def as_dict(self) -> dict[str, str | int]:
        return scan_ticket_to_dict(self.get())

    def update(self, values: Mapping[str, Any]) -> ScanTicketConfig:
        with self._lock:
            scan_ticket = scan_ticket_from_mapping(values, base=self._scan_ticket)
            save_scan_ticket_config(self.config_file, scan_ticket)
            self._scan_ticket = scan_ticket
            return scan_ticket


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
    admin_port: int = 8888
    original_dir: Path = Path("/original")
    scan_ticket_store: ScanTicketStore | None = None
    service_settings_store: ServiceSettingsStore | None = None
    post_processing_store: PostProcessingSettingsStore | None = None
    ui_settings_store: UiSettingsStore | None = None

    @property
    def metadata_url(self) -> str:
        return f"http://{self.host_ip}:{self.http_port}/metadata"

    @property
    def scanner_url(self) -> str:
        return f"http://{self.host_ip}:{self.http_port}/scanner"

    @classmethod
    def from_env(cls) -> Config:
        uuid_file = Path(os.getenv("WSD_UUID_FILE", "/data/wsd-uuid"))
        service_settings_store = ServiceSettingsStore(SCAN_CONFIG_FILE)
        service_settings = service_settings_store.get()
        post_processing_store = PostProcessingSettingsStore(SCAN_CONFIG_FILE)
        ui_settings_store = UiSettingsStore(SCAN_CONFIG_FILE)
        scan_ticket_store = ScanTicketStore(SCAN_CONFIG_FILE, SCAN_DEFAULTS_FILE)
        device_name = service_settings.wsd_device_name
        if not device_name:
            raise ValueError("WSD_DEVICE_NAME must not be empty")
        return cls(
            device_name=device_name,
            endpoint_uuid=load_or_create_uuid(uuid_file),
            http_port=_env_port("WSD_HTTP_PORT", 5357),
            output_dir=Path(os.getenv("OUTPUT_DIR", "/scans")),
            original_dir=Path(os.getenv("ORIGINAL_DIR", "/original")),
            debug=service_settings.debug,
            raw_dump_dir=Path(os.getenv("RAW_DUMP_DIR", "/debug-dumps")),
            log_level=service_settings.log_level,
            host_ip=detect_host_ip(service_settings.wsd_host),
            interface=service_settings.wsd_interface or None,
            scanner_ip=service_settings.wsd_scanner_ip or None,
            wsd_subscribe_enabled=parse_bool(
                os.getenv("WSD_SUBSCRIBE_ENABLED"),
                default=False,
            ),
            wsd_subscribe_interval_seconds=_env_positive_int(
                "WSD_SUBSCRIBE_INTERVAL_SECONDS",
                60,
            ),
            max_post_bytes=_env_positive_int("MAX_POST_BYTES", 100 * 1024 * 1024),
            scan_ticket=scan_ticket_store.get(),
            uuid_file=uuid_file,
            admin_port=_env_port("WSD_ADMIN_PORT", 8888),
            scan_ticket_store=scan_ticket_store,
            service_settings_store=service_settings_store,
            post_processing_store=post_processing_store,
            ui_settings_store=ui_settings_store,
        )
