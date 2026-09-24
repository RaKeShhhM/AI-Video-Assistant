"""YouTube-only HTTPS transport, including redirects and resolved IP addresses.

This adapter integrates with yt-dlp's networking API. Keep its regression tests
when upgrading the pinned yt-dlp dependency. No global socket/session patches.
"""
import ipaddress
import socket
from urllib.parse import urlsplit

from urllib3.connection import HTTPSConnection
from urllib3.connectionpool import HTTPSConnectionPool
from urllib3.exceptions import NewConnectionError
from yt_dlp import YoutubeDL
from yt_dlp.networking._requests import RequestsRH
from yt_dlp.networking.exceptions import RequestError


def validate_destination(url):
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    allowed = (
        host in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtubei.googleapis.com"}
        or host.endswith(".googlevideo.com")
        or host.endswith(".ytimg.com")
    )
    if (parsed.scheme != "https" or not allowed or parsed.port not in (None, 443)
            or parsed.username is not None or parsed.password is not None):
        raise RequestError("Blocked YouTube request to an unsupported destination.")


class PublicHTTPSConnection(HTTPSConnection):
    def _new_conn(self):
        # Resolve once and connect to that exact numeric sockaddr. A second DNS
        # lookup after validation would permit DNS rebinding to a private IP.
        validate_destination(f"https://{self.host}:{self.port or 443}")
        try:
            addresses = socket.getaddrinfo(self.host, self.port or 443, type=socket.SOCK_STREAM)
            if not addresses:
                raise OSError("No addresses returned")
            for _, _, _, _, address in addresses:
                ip = ipaddress.ip_address(address[0])
                if not ip.is_global or ip.is_multicast or ip.is_reserved:
                    raise RequestError("Blocked YouTube request to a non-public IP address.")
            last_error = None
            for family, kind, protocol, _, address in addresses:
                connection = socket.socket(family, kind, protocol)
                try:
                    if self.timeout is None or isinstance(self.timeout, (int, float)):
                        connection.settimeout(self.timeout)
                    for option in self.socket_options or []:
                        connection.setsockopt(*option)
                    if self.source_address:
                        connection.bind(self.source_address)
                    connection.connect(address)
                    return connection
                except OSError as exc:
                    last_error = exc
                    connection.close()
            raise last_error
        except OSError as exc:
            raise NewConnectionError(self, "Could not connect to YouTube's public address") from exc
        # HTTPSConnection.connect still applies normal hostname/certificate
        # verification and uses the original hostname for TLS SNI.


class PublicHTTPSConnectionPool(HTTPSConnectionPool):
    ConnectionCls = PublicHTTPSConnection


class RestrictedRequestsRH(RequestsRH):
    def _create_instance(self, cookiejar, legacy_ssl_support=None):
        session = super()._create_instance(cookiejar, legacy_ssl_support)
        adapter = session.get_adapter("https://")
        # Copy before changing: urllib3's default mapping is shared globally.
        adapter.poolmanager.pool_classes_by_scheme = {
            "https": PublicHTTPSConnectionPool,
        }
        send = session.send

        def guarded_send(request, **kwargs):
            validate_destination(request.url)
            if any((kwargs.get("proxies") or {}).values()):
                raise RequestError("Proxies are disabled for YouTube downloads.")
            return send(request, **kwargs)

        # requests calls session.send again for every redirect target.
        session.send = guarded_send
        session.max_redirects = 5
        return session


class RestrictedYoutubeDL(YoutubeDL):
    def build_request_director(self, handlers, preferences=None):
        # No fallback handler may bypass the allowlist or IP checks.
        return super().build_request_director([RestrictedRequestsRH])
