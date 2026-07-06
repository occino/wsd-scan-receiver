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
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "consume"))
    monkeypatch.setenv("RAW_DUMP_DIR", str(tmp_path / "dumps"))
    monkeypatch.setenv("DEBUG", "true")

    config = Config.from_env()

    assert config.device_name == "Office Scanner"
    assert config.http_port == 9999
    assert config.debug is True
    assert config.host_ip == "192.0.2.10"
    assert config.metadata_url == "http://192.0.2.10:9999/metadata"
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
