from pathlib import Path

from wsd_scan_receiver.config import Config
from wsd_scan_receiver.soap import SOAP12, WSD
from wsd_scan_receiver.ws_scan_client import (
    DEVICE_PROBE_TYPES,
    create_scan_job_xml,
    parse_app_sequence_instance_id,
    parse_create_scan_job_info,
    parse_duration_seconds,
    parse_scan_available_event,
    parse_scan_device_discovery,
    parse_scanner_service_xaddrs,
    parse_subscription_info,
    probe_xml,
    renew_xml,
    retrieve_image_xml,
    subscribe_xml,
    transfer_get_xml,
    unsubscribe_xml,
)

WSA_2004 = "http://schemas.xmlsoap.org/ws/2004/08/addressing"


def _config(tmp_path: Path) -> Config:
    return Config(
        device_name="Paperless",
        endpoint_uuid="uuid:client",
        http_port=5357,
        output_dir=tmp_path / "consume",
        debug=False,
        raw_dump_dir=tmp_path / "dumps",
        log_level="INFO",
        host_ip="192.0.2.20",
        interface="ens16",
        epson_printer_ip=None,
        wsd_subscribe_enabled=True,
        wsd_subscribe_interval_seconds=60,
        uuid_file=tmp_path / "uuid",
    )


def test_parse_scan_device_probe_match() -> None:
    payload = f"""<s:Envelope xmlns:s="{SOAP12}" xmlns:a="{WSA_2004}" xmlns:d="{WSD}">
  <s:Body>
    <d:ProbeMatches>
      <d:ProbeMatch>
        <a:EndpointReference><a:Address>urn:uuid:scanner</a:Address></a:EndpointReference>
        <d:Types>wsdp:Device wscn:ScanDeviceType</d:Types>
        <d:XAddrs>http://192.0.2.21:5357/metadata</d:XAddrs>
      </d:ProbeMatch>
    </d:ProbeMatches>
  </s:Body>
</s:Envelope>""".encode()

    device = parse_scan_device_discovery(payload, peer="192.0.2.21:3702")

    assert device is not None
    assert device.endpoint == "urn:uuid:scanner"
    assert device.xaddrs == ("http://192.0.2.21:5357/metadata",)


def test_parse_scan_device_ignores_own_endpoint() -> None:
    payload = f"""<s:Envelope xmlns:s="{SOAP12}" xmlns:a="{WSA_2004}" xmlns:d="{WSD}">
  <s:Body><d:Hello>
    <a:EndpointReference><a:Address>uuid:client</a:Address></a:EndpointReference>
    <d:Types>wscn:ScanDeviceType</d:Types>
  </d:Hello></s:Body>
</s:Envelope>""".encode()

    assert parse_scan_device_discovery(payload, own_endpoint="uuid:client") is None


def test_parse_app_sequence_instance_id() -> None:
    payload = f"""<s:Envelope xmlns:s="{SOAP12}" xmlns:d="{WSD}">
  <s:Header><d:AppSequence InstanceId="1253" MessageNumber="1"/></s:Header>
  <s:Body/>
</s:Envelope>""".encode()

    assert parse_app_sequence_instance_id(payload) == "1253"


def test_parse_app_sequence_instance_id_missing() -> None:
    assert parse_app_sequence_instance_id(b"<Envelope/>") == ""


def test_probe_xml_targets_scan_devices() -> None:
    payload = probe_xml()

    assert b"wscn:ScanDeviceType" in payload
    assert b"wscn:ScannerServiceType" in payload
    assert f"{WSD}/Probe".encode() in payload


def test_directed_probe_xml_can_target_dpws_device() -> None:
    payload = probe_xml(DEVICE_PROBE_TYPES)

    assert b"<d:Types>wsdp:Device</d:Types>" in payload
    assert b"wscn:ScanDeviceType" not in payload


def test_transfer_get_xml_targets_device_endpoint() -> None:
    payload = transfer_get_xml("urn:uuid:scanner")

    assert b"http://schemas.xmlsoap.org/ws/2004/09/transfer/Get" in payload
    assert b"<a:To>urn:uuid:scanner</a:To>" in payload
    assert b"<s:Body/>" in payload


def test_renew_xml_contains_subscription_identifier() -> None:
    payload = renew_xml("http://192.0.2.21:80/WDP/SCAN", "urn:uuid:subscription")

    assert b"http://schemas.xmlsoap.org/ws/2004/08/eventing/Renew" in payload
    assert b"<a:To>http://192.0.2.21:80/WDP/SCAN</a:To>" in payload
    assert b"<wse:Identifier>urn:uuid:subscription</wse:Identifier>" in payload


def test_unsubscribe_xml_contains_subscription_identifier() -> None:
    payload = unsubscribe_xml("http://192.0.2.21:80/WDP/SCAN", "urn:uuid:subscription")

    assert b"http://schemas.xmlsoap.org/ws/2004/08/eventing/Unsubscribe" in payload
    assert b"<a:To>http://192.0.2.21:80/WDP/SCAN</a:To>" in payload
    assert b"<wse:Identifier>urn:uuid:subscription</wse:Identifier>" in payload
    assert b"<wse:Unsubscribe/>" in payload


def test_subscribe_xml_contains_notify_to_and_destination(tmp_path: Path) -> None:
    payload = subscribe_xml(_config(tmp_path), "http://192.0.2.21:5357/scanner")

    assert b"http://schemas.xmlsoap.org/ws/2004/08/eventing/Subscribe" in payload
    assert b"http://192.0.2.20:5357/events" in payload
    assert b"http://schemas.microsoft.com/windows/2006/08/wdp/scan/ScanAvailableEvent" in payload
    assert b"<wscn:ClientDisplayName>Paperless</wscn:ClientDisplayName>" in payload
    assert b"<wscn:ClientContext>Scan</wscn:ClientContext>" in payload


def test_parse_scanner_service_xaddrs_from_metadata() -> None:
    payload = f"""<s:Envelope
    xmlns:s="{SOAP12}"
    xmlns:a="{WSA_2004}"
    xmlns:wsdp="http://schemas.xmlsoap.org/ws/2006/02/devprof"
    xmlns:wscn="http://schemas.microsoft.com/windows/2006/08/wdp/scan"
    xmlns:wprt="http://schemas.microsoft.com/windows/2006/08/wdp/print">
  <s:Body>
    <wsdp:Relationship>
      <wsdp:Hosted>
        <a:EndpointReference><a:Address>http://192.0.2.21:80/WDP/SCAN</a:Address></a:EndpointReference>
        <wsdp:Types>wscn:ScannerServiceType</wsdp:Types>
      </wsdp:Hosted>
      <wsdp:Hosted>
        <a:EndpointReference><a:Address>http://192.0.2.21:80/WDP/PRINT</a:Address></a:EndpointReference>
        <wsdp:Types>wprt:PrinterServiceType</wsdp:Types>
      </wsdp:Hosted>
    </wsdp:Relationship>
  </s:Body>
</s:Envelope>""".encode()

    assert parse_scanner_service_xaddrs(payload) == ("http://192.0.2.21:80/WDP/SCAN",)


def test_parse_duration_seconds() -> None:
    assert parse_duration_seconds("PT15M") == 900
    assert parse_duration_seconds("PT1H") == 3600
    assert parse_duration_seconds("PT1H2M3S") == 3723


def test_parse_subscription_info_from_epson_response() -> None:
    payload = f"""<s:Envelope
    xmlns:s="{SOAP12}"
    xmlns:a="{WSA_2004}"
    xmlns:wse="http://schemas.xmlsoap.org/ws/2004/08/eventing"
    xmlns:wscn="http://schemas.microsoft.com/windows/2006/08/wdp/scan">
  <s:Body>
    <wse:SubscribeResponse>
      <wse:SubscriptionManager>
        <a:Address>http://192.0.2.21:80/WDP/SCAN</a:Address>
        <a:ReferenceParameters>
          <wse:Identifier>urn:uuid:subscription</wse:Identifier>
        </a:ReferenceParameters>
      </wse:SubscriptionManager>
      <wse:Expires>PT15M</wse:Expires>
      <wscn:DestinationResponses>
        <wscn:DestinationResponse>
          <wscn:DestinationToken>token</wscn:DestinationToken>
        </wscn:DestinationResponse>
      </wscn:DestinationResponses>
    </wse:SubscribeResponse>
  </s:Body>
</s:Envelope>""".encode()

    info = parse_subscription_info(payload)

    assert info is not None
    assert info.manager_url == "http://192.0.2.21:80/WDP/SCAN"
    assert info.identifier == "urn:uuid:subscription"
    assert info.expires_seconds == 900
    assert info.destination_token == "token"


def test_parse_scan_available_event() -> None:
    payload = f"""<s:Envelope
    xmlns:s="{SOAP12}"
    xmlns:wscn="http://schemas.microsoft.com/windows/2006/08/wdp/scan">
  <s:Body>
    <wscn:ScanAvailableEvent>
      <wscn:ClientContext>Scan</wscn:ClientContext>
      <wscn:ScanIdentifier>DCCD2F4A1F89_117_0</wscn:ScanIdentifier>
    </wscn:ScanAvailableEvent>
  </s:Body>
</s:Envelope>""".encode()

    event = parse_scan_available_event(payload)

    assert event is not None
    assert event.client_context == "Scan"
    assert event.scan_identifier == "DCCD2F4A1F89_117_0"


def test_create_scan_job_xml_contains_push_event_identifiers() -> None:
    payload = create_scan_job_xml(
        "http://192.0.2.21:80/WDP/SCAN",
        scan_identifier="scan-id",
        destination_token="dest-token",
        device_name="Paperless",
        from_endpoint="urn:uuid:client",
        input_source="Platen",
    )

    assert b"http://schemas.microsoft.com/windows/2006/08/wdp/scan/CreateScanJob" in payload
    assert b"<a:Address>urn:uuid:client</a:Address>" in payload
    assert b"<wscn:ScanIdentifier>scan-id</wscn:ScanIdentifier>" in payload
    assert b"<wscn:DestinationToken>dest-token</wscn:DestinationToken>" in payload
    assert b"<wscn:Format>exif</wscn:Format>" in payload
    assert b"<wscn:CompressionQualityFactor>50</wscn:CompressionQualityFactor>" in payload
    assert b"<wscn:InputSource>Platen</wscn:InputSource>" in payload
    assert b"<wscn:ContentType>Text</wscn:ContentType>" in payload
    assert b"<wscn:ScanRegionWidth>8500</wscn:ScanRegionWidth>" in payload
    assert b"<wscn:ScanRegionHeight>11700</wscn:ScanRegionHeight>" in payload
    assert b"<wscn:ColorProcessing>RGB24</wscn:ColorProcessing>" in payload
    assert b"<wscn:MediaBack>" in payload


def test_parse_create_scan_job_info() -> None:
    payload = f"""<s:Envelope
    xmlns:s="{SOAP12}"
    xmlns:wscn="http://schemas.microsoft.com/windows/2006/08/wdp/scan">
  <s:Body>
    <wscn:CreateScanJobResponse>
      <wscn:JobId>1</wscn:JobId>
      <wscn:JobToken>job-token</wscn:JobToken>
    </wscn:CreateScanJobResponse>
  </s:Body>
</s:Envelope>""".encode()

    info = parse_create_scan_job_info(payload)

    assert info is not None
    assert info.job_id == "1"
    assert info.job_token == "job-token"


def test_retrieve_image_xml_contains_job_identifiers() -> None:
    payload = retrieve_image_xml(
        "http://192.0.2.21:80/WDP/SCAN",
        job_id="1",
        job_token="job-token",
    )

    assert b"http://schemas.microsoft.com/windows/2006/08/wdp/scan/RetrieveImage" in payload
    assert b"<wscn:JobId>1</wscn:JobId>" in payload
    assert b"<wscn:JobToken>job-token</wscn:JobToken>" in payload
    assert b"<wscn:DocumentName>scan</wscn:DocumentName>" in payload
