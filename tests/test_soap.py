from pathlib import Path

from tests.helpers import default_scan_ticket
from wsd_scan_receiver.config import Config
from wsd_scan_receiver.soap import (
    MEX,
    SOAP12,
    WSA,
    WSA_2004,
    WST,
    parse_soap_envelope,
    route_soap_request,
)


def _config(tmp_path: Path) -> Config:
    return Config(
        device_name="Test Scanner",
        endpoint_uuid="uuid:test",
        http_port=5357,
        output_dir=tmp_path / "consume",
        debug=False,
        raw_dump_dir=tmp_path / "dumps",
        log_level="INFO",
        host_ip="127.0.0.1",
        interface=None,
        scanner_ip=None,
        max_post_bytes=100 * 1024 * 1024,
        wsd_subscribe_enabled=False,
        wsd_subscribe_interval_seconds=60,
        scan_ticket=default_scan_ticket(),
        uuid_file=tmp_path / "uuid",
    )


def test_parse_soap_envelope() -> None:
    payload = f"""<s:Envelope xmlns:s="{SOAP12}" xmlns:a="{WSA}" xmlns:wst="{WST}">
  <s:Header>
    <a:Action>{WST}/Get</a:Action>
    <a:MessageID>uuid:message</a:MessageID>
  </s:Header>
  <s:Body><wst:Get /></s:Body>
</s:Envelope>""".encode()

    request = parse_soap_envelope(payload)

    assert request.action == f"{WST}/Get"
    assert request.message_id == "uuid:message"
    assert request.body_tag == f"{{{WST}}}Get"


def test_route_get_metadata(tmp_path: Path) -> None:
    payload = f"""<s:Envelope xmlns:s="{SOAP12}" xmlns:a="{WSA}" xmlns:mex="{MEX}">
  <s:Header>
    <a:Action>{MEX}/GetMetadata/Request</a:Action>
    <a:MessageID>uuid:message</a:MessageID>
  </s:Header>
  <s:Body><mex:GetMetadata /></s:Body>
</s:Envelope>""".encode()

    status, body, content_type = route_soap_request(parse_soap_envelope(payload), _config(tmp_path))

    assert status == 200
    assert content_type.startswith("application/soap+xml")
    assert b"Test Scanner" in body
    assert b"GetMetadata/Response" in body


def test_route_transfer_get_with_ws_addressing_2004(tmp_path: Path) -> None:
    payload = f"""<s:Envelope xmlns:s="{SOAP12}" xmlns:a="{WSA_2004}">
  <s:Header>
    <a:Action>{WST}/Get</a:Action>
    <a:MessageID>uuid:message</a:MessageID>
  </s:Header>
  <s:Body />
</s:Envelope>""".encode()

    request = parse_soap_envelope(payload)
    status, body, content_type = route_soap_request(request, _config(tmp_path))

    assert request.action == f"{WST}/Get"
    assert request.addressing_ns == WSA_2004
    assert status == 200
    assert content_type.startswith("application/soap+xml")
    assert WSA_2004.encode() in body
    assert b"<mex:Metadata>" in body
    assert b"<wst:GetResponse>" not in body
    assert b"pub:Computer" in body
    assert b"Workgroup:WORKGROUP" in body
    assert b"wscn:ScannerServiceType" not in body


def test_route_scan_available_event(tmp_path: Path) -> None:
    payload = f"""<s:Envelope xmlns:s="{SOAP12}" xmlns:a="{WSA}" xmlns:wscn="http://schemas.microsoft.com/windows/2006/08/wdp/scan">
  <s:Header>
    <a:Action>http://schemas.microsoft.com/windows/2006/08/wdp/scan/ScanAvailableEvent</a:Action>
    <a:MessageID>uuid:event</a:MessageID>
  </s:Header>
  <s:Body><wscn:ScanAvailableEvent /></s:Body>
</s:Envelope>""".encode()

    status, body, content_type = route_soap_request(parse_soap_envelope(payload), _config(tmp_path))

    assert status == 202
    assert body == b""
    assert content_type.startswith("application/soap+xml")


def test_route_create_scan_job_to_receiver_is_unsupported(tmp_path: Path) -> None:
    payload = f"""<s:Envelope xmlns:s="{SOAP12}" xmlns:a="{WSA}" xmlns:wscn="http://schemas.microsoft.com/windows/2006/08/wdp/scan">
  <s:Header>
    <a:Action>http://schemas.microsoft.com/windows/2006/08/wdp/scan/CreateScanJob</a:Action>
    <a:MessageID>uuid:create</a:MessageID>
  </s:Header>
  <s:Body><wscn:CreateScanJobRequest /></s:Body>
</s:Envelope>""".encode()

    status, body, content_type = route_soap_request(parse_soap_envelope(payload), _config(tmp_path))

    assert status == 500
    assert content_type.startswith("application/soap+xml")
    assert b"Unsupported WSD action" in body
    assert b"experimental-job" not in body
