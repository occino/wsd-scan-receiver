import xml.etree.ElementTree as ET
from pathlib import Path

from wsd_scan_receiver.config import Config
from wsd_scan_receiver.discovery import (
    WSD_OASIS,
    DiscoveryService,
    hello_xml,
    is_relevant_probe,
    parse_probe,
    probe_match_xml,
)
from wsd_scan_receiver.soap import SOAP12, WSA, WSD


def _config(tmp_path: Path) -> Config:
    return Config(
        device_name="Test Scanner",
        endpoint_uuid="uuid:test",
        http_port=5357,
        output_dir=tmp_path / "consume",
        debug=False,
        raw_dump_dir=tmp_path / "dumps",
        log_level="INFO",
        host_ip="192.0.2.20",
        interface=None,
        scanner_ip=None,
        max_post_bytes=100 * 1024 * 1024,
        wsd_subscribe_enabled=False,
        wsd_subscribe_interval_seconds=60,
        uuid_file=tmp_path / "uuid",
    )


def test_parse_probe() -> None:
    payload = f"""<s:Envelope xmlns:s="{SOAP12}" xmlns:a="{WSA}" xmlns:d="{WSD}">
  <s:Header><a:MessageID>uuid:probe</a:MessageID></s:Header>
  <s:Body><d:Probe><d:Types>wscn:ScannerServiceType</d:Types></d:Probe></s:Body>
</s:Envelope>""".encode()

    probe = parse_probe(payload)

    assert probe is not None
    assert probe.message_id == "uuid:probe"
    assert probe.types == "wscn:ScannerServiceType"
    assert probe.discovery_ns == WSD


def test_parse_oasis_probe() -> None:
    payload = f"""<s:Envelope xmlns:s="{SOAP12}" xmlns:a="{WSA}" xmlns:d="{WSD_OASIS}">
  <s:Header><a:MessageID>uuid:oasis-probe</a:MessageID></s:Header>
  <s:Body><d:Probe><d:Types>wsdp:Device</d:Types></d:Probe></s:Body>
</s:Envelope>""".encode()

    probe = parse_probe(payload)

    assert probe is not None
    assert probe.message_id == "uuid:oasis-probe"
    assert probe.types == "wsdp:Device"
    assert probe.discovery_ns == WSD_OASIS


def test_probe_match_xml_contains_xaddrs(tmp_path: Path) -> None:
    response = probe_match_xml(_config(tmp_path), relates_to="uuid:probe")
    root = ET.fromstring(response)

    assert root.findtext(f"./{{{SOAP12}}}Header/{{{WSA}}}RelatesTo") == "uuid:probe"
    assert (
        root.findtext(f"./{{{SOAP12}}}Header/{{{WSA}}}To")
        == "http://www.w3.org/2005/08/addressing/anonymous"
    )
    assert b"http://192.0.2.20:5357/metadata" in response
    assert b"pub:Computer" in response
    assert b"wscn:ScanDeviceType" not in response
    assert b"wscn:ScannerServiceType" not in response
    assert b"pnpx:DeviceCategory/Computers" in response


def test_hello_xml_contains_device_and_oasis_action(tmp_path: Path) -> None:
    response = hello_xml(_config(tmp_path), discovery_ns=WSD_OASIS)
    root = ET.fromstring(response)

    assert root.findtext(f"./{{{SOAP12}}}Header/{{{WSA}}}Action") == f"{WSD_OASIS}/Hello"
    assert (
        root.findtext(f"./{{{SOAP12}}}Header/{{{WSA}}}To")
        == "urn:docs-oasis-open-org:ws-dd:ns:discovery:2009:01"
    )
    assert b"Test Scanner" in response
    assert b"http://192.0.2.20:5357/metadata" in response


def test_pub_computer_probe_is_relevant() -> None:
    payload = f"""<s:Envelope xmlns:s="{SOAP12}" xmlns:a="{WSA}" xmlns:d="{WSD}">
  <s:Header><a:MessageID>uuid:computer-probe</a:MessageID></s:Header>
  <s:Body><d:Probe><d:Types>pub:Computer</d:Types></d:Probe></s:Body>
</s:Envelope>""".encode()

    probe = parse_probe(payload)

    assert probe is not None
    assert is_relevant_probe(probe) is True


def test_generic_device_probe_is_relevant() -> None:
    payload = f"""<s:Envelope xmlns:s="{SOAP12}" xmlns:a="{WSA}" xmlns:d="{WSD}">
  <s:Header><a:MessageID>uuid:device-probe</a:MessageID></s:Header>
  <s:Body><d:Probe><d:Types>wsdp:Device</d:Types></d:Probe></s:Body>
</s:Envelope>""".encode()

    probe = parse_probe(payload)

    assert probe is not None
    assert is_relevant_probe(probe) is True


def test_scan_only_probe_is_not_relevant() -> None:
    payload = f"""<s:Envelope xmlns:s="{SOAP12}" xmlns:a="{WSA}" xmlns:d="{WSD}">
  <s:Header><a:MessageID>uuid:scan-probe</a:MessageID></s:Header>
  <s:Body>
    <d:Probe><d:Types>wscn:ScanDeviceType wscn:ScannerServiceType</d:Types></d:Probe>
  </s:Body>
</s:Envelope>""".encode()

    probe = parse_probe(payload)

    assert probe is not None
    assert is_relevant_probe(probe) is False


def test_discovery_observer_receives_foreign_payload(tmp_path: Path) -> None:
    seen: list[tuple[bytes, str]] = []
    service = DiscoveryService(
        _config(tmp_path),
        lambda payload, peer: seen.append((payload, peer)),
    )

    service.discovery_observer(b"<xml />", "192.0.2.21:3702")

    assert seen == [(b"<xml />", "192.0.2.21:3702")]
