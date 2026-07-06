import json
from pathlib import Path

import pytest

import wsd_scan_receiver.config as config_module
from wsd_scan_receiver.config import (
    Config,
    PostProcessingSettingsStore,
    ScanTicketStore,
    ServiceSettingsStore,
    load_or_create_uuid,
    load_post_processing_settings,
    load_scan_ticket_config,
    load_service_settings,
    normalize_endpoint_uuid,
    parse_bool,
    post_processing_settings_to_dict,
    scan_ticket_to_dict,
    service_settings_to_dict,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("true", True),
        ("1", True),
        ("yes", True),
        ("on", True),
        ("false", False),
        ("0", False),
        ("", False),
        (None, False),
    ],
)
def test_parse_bool(value: str | None, expected: bool) -> None:
    assert parse_bool(value) is expected


def test_load_or_create_uuid_persists(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("WSD_UUID", raising=False)
    uuid_file = tmp_path / "wsd-uuid"

    first = load_or_create_uuid(uuid_file)
    second = load_or_create_uuid(uuid_file)

    assert first.startswith("urn:uuid:")
    assert first == second


def test_load_or_create_uuid_uses_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("WSD_UUID", "1234")

    assert load_or_create_uuid(tmp_path / "uuid") == "urn:uuid:1234"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1234", "urn:uuid:1234"),
        ("uuid:1234", "urn:uuid:1234"),
        ("urn:uuid:1234", "urn:uuid:1234"),
    ],
)
def test_normalize_endpoint_uuid(value: str, expected: str) -> None:
    assert normalize_endpoint_uuid(value) == expected


def test_load_or_create_uuid_migrates_legacy_uuid_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("WSD_UUID", raising=False)
    uuid_file = tmp_path / "wsd-uuid"
    uuid_file.write_text("uuid:legacy\n", encoding="utf-8")

    assert load_or_create_uuid(uuid_file) == "urn:uuid:legacy"
    assert uuid_file.read_text(encoding="utf-8") == "urn:uuid:legacy\n"


def test_config_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("WSD_HOST", "192.0.2.10")
    monkeypatch.setenv("WSD_UUID_FILE", str(tmp_path / "uuid"))
    monkeypatch.setattr(config_module, "SCAN_CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setenv("WSD_DEVICE_NAME", "Office Scanner")
    monkeypatch.setenv("WSD_HTTP_PORT", "9999")
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "scans"))
    monkeypatch.setenv("RAW_DUMP_DIR", str(tmp_path / "dumps"))
    monkeypatch.setenv("DEBUG", "true")
    monkeypatch.setenv("WSD_SCANNER_IP", "192.0.2.21")
    monkeypatch.setenv("MAX_POST_BYTES", "1024")
    monkeypatch.setenv("SCAN_FORMAT", "tiff-single-uncompressed")
    monkeypatch.setenv("SCAN_RESOLUTION", "300")

    config = Config.from_env()

    assert config.device_name == "Office Scanner"
    assert config.http_port == 9999
    assert config.debug is True
    assert config.host_ip == "192.0.2.10"
    assert config.metadata_url == "http://192.0.2.10:9999/metadata"
    assert config.scanner_ip == "192.0.2.21"
    assert config.max_post_bytes == 1024
    assert config.scan_ticket.format == "exif"
    assert config.scan_ticket.resolution == 100
    assert config.scan_ticket_store is not None
    assert config.wsd_subscribe_enabled is False
    assert config.wsd_subscribe_interval_seconds == 60


def test_config_from_env_loads_scan_ticket_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps({"format": "tiff-single-uncompressed", "resolution": 300}),
        encoding="utf-8",
    )
    monkeypatch.setenv("WSD_HOST", "192.0.2.10")
    monkeypatch.setenv("WSD_UUID_FILE", str(tmp_path / "uuid"))
    monkeypatch.setattr(config_module, "SCAN_CONFIG_FILE", config_file)

    config = Config.from_env()

    assert config.scan_ticket.format == "tiff-single-uncompressed"
    assert config.scan_ticket.resolution == 300


def test_config_from_env_loads_service_settings_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps(
            {
                "service": {
                    "WSD_DEVICE_NAME": "Stored Scanner",
                    "WSD_HOST": "192.0.2.30",
                    "WSD_INTERFACE": "eth0",
                    "WSD_SCANNER_IP": "192.0.2.31",
                    "DEBUG": True,
                    "LOG_LEVEL": "debug",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("WSD_HOST", "192.0.2.10")
    monkeypatch.setenv("WSD_UUID_FILE", str(tmp_path / "uuid"))
    monkeypatch.setenv("WSD_DEVICE_NAME", "Env Scanner")
    monkeypatch.setattr(config_module, "SCAN_CONFIG_FILE", config_file)

    config = Config.from_env()

    assert config.device_name == "Stored Scanner"
    assert config.host_ip == "192.0.2.30"
    assert config.interface == "eth0"
    assert config.scanner_ip == "192.0.2.31"
    assert config.debug is True
    assert config.log_level == "DEBUG"


def test_load_scan_ticket_config_merges_over_defaults(tmp_path: Path) -> None:
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"resolution": 300}), encoding="utf-8")

    scan_ticket = load_scan_ticket_config(config_file)

    assert scan_ticket.format == "exif"
    assert scan_ticket.resolution == 300


def test_load_scan_ticket_config_accepts_sectioned_config(tmp_path: Path) -> None:
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"scan": {"resolution": 300}}), encoding="utf-8")

    scan_ticket = load_scan_ticket_config(config_file)

    assert scan_ticket.format == "exif"
    assert scan_ticket.resolution == 300


def test_load_service_settings_merges_over_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps({"service": {"WSD_DEVICE_NAME": "Stored Scanner", "DEBUG": True}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("WSD_DEVICE_NAME", "Env Scanner")
    monkeypatch.setenv("LOG_LEVEL", "warning")

    settings = load_service_settings(config_file)

    assert settings.wsd_device_name == "Stored Scanner"
    assert settings.debug is True
    assert settings.log_level == "WARNING"


def test_load_post_processing_settings_defaults_enabled(tmp_path: Path) -> None:
    settings = load_post_processing_settings(tmp_path / "config.json")

    assert settings.enabled is True
    assert settings.crop_mode == "auto"


def test_load_post_processing_settings_migrates_disabled_config_to_none(tmp_path: Path) -> None:
    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps({"post_processing": {"enabled": False}}),
        encoding="utf-8",
    )

    settings = load_post_processing_settings(config_file)

    assert settings.enabled is True
    assert settings.crop_mode == "none"
    assert settings.background_threshold == 220


def test_load_post_processing_settings_reads_parameters(tmp_path: Path) -> None:
    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps(
            {
                "post_processing": {
                    "enabled": True,
                    "crop_mode": "DIN-A4",
                    "background_threshold": 210,
                    "document_contrast": 20,
                    "min_document_width_percent": 70,
                    "min_document_height_percent": 80,
                    "crop_side_padding": 12,
                    "crop_bottom_padding": 18,
                }
            }
        ),
        encoding="utf-8",
    )

    settings = load_post_processing_settings(config_file)

    assert settings.crop_mode == "DIN-A4"
    assert settings.background_threshold == 210
    assert settings.document_contrast == 20
    assert settings.min_document_width_percent == 70
    assert settings.min_document_height_percent == 80
    assert settings.crop_side_padding == 12
    assert settings.crop_bottom_padding == 18


def test_load_post_processing_settings_migrates_crop_padding(tmp_path: Path) -> None:
    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps({"post_processing": {"crop_padding": 12}}),
        encoding="utf-8",
    )

    settings = load_post_processing_settings(config_file)

    assert settings.crop_side_padding == 12
    assert settings.crop_bottom_padding == 12


def test_load_post_processing_settings_rejects_invalid_crop_mode(tmp_path: Path) -> None:
    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps({"post_processing": {"crop_mode": "letter"}}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="crop_mode"):
        load_post_processing_settings(config_file)


def test_load_post_processing_settings_accepts_none_crop_mode(tmp_path: Path) -> None:
    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps({"post_processing": {"crop_mode": "none"}}),
        encoding="utf-8",
    )

    settings = load_post_processing_settings(config_file)

    assert settings.crop_mode == "none"


def test_load_scan_ticket_config_rejects_invalid_json(tmp_path: Path) -> None:
    config_file = tmp_path / "config.json"
    config_file.write_text("{", encoding="utf-8")

    with pytest.raises(ValueError, match="JSON object"):
        load_scan_ticket_config(config_file)


def test_load_scan_ticket_config_rejects_invalid_value(tmp_path: Path) -> None:
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"resolution": 0}), encoding="utf-8")

    with pytest.raises(ValueError, match="resolution"):
        load_scan_ticket_config(config_file)


def test_scan_ticket_store_updates_file_and_memory(tmp_path: Path) -> None:
    config_file = tmp_path / "config.json"
    store = ScanTicketStore(config_file)

    scan_ticket = store.update({"resolution": "300", "compression_quality": "75"})

    assert scan_ticket.resolution == 300
    assert store.get().compression_quality == 75
    assert json.loads(config_file.read_text(encoding="utf-8"))["scan"] == scan_ticket_to_dict(
        scan_ticket
    )


def test_service_settings_store_updates_file_and_memory(tmp_path: Path) -> None:
    config_file = tmp_path / "config.json"
    store = ServiceSettingsStore(config_file)

    settings = store.update({"WSD_DEVICE_NAME": "Office Scanner", "DEBUG": "true"})

    assert settings.wsd_device_name == "Office Scanner"
    assert store.get().debug is True
    assert json.loads(config_file.read_text(encoding="utf-8"))[
        "service"
    ] == service_settings_to_dict(settings)


def test_post_processing_settings_store_updates_file_and_memory(tmp_path: Path) -> None:
    config_file = tmp_path / "config.json"
    store = PostProcessingSettingsStore(config_file)

    settings = store.update(
        {
            "enabled": "false",
            "crop_mode": "DIN-A4",
            "background_threshold": "210",
            "document_contrast": "20",
            "min_document_width_percent": "70",
            "min_document_height_percent": "80",
            "crop_side_padding": "12",
            "crop_bottom_padding": "18",
        }
    )

    assert settings.enabled is True
    assert settings.crop_mode == "DIN-A4"
    assert settings.background_threshold == 210
    assert settings.crop_side_padding == 12
    assert settings.crop_bottom_padding == 18
    assert store.get().enabled is True
    assert json.loads(config_file.read_text(encoding="utf-8"))[
        "post_processing"
    ] == post_processing_settings_to_dict(settings)


def test_service_settings_store_rejects_invalid_update_without_changing_memory(
    tmp_path: Path,
) -> None:
    store = ServiceSettingsStore(tmp_path / "config.json")
    before = store.get()

    with pytest.raises(ValueError, match="WSD_DEVICE_NAME"):
        store.update({"WSD_DEVICE_NAME": " "})

    assert store.get() == before


def test_config_from_env_ignores_invalid_scan_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("WSD_HOST", "192.0.2.10")
    monkeypatch.setenv("WSD_UUID_FILE", str(tmp_path / "uuid"))
    monkeypatch.setattr(config_module, "SCAN_CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setenv("SCAN_RESOLUTION", "0")

    config = Config.from_env()

    assert config.scan_ticket.resolution == 100


def test_scan_ticket_store_rejects_invalid_update_without_changing_memory(
    tmp_path: Path,
) -> None:
    store = ScanTicketStore(tmp_path / "config.json")
    before = store.get()

    with pytest.raises(ValueError, match="resolution"):
        store.update({"resolution": "0"})

    assert store.get() == before


def test_config_from_env_legacy_scan_env_no_longer_overrides(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("WSD_HOST", "192.0.2.10")
    monkeypatch.setenv("WSD_UUID_FILE", str(tmp_path / "uuid"))
    monkeypatch.setattr(config_module, "SCAN_CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setenv("SCAN_FORMAT", "tiff-single-uncompressed")
    monkeypatch.setenv("SCAN_INPUT_SOURCE", "Platen")
    monkeypatch.setenv("SCAN_CONTENT_TYPE", "Photo")
    monkeypatch.setenv("SCAN_COLOR_PROCESSING", "Grayscale8")
    monkeypatch.setenv("SCAN_RESOLUTION", "300")
    monkeypatch.setenv("SCAN_COMPRESSION_QUALITY", "75")
    monkeypatch.setenv("SCAN_IMAGES_TO_TRANSFER", "2")
    monkeypatch.setenv("SCAN_WIDTH", "2100")
    monkeypatch.setenv("SCAN_HEIGHT", "2970")
    monkeypatch.setenv("SCAN_REGION_X", "10")
    monkeypatch.setenv("SCAN_REGION_Y", "20")
    monkeypatch.setenv("SCAN_REGION_WIDTH", "2000")
    monkeypatch.setenv("SCAN_REGION_HEIGHT", "2900")
    monkeypatch.setenv("SCAN_BRIGHTNESS", "1")
    monkeypatch.setenv("SCAN_CONTRAST", "2")
    monkeypatch.setenv("SCAN_SHARPNESS", "3")
    monkeypatch.setenv("SCAN_ROTATION", "90")
    monkeypatch.setenv("SCAN_SCALING_WIDTH", "95")
    monkeypatch.setenv("SCAN_SCALING_HEIGHT", "96")

    config = Config.from_env()

    assert config.scan_ticket.format == "exif"
    assert config.scan_ticket.input_source == "Auto"
    assert config.scan_ticket.content_type == "Text"
    assert config.scan_ticket.color_processing == "RGB24"
    assert config.scan_ticket.resolution == 100
    assert config.scan_ticket.compression_quality == 50
    assert config.scan_ticket.images_to_transfer == 1
    assert config.scan_ticket.width == 8500
    assert config.scan_ticket.height == 11700
    assert config.scan_ticket.region_x == 0
    assert config.scan_ticket.region_y == 0
    assert config.scan_ticket.region_width == 8500
    assert config.scan_ticket.region_height == 11700
    assert config.scan_ticket.brightness == 0
    assert config.scan_ticket.contrast == 0
    assert config.scan_ticket.sharpness == 0
    assert config.scan_ticket.rotation == 0
    assert config.scan_ticket.scaling_width == 100
    assert config.scan_ticket.scaling_height == 100


def test_config_from_env_wsd_subscribe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("WSD_HOST", "192.0.2.10")
    monkeypatch.setenv("WSD_UUID_FILE", str(tmp_path / "uuid"))
    monkeypatch.setattr(config_module, "SCAN_CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setenv("WSD_SUBSCRIBE_ENABLED", "true")
    monkeypatch.setenv("WSD_SUBSCRIBE_INTERVAL_SECONDS", "15")

    config = Config.from_env()

    assert config.wsd_subscribe_enabled is True
    assert config.wsd_subscribe_interval_seconds == 15


def test_config_from_env_accepts_legacy_epson_printer_ip(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("WSD_HOST", "192.0.2.10")
    monkeypatch.setenv("WSD_UUID_FILE", str(tmp_path / "uuid"))
    monkeypatch.setattr(config_module, "SCAN_CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.delenv("WSD_SCANNER_IP", raising=False)
    monkeypatch.setenv("EPSON_PRINTER_IP", "192.0.2.21")

    config = Config.from_env()

    assert config.scanner_ip == "192.0.2.21"


def test_config_rejects_empty_device_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("WSD_HOST", "192.0.2.10")
    monkeypatch.setenv("WSD_UUID_FILE", str(tmp_path / "uuid"))
    monkeypatch.setattr(config_module, "SCAN_CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setenv("WSD_DEVICE_NAME", " ")

    with pytest.raises(ValueError, match="WSD_DEVICE_NAME"):
        Config.from_env()


def test_config_rejects_invalid_port(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("WSD_HOST", "192.0.2.10")
    monkeypatch.setenv("WSD_UUID_FILE", str(tmp_path / "uuid"))
    monkeypatch.setattr(config_module, "SCAN_CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setenv("WSD_HTTP_PORT", "99999")

    with pytest.raises(ValueError, match="WSD_HTTP_PORT"):
        Config.from_env()
