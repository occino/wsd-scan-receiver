from pathlib import Path

import pytest

from wsd_scan_receiver.config import (
    Config,
    load_or_create_uuid,
    normalize_endpoint_uuid,
    parse_bool,
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
    monkeypatch.setenv("WSD_DEVICE_NAME", "Office Scanner")
    monkeypatch.setenv("WSD_HTTP_PORT", "9999")
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "scans"))
    monkeypatch.setenv("RAW_DUMP_DIR", str(tmp_path / "dumps"))
    monkeypatch.setenv("DEBUG", "true")
    monkeypatch.setenv("WSD_SCANNER_IP", "192.0.2.21")
    monkeypatch.setenv("MAX_POST_BYTES", "1024")
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

    assert config.device_name == "Office Scanner"
    assert config.http_port == 9999
    assert config.debug is True
    assert config.host_ip == "192.0.2.10"
    assert config.metadata_url == "http://192.0.2.10:9999/metadata"
    assert config.scanner_ip == "192.0.2.21"
    assert config.max_post_bytes == 1024
    assert config.scan_ticket.format == "tiff-single-uncompressed"
    assert config.scan_ticket.input_source == "Platen"
    assert config.scan_ticket.content_type == "Photo"
    assert config.scan_ticket.color_processing == "Grayscale8"
    assert config.scan_ticket.resolution == 300
    assert config.scan_ticket.compression_quality == 75
    assert config.scan_ticket.images_to_transfer == 2
    assert config.scan_ticket.width == 2100
    assert config.scan_ticket.height == 2970
    assert config.scan_ticket.region_x == 10
    assert config.scan_ticket.region_y == 20
    assert config.scan_ticket.region_width == 2000
    assert config.scan_ticket.region_height == 2900
    assert config.scan_ticket.brightness == 1
    assert config.scan_ticket.contrast == 2
    assert config.scan_ticket.sharpness == 3
    assert config.scan_ticket.rotation == 90
    assert config.scan_ticket.scaling_width == 95
    assert config.scan_ticket.scaling_height == 96
    assert config.wsd_subscribe_enabled is False
    assert config.wsd_subscribe_interval_seconds == 60


def test_config_from_env_wsd_subscribe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("WSD_HOST", "192.0.2.10")
    monkeypatch.setenv("WSD_UUID_FILE", str(tmp_path / "uuid"))
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
    monkeypatch.delenv("WSD_SCANNER_IP", raising=False)
    monkeypatch.setenv("EPSON_PRINTER_IP", "192.0.2.21")

    config = Config.from_env()

    assert config.scanner_ip == "192.0.2.21"


def test_config_rejects_empty_device_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("WSD_HOST", "192.0.2.10")
    monkeypatch.setenv("WSD_UUID_FILE", str(tmp_path / "uuid"))
    monkeypatch.setenv("WSD_DEVICE_NAME", " ")

    with pytest.raises(ValueError, match="WSD_DEVICE_NAME"):
        Config.from_env()


def test_config_rejects_invalid_port(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("WSD_HOST", "192.0.2.10")
    monkeypatch.setenv("WSD_UUID_FILE", str(tmp_path / "uuid"))
    monkeypatch.setenv("WSD_HTTP_PORT", "99999")

    with pytest.raises(ValueError, match="WSD_HTTP_PORT"):
        Config.from_env()


def test_config_rejects_invalid_scan_resolution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("WSD_HOST", "192.0.2.10")
    monkeypatch.setenv("WSD_UUID_FILE", str(tmp_path / "uuid"))
    monkeypatch.setenv("SCAN_RESOLUTION", "0")

    with pytest.raises(ValueError, match="SCAN_RESOLUTION"):
        Config.from_env()
