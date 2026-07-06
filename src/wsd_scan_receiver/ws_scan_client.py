"""Experimental active WSD scan client subscription support.

WSD push scanning is client-driven: a computer subscribes to a scanner service
for ScanAvailableEvent notifications, and the scanner can then show that client
as a destination. This module probes for WSD scanners and attempts the
WS-Eventing Subscribe step needed before any push scan can arrive.
"""

from __future__ import annotations

import html
import json
import logging
import socket
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import Config
from .discovery import (
    DISCOVERY_PORT,
    MULTICAST_GROUP,
    MULTICAST_GROUP_V6,
)
from .receiver import guess_extension, write_payload
from .soap import SOAP12, WSA_2004, WSCN, WSD

LOGGER = logging.getLogger(__name__)
WSE = "http://schemas.xmlsoap.org/ws/2004/08/eventing"
WSE_DELIVERY_PUSH = "http://schemas.xmlsoap.org/ws/2004/08/eventing/DeliveryModes/Push"
WST = "http://schemas.xmlsoap.org/ws/2004/09/transfer"
SCAN_PROBE_TYPES = "wscn:ScanDeviceType wscn:ScannerServiceType"
DEVICE_PROBE_TYPES = "wsdp:Device"
EPSON_STABLE_DISCOVERY_PATH = "/StableWSDiscoveryEndpoint/schemas-xmlsoap-org_ws_2005_04_discovery"
SUBSCRIPTION_RENEW_MARGIN_SECONDS = 120
SUBSCRIBE_EXPIRES = "PT1H"
SCAN_CLIENT_CONTEXT = "Scan"
DPWS_ACTION_FILTER = "http://schemas.xmlsoap.org/ws/2006/02/devprof/Action"


@dataclass(frozen=True)
class DiscoveredScanDevice:
    """Useful WS-Discovery fields for a remote scanner device."""

    endpoint: str
    types: str
    xaddrs: tuple[str, ...]
    peer: str


@dataclass(frozen=True)
class SubscriptionInfo:
    """State returned by a WS-Eventing Subscribe/Renew response."""

    manager_url: str
    identifier: str
    expires_seconds: int
    destination_token: str = ""


@dataclass(frozen=True)
class ActiveSubscription:
    """In-memory subscription state for a remote scanner endpoint."""

    manager_url: str
    identifier: str
    expires_at: float
    destination_token: str = ""
    discovery_instance_id: str = ""


@dataclass(frozen=True)
class ScanAvailableEvent:
    """Fields from a WS-Scan ScanAvailableEvent notification."""

    client_context: str
    scan_identifier: str
    input_source: str = ""


@dataclass(frozen=True)
class CreateScanJobInfo:
    """Fields needed to retrieve image data for a created WS-Scan job."""

    job_id: str
    job_token: str


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _first_child(element: ET.Element | None, local_name: str) -> ET.Element | None:
    if element is None:
        return None
    for child in element:
        if _local_name(child.tag) == local_name:
            return child
    return None


def _first_descendant(element: ET.Element, local_name: str) -> ET.Element | None:
    for child in element.iter():
        if _local_name(child.tag) == local_name:
            return child
    return None


def _text(element: ET.Element | None) -> str:
    if element is None or element.text is None:
        return ""
    return element.text.strip()


def parse_duration_seconds(value: str, *, default: int = 900) -> int:
    """Parse the simple ISO-8601 durations commonly returned by WS-Eventing."""
    value = value.strip().upper()
    if not value.startswith("PT"):
        return default

    total = 0
    number = ""
    for char in value[2:]:
        if char.isdigit():
            number += char
            continue
        if not number:
            return default
        amount = int(number)
        number = ""
        if char == "H":
            total += amount * 3600
        elif char == "M":
            total += amount * 60
        elif char == "S":
            total += amount
        else:
            return default
    return total or default


def parse_scan_device_discovery(
    payload: bytes,
    *,
    peer: str = "",
    own_endpoint: str = "",
) -> DiscoveredScanDevice | None:
    """Extract a remote WSD scan device from Hello/ProbeMatch/ResolveMatch XML."""
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return None

    body = _first_child(root, "Body")
    if body is None:
        return None

    candidates: list[ET.Element] = []
    for element in body.iter():
        if _local_name(element.tag) in {"Hello", "ProbeMatch", "ResolveMatch"}:
            candidates.append(element)

    for candidate in candidates:
        endpoint = _text(_first_descendant(candidate, "Address"))
        types = _text(_first_child(candidate, "Types"))
        xaddrs = tuple(_text(_first_child(candidate, "XAddrs")).split())
        if not endpoint or endpoint == own_endpoint:
            continue
        if "scan" not in types.lower() and "wscn" not in types.lower():
            continue
        return DiscoveredScanDevice(
            endpoint=endpoint,
            types=types,
            xaddrs=xaddrs,
            peer=peer,
        )
    return None


def parse_app_sequence_instance_id(payload: bytes) -> str:
    """Return the WS-Discovery AppSequence InstanceId, if present.

    DPWS devices are expected to change the AppSequence instance after a
    reboot. Epson scanners appear to keep WSD destinations only in volatile
    state, so a changed InstanceId is a strong signal that subscriptions should
    be created again.
    """
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return ""

    for element in root.iter():
        if _local_name(element.tag) == "AppSequence":
            return element.attrib.get("InstanceId", "")
    return ""


def probe_xml(types: str = SCAN_PROBE_TYPES) -> bytes:
    """Build a WS-Discovery Probe for WSD scan devices."""
    message_id = f"urn:uuid:{uuid.uuid4()}"
    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<s:Envelope
    xmlns:s="{SOAP12}"
    xmlns:a="{WSA_2004}"
    xmlns:d="{WSD}"
    xmlns:wsdp="http://schemas.xmlsoap.org/ws/2006/02/devprof"
    xmlns:wscn="{WSCN}">
  <s:Header>
    <a:Action>{WSD}/Probe</a:Action>
    <a:MessageID>{message_id}</a:MessageID>
    <a:To>urn:schemas-xmlsoap-org:ws:2005:04:discovery</a:To>
    <a:ReplyTo>
      <a:Address>http://schemas.xmlsoap.org/ws/2004/08/addressing/role/anonymous</a:Address>
    </a:ReplyTo>
  </s:Header>
  <s:Body>
    <d:Probe>
      <d:Types>{html.escape(types)}</d:Types>
    </d:Probe>
  </s:Body>
</s:Envelope>"""
    return xml.encode("utf-8")


def transfer_get_xml(target: str) -> bytes:
    """Build the WS-Transfer Get request used to retrieve DPWS metadata."""
    message_id = f"urn:uuid:{uuid.uuid4()}"
    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<s:Envelope
    xmlns:s="{SOAP12}"
    xmlns:a="{WSA_2004}">
  <s:Header>
    <a:Action>{WST}/Get</a:Action>
    <a:MessageID>{message_id}</a:MessageID>
    <a:To>{html.escape(target)}</a:To>
    <a:ReplyTo>
      <a:Address>http://schemas.xmlsoap.org/ws/2004/08/addressing/role/anonymous</a:Address>
    </a:ReplyTo>
  </s:Header>
  <s:Body/>
</s:Envelope>"""
    return xml.encode("utf-8")


def renew_xml(manager_url: str, identifier: str) -> bytes:
    """Build a WS-Eventing Renew request for an existing scan subscription."""
    message_id = f"urn:uuid:{uuid.uuid4()}"
    identifier_header = ""
    if identifier:
        identifier_header = f"\n    <wse:Identifier>{html.escape(identifier)}</wse:Identifier>"
    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<s:Envelope
    xmlns:s="{SOAP12}"
    xmlns:a="{WSA_2004}"
    xmlns:wse="{WSE}">
  <s:Header>
    <a:Action>{WSE}/Renew</a:Action>
    <a:MessageID>{message_id}</a:MessageID>
    <a:To>{html.escape(manager_url)}</a:To>{identifier_header}
    <a:ReplyTo>
      <a:Address>http://schemas.xmlsoap.org/ws/2004/08/addressing/role/anonymous</a:Address>
    </a:ReplyTo>
  </s:Header>
  <s:Body>
    <wse:Renew>
      <wse:Expires>{SUBSCRIBE_EXPIRES}</wse:Expires>
    </wse:Renew>
  </s:Body>
</s:Envelope>"""
    return xml.encode("utf-8")


def unsubscribe_xml(manager_url: str, identifier: str) -> bytes:
    """Build a WS-Eventing Unsubscribe request for an existing subscription."""
    message_id = f"urn:uuid:{uuid.uuid4()}"
    identifier_header = ""
    if identifier:
        identifier_header = f"\n    <wse:Identifier>{html.escape(identifier)}</wse:Identifier>"
    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<s:Envelope
    xmlns:s="{SOAP12}"
    xmlns:a="{WSA_2004}"
    xmlns:wse="{WSE}">
  <s:Header>
    <a:Action>{WSE}/Unsubscribe</a:Action>
    <a:MessageID>{message_id}</a:MessageID>
    <a:To>{html.escape(manager_url)}</a:To>{identifier_header}
    <a:ReplyTo>
      <a:Address>http://schemas.xmlsoap.org/ws/2004/08/addressing/role/anonymous</a:Address>
    </a:ReplyTo>
  </s:Header>
  <s:Body>
    <wse:Unsubscribe/>
  </s:Body>
</s:Envelope>"""
    return xml.encode("utf-8")


def subscribe_xml(config: Config, target_url: str) -> bytes:
    """Build a conservative WS-Eventing Subscribe request for scan events."""
    message_id = f"urn:uuid:{uuid.uuid4()}"
    notify_to = f"http://{config.host_ip}:{config.http_port}/events"
    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<s:Envelope
    xmlns:s="{SOAP12}"
    xmlns:a="{WSA_2004}"
    xmlns:wse="{WSE}"
    xmlns:wscn="{WSCN}">
  <s:Header>
    <a:Action>{WSE}/Subscribe</a:Action>
    <a:MessageID>{message_id}</a:MessageID>
    <a:To>{html.escape(target_url)}</a:To>
    <a:ReplyTo>
      <a:Address>http://schemas.xmlsoap.org/ws/2004/08/addressing/role/anonymous</a:Address>
    </a:ReplyTo>
  </s:Header>
  <s:Body>
    <wse:Subscribe>
      <wse:Delivery Mode="{WSE_DELIVERY_PUSH}">
        <wse:NotifyTo>
          <a:Address>{html.escape(notify_to)}</a:Address>
        </wse:NotifyTo>
      </wse:Delivery>
      <wse:Expires>{SUBSCRIBE_EXPIRES}</wse:Expires>
      <wse:Filter Dialect="{DPWS_ACTION_FILTER}">{WSCN}/ScanAvailableEvent</wse:Filter>
      <wscn:ScanDestinations>
        <wscn:ScanDestination>
          <wscn:ClientDisplayName>{html.escape(config.device_name)}</wscn:ClientDisplayName>
          <wscn:ClientContext>{SCAN_CLIENT_CONTEXT}</wscn:ClientContext>
        </wscn:ScanDestination>
      </wscn:ScanDestinations>
    </wse:Subscribe>
  </s:Body>
</s:Envelope>"""
    return xml.encode("utf-8")


def parse_scanner_service_xaddrs(payload: bytes) -> tuple[str, ...]:
    """Extract hosted WS-Scan service addresses from DPWS metadata XML."""
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return ()

    addresses: list[str] = []
    for hosted in root.iter():
        if _local_name(hosted.tag) != "Hosted":
            continue
        types = _text(_first_child(hosted, "Types"))
        if "scannerservicetype" not in types.lower() and "wscn" not in types.lower():
            continue
        endpoint = _first_child(hosted, "EndpointReference")
        address = _text(_first_child(endpoint, "Address"))
        if address:
            addresses.append(address)
    return tuple(dict.fromkeys(addresses))


def parse_subscription_info(payload: bytes) -> SubscriptionInfo | None:
    """Extract WS-Eventing subscription manager details from a response."""
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return None

    manager_url = ""
    identifier = ""
    for manager in root.iter():
        if _local_name(manager.tag) != "SubscriptionManager":
            continue
        manager_url = _text(_first_child(manager, "Address"))
        identifier = _text(_first_descendant(manager, "Identifier"))
        break

    expires = ""
    destination_token = ""
    for element in root.iter():
        if _local_name(element.tag) == "Expires":
            expires = _text(element)
        elif _local_name(element.tag) == "DestinationToken":
            destination_token = _text(element)

    if not manager_url:
        return None
    return SubscriptionInfo(
        manager_url=manager_url,
        identifier=identifier,
        expires_seconds=parse_duration_seconds(expires),
        destination_token=destination_token,
    )


def parse_scan_available_event(payload: bytes) -> ScanAvailableEvent | None:
    """Extract ClientContext and ScanIdentifier from a ScanAvailableEvent."""
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return None

    for event in root.iter():
        if _local_name(event.tag) != "ScanAvailableEvent":
            continue
        scan_identifier = _text(_first_child(event, "ScanIdentifier"))
        if not scan_identifier:
            return None
        return ScanAvailableEvent(
            client_context=_text(_first_child(event, "ClientContext")),
            scan_identifier=scan_identifier,
            input_source=_text(_first_child(event, "InputSource")),
        )
    return None


def parse_create_scan_job_info(payload: bytes) -> CreateScanJobInfo | None:
    """Extract JobId and JobToken from a CreateScanJobResponse."""
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return None

    for response in root.iter():
        if _local_name(response.tag) != "CreateScanJobResponse":
            continue
        job_id = _text(_first_child(response, "JobId"))
        job_token = _text(_first_child(response, "JobToken"))
        if job_id and job_token:
            return CreateScanJobInfo(job_id=job_id, job_token=job_token)
    return None


def create_scan_job_xml(
    target_url: str,
    *,
    scan_identifier: str,
    destination_token: str,
    device_name: str,
    from_endpoint: str,
    input_source: str = "",
) -> bytes:
    """Build a WS-Scan CreateScanJobRequest after a push ScanAvailableEvent."""
    message_id = f"urn:uuid:{uuid.uuid4()}"
    source = input_source or "Auto"
    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<s:Envelope
    xmlns:s="{SOAP12}"
    xmlns:a="{WSA_2004}"
    xmlns:wscn="{WSCN}">
  <s:Header>
    <a:Action>{WSCN}/CreateScanJob</a:Action>
    <a:MessageID>{message_id}</a:MessageID>
    <a:To>{html.escape(target_url)}</a:To>
    <a:From>
      <a:Address>{html.escape(from_endpoint)}</a:Address>
    </a:From>
    <a:ReplyTo>
      <a:Address>http://schemas.xmlsoap.org/ws/2004/08/addressing/role/anonymous</a:Address>
    </a:ReplyTo>
  </s:Header>
  <s:Body>
    <wscn:CreateScanJobRequest>
      <wscn:ScanIdentifier>{html.escape(scan_identifier)}</wscn:ScanIdentifier>
      <wscn:DestinationToken>{html.escape(destination_token)}</wscn:DestinationToken>
      <wscn:ScanTicket>
        <wscn:JobDescription>
          <wscn:JobName>{html.escape(device_name)} WSD Scan</wscn:JobName>
          <wscn:JobOriginatingUserName>{html.escape(device_name)}</wscn:JobOriginatingUserName>
          <wscn:JobInformation>Device initiated scan</wscn:JobInformation>
        </wscn:JobDescription>
        <wscn:DocumentParameters>
          <wscn:Format>exif</wscn:Format>
          <wscn:CompressionQualityFactor>50</wscn:CompressionQualityFactor>
          <wscn:ImagesToTransfer>1</wscn:ImagesToTransfer>
          <wscn:InputSource>{html.escape(source)}</wscn:InputSource>
          <wscn:ContentType>Text</wscn:ContentType>
          <wscn:InputSize>
            <wscn:InputMediaSize>
              <wscn:Width>8500</wscn:Width>
              <wscn:Height>11700</wscn:Height>
            </wscn:InputMediaSize>
          </wscn:InputSize>
          <wscn:Exposure>
            <wscn:ExposureSettings>
              <wscn:Contrast>0</wscn:Contrast>
              <wscn:Brightness>0</wscn:Brightness>
              <wscn:Sharpness>0</wscn:Sharpness>
            </wscn:ExposureSettings>
          </wscn:Exposure>
          <wscn:Scaling>
            <wscn:ScalingWidth>100</wscn:ScalingWidth>
            <wscn:ScalingHeight>100</wscn:ScalingHeight>
          </wscn:Scaling>
          <wscn:Rotation>0</wscn:Rotation>
          <wscn:MediaSides>
            <wscn:MediaFront>
              <wscn:ScanRegion>
                <wscn:ScanRegionXOffset>0</wscn:ScanRegionXOffset>
                <wscn:ScanRegionYOffset>0</wscn:ScanRegionYOffset>
                <wscn:ScanRegionWidth>8500</wscn:ScanRegionWidth>
                <wscn:ScanRegionHeight>11700</wscn:ScanRegionHeight>
              </wscn:ScanRegion>
              <wscn:ColorProcessing>RGB24</wscn:ColorProcessing>
              <wscn:Resolution>
                <wscn:Width>100</wscn:Width>
                <wscn:Height>100</wscn:Height>
              </wscn:Resolution>
            </wscn:MediaFront>
            <wscn:MediaBack>
              <wscn:ScanRegion>
                <wscn:ScanRegionXOffset>0</wscn:ScanRegionXOffset>
                <wscn:ScanRegionYOffset>0</wscn:ScanRegionYOffset>
                <wscn:ScanRegionWidth>8500</wscn:ScanRegionWidth>
                <wscn:ScanRegionHeight>11700</wscn:ScanRegionHeight>
              </wscn:ScanRegion>
              <wscn:ColorProcessing>RGB24</wscn:ColorProcessing>
              <wscn:Resolution>
                <wscn:Width>100</wscn:Width>
                <wscn:Height>100</wscn:Height>
              </wscn:Resolution>
            </wscn:MediaBack>
          </wscn:MediaSides>
        </wscn:DocumentParameters>
      </wscn:ScanTicket>
    </wscn:CreateScanJobRequest>
  </s:Body>
</s:Envelope>"""
    return xml.encode("utf-8")


def retrieve_image_xml(target_url: str, *, job_id: str, job_token: str) -> bytes:
    """Build a WS-Scan RetrieveImageRequest for a created scan job."""
    message_id = f"urn:uuid:{uuid.uuid4()}"
    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<s:Envelope
    xmlns:s="{SOAP12}"
    xmlns:a="{WSA_2004}"
    xmlns:wscn="{WSCN}">
  <s:Header>
    <a:Action>{WSCN}/RetrieveImage</a:Action>
    <a:MessageID>{message_id}</a:MessageID>
    <a:To>{html.escape(target_url)}</a:To>
    <a:ReplyTo>
      <a:Address>http://schemas.xmlsoap.org/ws/2004/08/addressing/role/anonymous</a:Address>
    </a:ReplyTo>
  </s:Header>
  <s:Body>
    <wscn:RetrieveImageRequest>
      <wscn:JobId>{html.escape(job_id)}</wscn:JobId>
      <wscn:JobToken>{html.escape(job_token)}</wscn:JobToken>
      <wscn:DocumentDescription>
        <wscn:DocumentName>scan</wscn:DocumentName>
      </wscn:DocumentDescription>
    </wscn:RetrieveImageRequest>
  </s:Body>
</s:Envelope>"""
    return xml.encode("utf-8")


class WsScanClientService:
    """Active WS-Scan client that probes scanners and tries event subscription."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_subscribe_attempt: dict[str, float] = {}
        self._subscriptions: dict[str, ActiveSubscription] = {}
        self._discovery_instance_ids: dict[str, str] = {}
        self._subscription_state_path = config.uuid_file.parent / "wsd-subscriptions.json"
        self._load_subscription_state()

    def start(self) -> None:
        if not self.config.wsd_subscribe_enabled:
            return
        self._thread = threading.Thread(target=self._serve, name="ws-scan-client", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._unsubscribe_all()

    def _serve(self) -> None:
        LOGGER.info(
            "active WSD scan subscription client started",
            extra={"interval_seconds": self.config.wsd_subscribe_interval_seconds},
        )
        while not self._stop.is_set():
            self.probe_once()
            self._stop.wait(max(self.config.wsd_subscribe_interval_seconds, 5))

    def probe_once(self) -> None:
        """Send scan-device probes and attempt subscription for responses."""
        self._probe_configured_printer_http()
        payload = probe_xml()
        self._probe_ipv4(payload)
        self._probe_ipv6(payload)

    def observe_discovery_payload(self, payload: bytes, peer: str) -> None:
        """Inspect multicast discovery traffic seen by the main listener."""
        if not self.config.wsd_subscribe_enabled:
            return
        device = parse_scan_device_discovery(
            payload,
            peer=peer,
            own_endpoint=self.config.endpoint_uuid,
        )
        if device is None:
            return
        LOGGER.info(
            "observed remote WSD scan device",
            extra={
                "peer": device.peer,
                "endpoint": device.endpoint,
                "types": device.types,
                "xaddrs": list(device.xaddrs),
            },
        )
        self._record_discovery_instance(
            device.endpoint,
            parse_app_sequence_instance_id(payload),
        )
        self._subscribe_to_device(device)

    def handle_scan_available_event(self, payload: bytes) -> None:
        """Start the client side of a push scan after a ScanAvailableEvent."""
        event = parse_scan_available_event(payload)
        if event is None:
            LOGGER.warning("could not parse ScanAvailableEvent payload")
            return
        if event.client_context and event.client_context != SCAN_CLIENT_CONTEXT:
            LOGGER.info(
                "ignoring ScanAvailableEvent for another client context",
                extra={
                    "client_context": event.client_context,
                    "scan_identifier": event.scan_identifier,
                },
            )
            return

        thread = threading.Thread(
            target=self._run_push_scan_job,
            args=(event,),
            name="ws-scan-push-job",
            daemon=True,
        )
        thread.start()

    def _probe_ipv4(self, payload: bytes) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        try:
            sock.settimeout(3.0)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
            sock.sendto(payload, (MULTICAST_GROUP, DISCOVERY_PORT))
            LOGGER.info("sent active WS-Discovery scan Probe", extra={"family": "ipv4"})
            self._read_probe_responses(sock, "ipv4")
        except OSError:
            LOGGER.exception("active IPv4 WS-Discovery scan Probe failed")
        finally:
            sock.close()

    def _probe_ipv6(self, payload: bytes) -> None:
        if not self.config.interface:
            return
        try:
            if_index = socket.if_nametoindex(self.config.interface)
        except OSError:
            return

        sock = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        try:
            sock.settimeout(3.0)
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_MULTICAST_HOPS, 2)
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_MULTICAST_IF, if_index)
            sock.sendto(payload, (MULTICAST_GROUP_V6, DISCOVERY_PORT, 0, if_index))
            LOGGER.info(
                "sent active WS-Discovery scan Probe",
                extra={"family": "ipv6", "if_index": if_index},
            )
            self._read_probe_responses(sock, "ipv6")
        except OSError:
            LOGGER.exception("active IPv6 WS-Discovery scan Probe failed")
        finally:
            sock.close()

    def _read_probe_responses(self, sock: socket.socket, family: str) -> None:
        deadline = time.monotonic() + 3.0
        found = 0
        while time.monotonic() < deadline and not self._stop.is_set():
            try:
                payload, addr = sock.recvfrom(65535)
            except TimeoutError:
                break
            except OSError:
                break

            peer = f"{addr[0]}:{addr[1]}"
            device = parse_scan_device_discovery(
                payload,
                peer=peer,
                own_endpoint=self.config.endpoint_uuid,
            )
            if device is None:
                continue
            found += 1
            LOGGER.info(
                "discovered remote WSD scan device",
                extra={
                    "family": family,
                    "peer": device.peer,
                    "endpoint": device.endpoint,
                    "types": device.types,
                    "xaddrs": list(device.xaddrs),
                },
            )
            self._subscribe_to_device(device)

        if found == 0:
            LOGGER.info(
                "active WS-Discovery scan Probe found no scan devices",
                extra={"family": family},
            )

    def _probe_configured_printer_http(self) -> None:
        if not self.config.epson_printer_ip:
            return

        discovery_url = f"http://{self.config.epson_printer_ip}:80{EPSON_STABLE_DISCOVERY_PATH}"
        LOGGER.info(
            "probing configured Epson WSD stable discovery endpoint",
            extra={"url": discovery_url},
        )
        response = self._post_soap(discovery_url, probe_xml(DEVICE_PROBE_TYPES))
        if response is None:
            return

        device = parse_scan_device_discovery(
            response,
            peer=discovery_url,
            own_endpoint=self.config.endpoint_uuid,
        )
        if device is None:
            LOGGER.info(
                "configured Epson WSD stable discovery response did not contain a scan device",
                extra={"url": discovery_url},
            )
            return

        LOGGER.info(
            "configured Epson WSD device discovered",
            extra={
                "endpoint": device.endpoint,
                "types": device.types,
                "xaddrs": list(device.xaddrs),
            },
        )
        self._record_discovery_instance(
            device.endpoint,
            parse_app_sequence_instance_id(response),
        )
        scanner_xaddrs = self._resolve_scanner_service_xaddrs(device)
        if scanner_xaddrs:
            device = DiscoveredScanDevice(
                endpoint=device.endpoint,
                types=device.types,
                xaddrs=scanner_xaddrs,
                peer=device.peer,
            )
        self._subscribe_to_device(device)

    def _resolve_scanner_service_xaddrs(self, device: DiscoveredScanDevice) -> tuple[str, ...]:
        scanner_xaddrs: list[str] = []
        for xaddr in device.xaddrs:
            if not xaddr.lower().startswith("http://"):
                continue
            response = self._post_soap(xaddr, transfer_get_xml(device.endpoint))
            if response is None:
                continue
            found = parse_scanner_service_xaddrs(response)
            if found:
                LOGGER.info(
                    "resolved hosted WSD scanner service endpoint",
                    extra={"device_xaddr": xaddr, "scanner_xaddrs": list(found)},
                )
                scanner_xaddrs.extend(found)
        return tuple(dict.fromkeys(scanner_xaddrs))

    def _record_discovery_instance(self, endpoint: str, instance_id: str) -> None:
        if not instance_id:
            return

        previous = self._discovery_instance_ids.get(endpoint)
        self._discovery_instance_ids[endpoint] = instance_id
        if previous is None or previous == instance_id:
            return

        self._subscriptions.pop(endpoint, None)
        self._last_subscribe_attempt.pop(endpoint, None)
        self._save_subscription_state()
        LOGGER.info(
            "remote WSD scanner discovery instance changed; cleared subscription state",
            extra={
                "endpoint": endpoint,
                "previous_instance_id": previous,
                "instance_id": instance_id,
            },
        )

    def _subscribe_to_device(self, device: DiscoveredScanDevice) -> None:
        if not device.xaddrs:
            LOGGER.info(
                "remote WSD scan device has no XAddrs; cannot subscribe yet",
                extra={"endpoint": device.endpoint, "peer": device.peer},
            )
            return

        now = time.monotonic()
        existing = self._subscriptions.get(device.endpoint)
        if existing and not existing.destination_token:
            LOGGER.info(
                "WSD scan subscription has no destination token; subscribing again",
                extra={"endpoint": device.endpoint, "manager_url": existing.manager_url},
            )
            self._subscriptions.pop(device.endpoint, None)
            existing = None
        if existing and existing.expires_at - now > SUBSCRIPTION_RENEW_MARGIN_SECONDS:
            LOGGER.info(
                "WSD scan subscription still active; skipping duplicate subscribe",
                extra={
                    "endpoint": device.endpoint,
                    "manager_url": existing.manager_url,
                    "expires_in_seconds": int(existing.expires_at - now),
                },
            )
            return
        if existing and self._try_renew(device.endpoint, existing):
            return

        last = self._last_subscribe_attempt.get(device.endpoint, 0.0)
        if now - last < 60:
            return
        self._last_subscribe_attempt[device.endpoint] = now

        for xaddr in device.xaddrs:
            if not xaddr.lower().startswith("http://"):
                continue
            if self._try_subscribe(xaddr, device):
                return

    def _try_subscribe(self, xaddr: str, device: DiscoveredScanDevice) -> bool:
        body = subscribe_xml(self.config, xaddr)
        LOGGER.info(
            "attempting WSD ScanAvailableEvent subscription",
            extra={"endpoint": device.endpoint, "xaddr": xaddr},
        )
        response_body = self._post_soap(xaddr, body)
        if response_body is None:
            return False
        info = parse_subscription_info(response_body)
        if info is None:
            return True
        self._subscriptions[device.endpoint] = ActiveSubscription(
            manager_url=info.manager_url,
            identifier=info.identifier,
            expires_at=time.monotonic() + info.expires_seconds,
            destination_token=info.destination_token,
            discovery_instance_id=self._discovery_instance_ids.get(device.endpoint, ""),
        )
        self._save_subscription_state()
        LOGGER.info(
            "WSD scan subscription stored",
            extra={
                "endpoint": device.endpoint,
                "manager_url": info.manager_url,
                "identifier": info.identifier,
                "destination_token": info.destination_token,
                "expires_seconds": info.expires_seconds,
            },
        )
        return True

    def _try_renew(self, endpoint: str, subscription: ActiveSubscription) -> bool:
        LOGGER.info(
            "renewing WSD scan subscription",
            extra={
                "endpoint": endpoint,
                "manager_url": subscription.manager_url,
                "identifier": subscription.identifier,
            },
        )
        response_body = self._post_soap(
            subscription.manager_url,
            renew_xml(subscription.manager_url, subscription.identifier),
        )
        if response_body is None:
            self._subscriptions.pop(endpoint, None)
            return False

        info = parse_subscription_info(response_body)
        expires_seconds = (
            info.expires_seconds if info else parse_duration_seconds(SUBSCRIBE_EXPIRES)
        )
        self._subscriptions[endpoint] = ActiveSubscription(
            manager_url=subscription.manager_url,
            identifier=subscription.identifier,
            expires_at=time.monotonic() + expires_seconds,
            destination_token=subscription.destination_token,
            discovery_instance_id=subscription.discovery_instance_id,
        )
        self._save_subscription_state()
        LOGGER.info(
            "WSD scan subscription renewed",
            extra={"endpoint": endpoint, "expires_seconds": expires_seconds},
        )
        return True

    def _run_push_scan_job(self, event: ScanAvailableEvent) -> None:
        subscription = self._find_subscription_for_event(event)
        if subscription is None:
            LOGGER.warning(
                "no active WSD scan subscription available for ScanAvailableEvent",
                extra={"scan_identifier": event.scan_identifier},
            )
            return
        if not subscription.destination_token:
            LOGGER.warning(
                "active WSD scan subscription has no DestinationToken",
                extra={
                    "manager_url": subscription.manager_url,
                    "scan_identifier": event.scan_identifier,
                },
            )
            return

        LOGGER.info(
            "creating WSD scan job after ScanAvailableEvent",
            extra={
                "manager_url": subscription.manager_url,
                "scan_identifier": event.scan_identifier,
            },
        )
        create_body = self._post_soap(
            subscription.manager_url,
            create_scan_job_xml(
                subscription.manager_url,
                scan_identifier=event.scan_identifier,
                destination_token=subscription.destination_token,
                device_name=self.config.device_name,
                from_endpoint=self.config.endpoint_uuid,
                input_source=event.input_source,
            ),
        )
        if create_body is None:
            return
        job = parse_create_scan_job_info(create_body)
        if job is None:
            LOGGER.warning(
                "CreateScanJobResponse did not contain JobId and JobToken",
                extra={"scan_identifier": event.scan_identifier},
            )
            return

        LOGGER.info(
            "retrieving WSD scan image",
            extra={"manager_url": subscription.manager_url, "job_id": job.job_id},
        )
        response = self._post_soap_response(
            subscription.manager_url,
            retrieve_image_xml(
                subscription.manager_url,
                job_id=job.job_id,
                job_token=job.job_token,
            ),
        )
        if response is None:
            return
        body, content_type = response
        self._store_scan_response(body, content_type)

    def _find_subscription_for_event(
        self,
        _event: ScanAvailableEvent,
    ) -> ActiveSubscription | None:
        for subscription in self._subscriptions.values():
            if subscription.destination_token:
                return subscription
        return None

    def _store_scan_response(self, body: bytes, content_type: str) -> None:
        if self.config.debug:
            dump = write_payload(
                self.config.raw_dump_dir,
                "retrieve-image-response",
                guess_extension(content_type, body),
                body,
            )
            LOGGER.info(
                "stored raw RetrieveImage response",
                extra={"path": str(dump), "content_type": content_type, "bytes": len(body)},
            )

        stored = self._store_multipart_scan_parts(body, content_type)
        if stored:
            return

        suffix = guess_extension(content_type, body)
        if suffix in {".pdf", ".jpg", ".png", ".tif"}:
            out_path = write_payload(self.config.output_dir, "scan", suffix, body)
            LOGGER.info(
                "stored retrieved scan payload",
                extra={"path": str(out_path), "content_type": content_type, "bytes": len(body)},
            )
            return

        LOGGER.warning(
            "RetrieveImage response did not contain directly recognized image data",
            extra={"content_type": content_type, "bytes": len(body)},
        )

    def _store_multipart_scan_parts(self, body: bytes, content_type: str) -> int:
        if "multipart/" not in content_type.lower():
            return 0
        header = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode()
        message = BytesParser(policy=policy.default).parsebytes(header + body)
        stored = 0
        for part in message.iter_parts():
            part_content_type = part.get_content_type()
            payload = part.get_payload(decode=True) or b""
            suffix = guess_extension(part_content_type, payload)
            if suffix not in {".pdf", ".jpg", ".png", ".tif"}:
                continue
            out_path = write_payload(self.config.output_dir, "scan", suffix, payload)
            stored += 1
            LOGGER.info(
                "stored multipart scan payload",
                extra={
                    "path": str(out_path),
                    "content_type": part_content_type,
                    "bytes": len(payload),
                },
            )
        return stored

    def _unsubscribe_all(self) -> None:
        for endpoint, subscription in list(self._subscriptions.items()):
            LOGGER.info(
                "unsubscribing WSD scan subscription",
                extra={
                    "endpoint": endpoint,
                    "manager_url": subscription.manager_url,
                    "identifier": subscription.identifier,
                },
            )
            self._post_soap(
                subscription.manager_url,
                unsubscribe_xml(subscription.manager_url, subscription.identifier),
            )
            self._subscriptions.pop(endpoint, None)
        self._save_subscription_state()

    def _load_subscription_state(self) -> None:
        try:
            raw = self._subscription_state_path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError):
            return

        now_epoch = time.time()
        now_mono = time.monotonic()
        for endpoint, item in data.items():
            try:
                expires_at_epoch = float(item["expires_at_epoch"])
                manager_url = str(item["manager_url"])
                identifier = str(item.get("identifier", ""))
                destination_token = str(item.get("destination_token", ""))
                discovery_instance_id = str(item.get("discovery_instance_id", ""))
            except (KeyError, TypeError, ValueError):
                continue
            if not destination_token:
                continue
            remaining = expires_at_epoch - now_epoch
            if remaining <= SUBSCRIPTION_RENEW_MARGIN_SECONDS:
                continue
            self._subscriptions[str(endpoint)] = ActiveSubscription(
                manager_url=manager_url,
                identifier=identifier,
                expires_at=now_mono + remaining,
                destination_token=destination_token,
                discovery_instance_id=discovery_instance_id,
            )
            if discovery_instance_id:
                self._discovery_instance_ids[str(endpoint)] = discovery_instance_id

        if self._subscriptions:
            LOGGER.info(
                "loaded persisted WSD scan subscriptions",
                extra={"count": len(self._subscriptions)},
            )

    def _save_subscription_state(self) -> None:
        data = {}
        now_mono = time.monotonic()
        now_epoch = time.time()
        for endpoint, subscription in self._subscriptions.items():
            remaining = subscription.expires_at - now_mono
            if remaining <= 0:
                continue
            data[endpoint] = {
                "manager_url": subscription.manager_url,
                "identifier": subscription.identifier,
                "destination_token": subscription.destination_token,
                "expires_at_epoch": now_epoch + remaining,
                "discovery_instance_id": subscription.discovery_instance_id,
            }

        try:
            self._subscription_state_path.parent.mkdir(parents=True, exist_ok=True)
            if data:
                self._subscription_state_path.write_text(
                    json.dumps(data, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
            else:
                self._subscription_state_path.unlink(missing_ok=True)
        except OSError:
            LOGGER.warning(
                "failed to persist WSD scan subscription state",
                extra={"path": str(self._subscription_state_path)},
                exc_info=True,
            )

    def _post_soap(self, url: str, body: bytes) -> bytes | None:
        response = self._post_soap_response(url, body)
        if response is None:
            return None
        response_body, _content_type = response
        return response_body

    def _post_soap_response(self, url: str, body: bytes) -> tuple[bytes, str] | None:
        request = Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/soap+xml; charset=utf-8",
                "Connection": "close",
                "User-Agent": "wsd-scan-receiver/0.1",
            },
            method="POST",
        )
        if self.config.debug:
            action = self._soap_action_from_body(body)
            dump = write_payload(
                self.config.raw_dump_dir,
                f"outgoing-{action}",
                ".xml",
                body,
            )
            LOGGER.info(
                "stored outgoing SOAP request",
                extra={"path": str(dump), "url": url, "action": action, "bytes": len(body)},
            )
        try:
            with urlopen(request, timeout=8) as response:
                response_body = response.read()
                content_type = response.headers.get("Content-Type", "application/octet-stream")
                LOGGER.info(
                    "SOAP HTTP response received",
                    extra={
                        "url": url,
                        "status": response.status,
                        "content_type": content_type,
                        "body": response_body.decode("utf-8", "replace"),
                    },
                )
                if 200 <= response.status < 300:
                    return response_body, content_type
        except HTTPError as exc:
            error_body = exc.read()
            content_type = exc.headers.get("Content-Type", "application/octet-stream")
            LOGGER.warning(
                "SOAP HTTP request failed",
                extra={
                    "url": url,
                    "status": exc.code,
                    "content_type": content_type,
                    "body": error_body.decode("utf-8", "replace"),
                },
            )
            if self.config.debug:
                dump = write_payload(
                    self.config.raw_dump_dir,
                    "soap-error-response",
                    guess_extension(content_type, error_body),
                    error_body,
                )
                LOGGER.info(
                    "stored SOAP HTTP error response",
                    extra={
                        "path": str(dump),
                        "url": url,
                        "status": exc.code,
                        "bytes": len(error_body),
                    },
                )
        except URLError as exc:
            LOGGER.warning(
                "SOAP HTTP request failed",
                extra={"url": url, "error": str(exc)},
            )
        return None

    def _soap_action_from_body(self, body: bytes) -> str:
        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            return "soap"
        action = _text(_first_descendant(root, "Action"))
        if not action:
            return "soap"
        return action.rsplit("/", 1)[-1].lower() or "soap"
