"""SOAP helpers and DPWS/WS-Scan routing.

The handlers in this module intentionally cover only the stable discovery and
metadata pieces plus conservative responses for common WS-Transfer,
WS-MetadataExchange, and WS-Scan actions. Real push-scan payload behavior varies
by scanner firmware, so unknown requests are logged and answered without
crashing the service.
"""

from __future__ import annotations

import html
import logging
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from . import __version__
from .config import Config

LOGGER = logging.getLogger(__name__)

SOAP12 = "http://www.w3.org/2003/05/soap-envelope"
WSA = "http://www.w3.org/2005/08/addressing"
WSA_2004 = "http://schemas.xmlsoap.org/ws/2004/08/addressing"
WSD = "http://schemas.xmlsoap.org/ws/2005/04/discovery"
WST = "http://schemas.xmlsoap.org/ws/2004/09/transfer"
MEX = "http://schemas.xmlsoap.org/ws/2004/09/mex"
DPWS = "http://schemas.xmlsoap.org/ws/2006/02/devprof"
WSCN = "http://schemas.microsoft.com/windows/2006/08/wdp/scan"
PNPX = "http://schemas.microsoft.com/windows/pnpx/2005/10"
PUB = "http://schemas.microsoft.com/windows/pub/2005/07"
WORKGROUP = "WORKGROUP"

ET.register_namespace("s", SOAP12)
ET.register_namespace("a", WSA)
ET.register_namespace("d", WSD)
ET.register_namespace("wst", WST)
ET.register_namespace("mex", MEX)
ET.register_namespace("dpws", DPWS)
ET.register_namespace("wscn", WSCN)
ET.register_namespace("pnpx", PNPX)
ET.register_namespace("pub", PUB)


@dataclass(frozen=True)
class SoapRequest:
    """Parsed SOAP envelope fields useful for routing."""

    action: str | None
    message_id: str | None
    relates_to: str | None
    body_tag: str | None
    raw_body_xml: str
    addressing_ns: str


def _qname(ns: str, tag: str) -> str:
    return f"{{{ns}}}{tag}"


def _text(root: ET.Element, path: str) -> str | None:
    item = root.find(path)
    if item is None or item.text is None:
        return None
    return item.text.strip()


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _namespace(tag: str) -> str:
    if tag.startswith("{"):
        return tag[1:].split("}", 1)[0]
    return ""


def _first_child(element: ET.Element | None, local_name: str) -> ET.Element | None:
    if element is None:
        return None
    for child in element:
        if _local_name(child.tag) == local_name:
            return child
    return None


def _child_text(element: ET.Element | None, local_name: str) -> str | None:
    child = _first_child(element, local_name)
    if child is None or child.text is None:
        return None
    return child.text.strip()


def parse_soap_envelope(payload: bytes) -> SoapRequest:
    """Parse a SOAP envelope and extract headers without assuming an action set."""
    root = ET.fromstring(payload)
    header = _first_child(root, "Header")
    action_node = _first_child(header, "Action")
    action = action_node.text.strip() if action_node is not None and action_node.text else None
    message_id = _child_text(header, "MessageID")
    relates_to = _child_text(header, "RelatesTo")
    addressing_ns = _namespace(action_node.tag) if action_node is not None else WSA
    body = _first_child(root, "Body")
    first_child = body[0] if body is not None and len(body) else None
    raw_body_xml = ET.tostring(body, encoding="unicode") if body is not None else ""
    return SoapRequest(
        action=action,
        message_id=message_id,
        relates_to=relates_to,
        body_tag=first_child.tag if first_child is not None else None,
        raw_body_xml=raw_body_xml,
        addressing_ns=addressing_ns or WSA,
    )


def soap_envelope(
    action: str,
    body_xml: str,
    *,
    relates_to: str | None = None,
    addressing_ns: str = WSA,
) -> bytes:
    """Build a SOAP 1.2 response envelope."""
    message_id = f"urn:uuid:{uuid.uuid4()}"
    relates = f"<a:RelatesTo>{html.escape(relates_to)}</a:RelatesTo>" if relates_to else ""
    anonymous = (
        "http://schemas.xmlsoap.org/ws/2004/08/addressing/role/anonymous"
        if addressing_ns == WSA_2004
        else "http://www.w3.org/2005/08/addressing/anonymous"
    )
    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<s:Envelope
    xmlns:s="{SOAP12}"
    xmlns:a="{addressing_ns}"
    xmlns:wst="{WST}"
    xmlns:mex="{MEX}"
    xmlns:dpws="{DPWS}"
    xmlns:wscn="{WSCN}"
    xmlns:pnpx="{PNPX}"
    xmlns:pub="{PUB}">
  <s:Header>
    <a:To>{html.escape(anonymous)}</a:To>
    <a:Action>{html.escape(action)}</a:Action>
    <a:MessageID>{message_id}</a:MessageID>
    {relates}
  </s:Header>
  <s:Body>
    {body_xml}
  </s:Body>
</s:Envelope>"""
    return xml.encode("utf-8")


def device_metadata_xml(config: Config) -> str:
    """Return DPWS metadata describing this host as a scan destination computer."""
    computer_name = config.device_name
    return f"""<mex:Metadata>
  <mex:MetadataSection Dialect="http://schemas.xmlsoap.org/ws/2006/02/devprof/ThisDevice">
    <dpws:ThisDevice>
      <dpws:FriendlyName>{html.escape(config.device_name)}</dpws:FriendlyName>
      <dpws:FirmwareVersion>{html.escape(__version__)}</dpws:FirmwareVersion>
      <dpws:SerialNumber>{html.escape(config.endpoint_uuid)}</dpws:SerialNumber>
    </dpws:ThisDevice>
  </mex:MetadataSection>
  <mex:MetadataSection Dialect="http://schemas.xmlsoap.org/ws/2006/02/devprof/ThisModel">
    <dpws:ThisModel>
      <dpws:Manufacturer>wsd-scan-receiver</dpws:Manufacturer>
      <dpws:ManufacturerUrl>https://example.invalid/wsd-scan-receiver</dpws:ManufacturerUrl>
      <dpws:ModelName>WSD Scan Receiver</dpws:ModelName>
      <dpws:ModelNumber>{html.escape(__version__)}</dpws:ModelNumber>
      <pnpx:DeviceCategory>Computers</pnpx:DeviceCategory>
      <dpws:PresentationUrl>{html.escape(config.metadata_url)}</dpws:PresentationUrl>
    </dpws:ThisModel>
  </mex:MetadataSection>
  <mex:MetadataSection Dialect="http://schemas.xmlsoap.org/ws/2006/02/devprof/Relationship">
    <dpws:Relationship Type="http://schemas.xmlsoap.org/ws/2006/02/devprof/host">
      <dpws:Host>
        <a:EndpointReference><a:Address>{html.escape(config.endpoint_uuid)}</a:Address></a:EndpointReference>
        <dpws:Types>pub:Computer</dpws:Types>
        <dpws:ServiceId>{html.escape(config.endpoint_uuid)}</dpws:ServiceId>
        <pub:Computer>{html.escape(computer_name)}/Workgroup:{WORKGROUP}</pub:Computer>
      </dpws:Host>
    </dpws:Relationship>
  </mex:MetadataSection>
</mex:Metadata>"""


def route_soap_request(request: SoapRequest, config: Config) -> tuple[int, bytes, str]:
    """Route known SOAP actions to conservative XML responses."""
    action = request.action or ""
    LOGGER.info(
        "routing SOAP action",
        extra={"soap_action": action, "body_tag": request.body_tag},
    )

    if action.endswith("/Get") or request.body_tag == _qname(WST, "Get"):
        return (
            200,
            soap_envelope(
                f"{WST}/GetResponse",
                device_metadata_xml(config),
                relates_to=request.message_id,
                addressing_ns=request.addressing_ns,
            ),
            "application/soap+xml; charset=utf-8",
        )

    if "GetMetadata" in action or request.body_tag == _qname(MEX, "GetMetadata"):
        return (
            200,
            soap_envelope(
                f"{MEX}/GetMetadata/Response",
                device_metadata_xml(config),
                relates_to=request.message_id,
                addressing_ns=request.addressing_ns,
            ),
            "application/soap+xml; charset=utf-8",
        )

    if "ScanAvailableEvent" in action or (
        request.body_tag is not None and _local_name(request.body_tag) == "ScanAvailableEvent"
    ):
        LOGGER.info(
            "received WSD ScanAvailableEvent notification",
            extra={"body": request.raw_body_xml},
        )
        return (202, b"", "application/soap+xml; charset=utf-8")

    fault = """<s:Fault>
  <s:Code><s:Value>s:Sender</s:Value></s:Code>
  <s:Reason><s:Text xml:lang="en">Unsupported WSD action</s:Text></s:Reason>
</s:Fault>"""
    return (
        500,
        soap_envelope(
            f"{SOAP12}/fault",
            fault,
            relates_to=request.message_id,
            addressing_ns=request.addressing_ns,
        ),
        "application/soap+xml; charset=utf-8",
    )
