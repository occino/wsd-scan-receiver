"""WS-Discovery listener and announcement generation."""

from __future__ import annotations

import html
import logging
import select
import socket
import struct
import threading
import time
import uuid
import xml.etree.ElementTree as ET
import zlib
from collections.abc import Callable
from dataclasses import dataclass

from .config import Config
from .soap import PUB, SOAP12, WSA, WSD

LOGGER = logging.getLogger(__name__)
MULTICAST_GROUP = "239.255.255.250"
MULTICAST_GROUP_V6 = "ff02::c"
DISCOVERY_PORT = 3702
WSD_OASIS = "http://docs.oasis-open.org/ws-dd/ns/discovery/2009/01"
WSD_NAMESPACES = (WSD, WSD_OASIS)
DISCOVERY_TO_URIS = {
    WSD: "urn:schemas-xmlsoap-org:ws:2005:04:discovery",
    WSD_OASIS: "urn:docs-oasis-open-org:ws-dd:ns:discovery:2009:01",
}
WSA_ANONYMOUS = {
    WSA: "http://www.w3.org/2005/08/addressing/anonymous",
    "http://schemas.xmlsoap.org/ws/2004/08/addressing": (
        "http://schemas.xmlsoap.org/ws/2004/08/addressing/role/anonymous"
    ),
}
HELLO_INTERVAL_SECONDS = 60.0
DISCOVERY_TYPES = "dpws:Device pub:Computer"


@dataclass(frozen=True)
class DiscoveryMessage:
    """Relevant fields from a WS-Discovery Probe or Resolve message."""

    kind: str
    message_id: str | None
    types: str
    address: str | None
    discovery_ns: str
    addressing_ns: str


Probe = DiscoveryMessage


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


def _first_descendant(element: ET.Element, local_name: str) -> ET.Element | None:
    for child in element.iter():
        if _local_name(child.tag) == local_name:
            return child
    return None


def _text(element: ET.Element | None) -> str | None:
    if element is None or element.text is None:
        return None
    return element.text.strip()


def parse_probe(payload: bytes) -> Probe | None:
    """Return Probe data if the datagram is a WS-Discovery Probe."""
    message = parse_discovery_message(payload)
    if message is None or message.kind != "Probe":
        return None
    return message


def parse_discovery_message(payload: bytes) -> DiscoveryMessage | None:
    """Return Probe or Resolve data from either common WS-Discovery namespace."""
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return None

    body = _first_child(root, "Body")
    request = _first_child(body, "Probe")
    if request is None:
        request = _first_child(body, "Resolve")
    if request is None:
        return None

    discovery_ns = _namespace(request.tag)
    if discovery_ns not in WSD_NAMESPACES:
        return None

    header = _first_child(root, "Header")
    message_id = _text(_first_child(header, "MessageID"))
    addressing_ns = _namespace(_first_child(header, "MessageID").tag) if message_id else WSA
    types = _text(_first_child(request, "Types")) or ""
    address = _text(_first_descendant(request, "Address"))
    return DiscoveryMessage(
        kind=_local_name(request.tag),
        message_id=message_id,
        types=types,
        address=address,
        discovery_ns=discovery_ns,
        addressing_ns=addressing_ns or WSA,
    )


def is_relevant_probe(probe: Probe) -> bool:
    """Decide whether a Probe is likely looking for this computer host."""
    if not probe.types:
        return True
    type_tokens = {token.strip().lower() for token in probe.types.split() if token.strip()}
    for type_token in type_tokens:
        if type_token in {"dpws:device", "wsdp:device", "pub:computer"}:
            return True
        if type_token.endswith(":computer"):
            return True
        if type_token.endswith(":device") and not type_token.startswith("wscn:"):
            return True
    return False


def probe_match_xml(
    config: Config,
    *,
    relates_to: str | None = None,
    discovery_ns: str = WSD,
    addressing_ns: str = WSA,
    xaddrs: str | None = None,
) -> bytes:
    """Build a WS-Discovery ProbeMatches response for the configured device."""
    return discovery_match_xml(
        config,
        "Probe",
        relates_to=relates_to,
        discovery_ns=discovery_ns,
        addressing_ns=addressing_ns,
        xaddrs=xaddrs,
    )


def discovery_payload_xml(config: Config, wrapper: str, *, xaddrs: str | None = None) -> str:
    """Build the common WS-Discovery device payload."""
    scopes = (
        "pnpx:DeviceCategory/Computers "
        f"pnpx:ComputerName/{html.escape(config.device_name)} {html.escape(config.device_name)}"
    )
    advertised_xaddrs = xaddrs or config.metadata_url
    metadata_version = zlib.crc32(config.endpoint_uuid.encode("utf-8")) or 1
    return f"""<{wrapper}>
        <a:EndpointReference>
          <a:Address>{html.escape(config.endpoint_uuid)}</a:Address>
        </a:EndpointReference>
        <d:Types>{DISCOVERY_TYPES}</d:Types>
        <d:Scopes>{scopes}</d:Scopes>
        <d:XAddrs>{html.escape(advertised_xaddrs)}</d:XAddrs>
        <d:MetadataVersion>{metadata_version}</d:MetadataVersion>
      </{wrapper}>"""


def discovery_match_xml(
    config: Config,
    request_kind: str,
    *,
    relates_to: str | None = None,
    discovery_ns: str = WSD,
    addressing_ns: str = WSA,
    xaddrs: str | None = None,
    app_sequence: tuple[int, int] = (1, 1),
) -> bytes:
    """Build a WS-Discovery ProbeMatches or ResolveMatches response."""
    message_id = f"urn:uuid:{uuid.uuid4()}"
    relates = f"<a:RelatesTo>{html.escape(relates_to)}</a:RelatesTo>" if relates_to else ""
    to = WSA_ANONYMOUS.get(addressing_ns, WSA_ANONYMOUS[WSA])
    match_kind = f"{request_kind}Match"
    matches_kind = f"{request_kind}Matches"
    action = f"{discovery_ns}/{matches_kind}"
    instance_id, message_number = app_sequence
    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<s:Envelope
    xmlns:s="{SOAP12}"
    xmlns:a="{addressing_ns}"
    xmlns:d="{discovery_ns}"
    xmlns:dpws="http://schemas.xmlsoap.org/ws/2006/02/devprof"
    xmlns:wscn="http://schemas.microsoft.com/windows/2006/08/wdp/scan"
    xmlns:pnpx="http://schemas.microsoft.com/windows/pnpx/2005/10"
    xmlns:pub="{PUB}">
  <s:Header>
    <a:Action>{html.escape(action)}</a:Action>
    <a:MessageID>{message_id}</a:MessageID>
    <a:To>{html.escape(to)}</a:To>
    {relates}
    <d:AppSequence InstanceId="{instance_id}" MessageNumber="{message_number}"/>
  </s:Header>
  <s:Body>
    <d:{matches_kind}>
      {discovery_payload_xml(config, f"d:{match_kind}", xaddrs=xaddrs)}
    </d:{matches_kind}>
  </s:Body>
</s:Envelope>"""
    return xml.encode("utf-8")


def hello_xml(
    config: Config,
    *,
    discovery_ns: str = WSD,
    addressing_ns: str = WSA,
    xaddrs: str | None = None,
    app_sequence: tuple[int, int] = (1, 1),
) -> bytes:
    """Build a multicast WS-Discovery Hello announcement."""
    message_id = f"urn:uuid:{uuid.uuid4()}"
    to = DISCOVERY_TO_URIS.get(discovery_ns, discovery_ns)
    instance_id, message_number = app_sequence
    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<s:Envelope
    xmlns:s="{SOAP12}"
    xmlns:a="{addressing_ns}"
    xmlns:d="{discovery_ns}"
    xmlns:dpws="http://schemas.xmlsoap.org/ws/2006/02/devprof"
    xmlns:wscn="http://schemas.microsoft.com/windows/2006/08/wdp/scan"
    xmlns:pnpx="http://schemas.microsoft.com/windows/pnpx/2005/10"
    xmlns:pub="{PUB}">
  <s:Header>
    <a:Action>{html.escape(discovery_ns)}/Hello</a:Action>
    <a:MessageID>{message_id}</a:MessageID>
    <a:To>{html.escape(to)}</a:To>
    <d:AppSequence InstanceId="{instance_id}" MessageNumber="{message_number}"/>
  </s:Header>
  <s:Body>
    {discovery_payload_xml(config, "d:Hello", xaddrs=xaddrs)}
  </s:Body>
</s:Envelope>"""
    return xml.encode("utf-8")


def is_own_hello(payload: bytes, config: Config) -> bool:
    """Return true for multicast loopback copies of our own Hello messages."""
    return (
        config.endpoint_uuid.encode("utf-8") in payload
        and b"/Hello" in payload
        and config.metadata_url.encode("utf-8") in payload
    )


class DiscoveryService:
    """UDP WS-Discovery responder for multicast Probe messages."""

    def __init__(
        self,
        config: Config,
        discovery_observer: Callable[[bytes, str], None] | None = None,
    ) -> None:
        self.config = config
        self.discovery_observer = discovery_observer
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._sockets: list[socket.socket] = []
        self._ipv6_interfaces: list[int] = []
        self._instance_id = int(time.time())
        self._message_number = 0
        self._last_foreign_hello_response = 0.0

    def _next_app_sequence(self) -> tuple[int, int]:
        self._message_number += 1
        return self._instance_id, self._message_number

    def start(self) -> None:
        self._thread = threading.Thread(target=self._serve, name="ws-discovery", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        for sock in self._sockets:
            sock.close()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _make_ipv4_socket(self) -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                LOGGER.debug("SO_REUSEPORT is unavailable for WS-Discovery IPv4")
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
        sock.bind(("", DISCOVERY_PORT))
        multicast_if = self._ipv4_multicast_interface()
        membership = socket.inet_aton(MULTICAST_GROUP) + socket.inet_aton(multicast_if)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(multicast_if))
        sock.settimeout(1.0)
        LOGGER.info(
            "joined IPv4 WS-Discovery multicast group",
            extra={"group": MULTICAST_GROUP, "interface_ip": multicast_if},
        )
        return sock

    def _ipv4_multicast_interface(self) -> str:
        if self.config.interface:
            interface_ip = self._interface_ipv4_address(self.config.interface)
            if interface_ip:
                return interface_ip
        try:
            socket.inet_aton(self.config.host_ip)
        except OSError:
            return "0.0.0.0"
        return self.config.host_ip

    def _interface_ipv4_address(self, interface: str) -> str | None:
        try:
            import fcntl
        except ImportError:
            return None
        request = struct.pack("256s", interface.encode("utf-8")[:15])
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            result = fcntl.ioctl(probe.fileno(), 0x8915, request)
        except OSError:
            LOGGER.warning(
                "could not resolve IPv4 address for WSD_INTERFACE",
                extra={"interface": interface},
            )
            return None
        finally:
            probe.close()
        return socket.inet_ntoa(result[20:24])

    def _interface_name(self, if_index: int) -> str | None:
        try:
            return socket.if_indextoname(if_index)
        except OSError:
            return None

    def _interface_link_local_ipv6_address(self, if_index: int) -> str | None:
        """Return the first link-local IPv6 address for an interface index."""
        if_name = self._interface_name(if_index)
        if not if_name:
            return None
        try:
            with open("/proc/net/if_inet6", encoding="ascii") as handle:
                for line in handle:
                    parts = line.split()
                    if len(parts) < 6 or parts[5] != if_name:
                        continue
                    raw, _idx, _plen, scope, _flags, _name = parts
                    if scope != "20":
                        continue
                    hextets = [raw[i : i + 4] for i in range(0, 32, 4)]
                    return socket.inet_ntop(
                        socket.AF_INET6,
                        bytes.fromhex("".join(hextets)),
                    )
        except OSError:
            LOGGER.debug("could not read IPv6 interface addresses")
        return None

    def _xaddrs_for_socket(self, sock: socket.socket, addr: tuple[object, ...]) -> str:
        xaddrs = [self.config.metadata_url]
        if sock.family != socket.AF_INET6:
            return " ".join(xaddrs)

        if_index = 0
        if len(addr) >= 4 and isinstance(addr[3], int):
            if_index = addr[3]
        if not if_index and self._ipv6_interfaces:
            if_index = self._ipv6_interfaces[0]

        address = self._interface_link_local_ipv6_address(if_index) if if_index else None
        if_name = self._interface_name(if_index) if if_index else None
        if address and if_name:
            scoped_host = f"[{address}%25{if_name}]"
            ipv6_xaddrs = [
                f"http://{scoped_host}:{self.config.http_port}/metadata",
            ]
            return " ".join(ipv6_xaddrs + xaddrs)
        return " ".join(xaddrs)

    def _make_ipv6_socket(self) -> socket.socket | None:
        try:
            sock = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_MULTICAST_HOPS, 2)
            sock.bind(("::", DISCOVERY_PORT))
        except OSError:
            LOGGER.exception("failed to start IPv6 WS-Discovery listener")
            return None

        group = socket.inet_pton(socket.AF_INET6, MULTICAST_GROUP_V6)
        for if_index in self._candidate_ipv6_interfaces():
            try:
                sock.setsockopt(
                    socket.IPPROTO_IPV6,
                    socket.IPV6_JOIN_GROUP,
                    group + if_index.to_bytes(4, "little"),
                )
                self._ipv6_interfaces.append(if_index)
            except OSError:
                LOGGER.debug(
                    "failed joining IPv6 WS-Discovery multicast group",
                    extra={"if_index": if_index},
                )

        if not self._ipv6_interfaces:
            LOGGER.warning("IPv6 WS-Discovery listener started without multicast memberships")
        sock.setblocking(False)
        return sock

    def _candidate_ipv6_interfaces(self) -> list[int]:
        if self.config.interface:
            try:
                return [socket.if_nametoindex(self.config.interface)]
            except OSError:
                LOGGER.warning(
                    "configured WSD_INTERFACE does not exist; falling back to auto-detection",
                    extra={"interface": self.config.interface},
                )

        interfaces: list[int] = []
        for if_index, if_name in socket.if_nameindex():
            if if_name == "lo" or if_name.startswith(("docker", "br-", "veth")):
                continue
            interfaces.append(if_index)
        return interfaces

    def _serve(self) -> None:
        self._sockets = [self._make_ipv4_socket()]
        ipv6_socket = self._make_ipv6_socket()
        if ipv6_socket is not None:
            self._sockets.append(ipv6_socket)
        LOGGER.info(
            "WS-Discovery listener started",
            extra={
                "udp_port": DISCOVERY_PORT,
                "ipv6": ipv6_socket is not None,
                "ipv6_interfaces": self._ipv6_interfaces,
                "interface": self.config.interface,
            },
        )
        next_hello = 0.0
        while not self._stop.is_set():
            next_hello = self._send_periodic_hello(next_hello)
            try:
                ready, _, _ = select.select(self._sockets, [], [], 1.0)
            except TimeoutError:
                continue
            except OSError:
                if not self._stop.is_set():
                    LOGGER.exception("WS-Discovery select error")
                break

            if not ready:
                continue

            for sock in ready:
                self._receive_datagram(sock)

    def _receive_datagram(self, sock: socket.socket) -> None:
        try:
            payload, addr = sock.recvfrom(65535)
        except BlockingIOError:
            return
        except OSError:
            if not self._stop.is_set():
                LOGGER.exception("WS-Discovery socket error")
            return

        if self.config.debug and not is_own_hello(payload, self.config):
            LOGGER.info(
                "received WS-Discovery datagram",
                extra={
                    "peer": f"{addr[0]}:{addr[1]}",
                    "family": "ipv6" if sock.family == socket.AF_INET6 else "ipv4",
                    "payload": payload.decode("utf-8", "replace"),
                },
            )
        if self.discovery_observer is not None and not is_own_hello(payload, self.config):
            self.discovery_observer(payload, f"{addr[0]}:{addr[1]}")

        message = parse_discovery_message(payload)
        if message is None:
            if b"/Hello" in payload and not is_own_hello(payload, self.config):
                now = time.monotonic()
                if now - self._last_foreign_hello_response > 5:
                    self._last_foreign_hello_response = now
                    LOGGER.info(
                        "received foreign WS-Discovery Hello; announcing receiver immediately",
                        extra={"peer": f"{addr[0]}:{addr[1]}"},
                    )
                    self.send_hello()
            return
        if message.kind == "Probe" and not is_relevant_probe(message):
            LOGGER.debug("ignoring non-scanner Probe", extra={"probe_types": message.types})
            return
        if message.kind == "Resolve" and message.address not in {None, self.config.endpoint_uuid}:
            LOGGER.debug(
                "ignoring Resolve for another endpoint",
                extra={"address": message.address},
            )
            return

        response = discovery_match_xml(
            self.config,
            message.kind,
            relates_to=message.message_id,
            discovery_ns=message.discovery_ns,
            addressing_ns=message.addressing_ns,
            xaddrs=self._xaddrs_for_socket(sock, addr),
            app_sequence=self._next_app_sequence(),
        )
        try:
            sock.sendto(response, addr)
            LOGGER.info(
                "sent WS-Discovery response",
                extra={
                    "peer": f"{addr[0]}:{addr[1]}",
                    "family": "ipv6" if sock.family == socket.AF_INET6 else "ipv4",
                    "kind": message.kind,
                    "probe_types": message.types,
                    "discovery_ns": message.discovery_ns,
                    "message_number": self._message_number,
                },
            )
        except OSError:
            LOGGER.exception("failed sending WS-Discovery response")

    def _send_periodic_hello(self, next_hello: float) -> float:
        now = time.monotonic()
        if next_hello > now:
            return next_hello
        self.send_hello()
        return now + HELLO_INTERVAL_SECONDS

    def send_hello(self) -> None:
        """Send Hello announcements for the supported discovery namespaces."""
        if not self._sockets:
            return
        for sock in self._sockets:
            interfaces = self._ipv6_interfaces if sock.family == socket.AF_INET6 else [0]
            for interface in interfaces:
                self._send_hello_on_socket(sock, interface)

    def _send_hello_on_socket(self, sock: socket.socket, interface: int) -> None:
        for namespace in WSD_NAMESPACES:
            try:
                if sock.family == socket.AF_INET6:
                    destination = (MULTICAST_GROUP_V6, DISCOVERY_PORT, 0, interface)
                    sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_MULTICAST_IF, interface)
                else:
                    destination = (MULTICAST_GROUP, DISCOVERY_PORT)
                xaddrs = None
                if sock.family == socket.AF_INET6:
                    fake_addr = ("::", DISCOVERY_PORT, 0, interface)
                    xaddrs = self._xaddrs_for_socket(sock, fake_addr)
                sock.sendto(
                    hello_xml(
                        self.config,
                        discovery_ns=namespace,
                        xaddrs=xaddrs,
                        app_sequence=self._next_app_sequence(),
                    ),
                    destination,
                )
                LOGGER.info(
                    "sent WS-Discovery Hello",
                    extra={
                        "discovery_ns": namespace,
                        "family": "ipv6" if sock.family == socket.AF_INET6 else "ipv4",
                        "if_index": interface if sock.family == socket.AF_INET6 else None,
                        "message_number": self._message_number,
                    },
                )
            except OSError:
                LOGGER.exception("failed sending WS-Discovery Hello")
