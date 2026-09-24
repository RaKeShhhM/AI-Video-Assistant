import unittest
from unittest.mock import Mock, patch

import requests
from yt_dlp.networking.exceptions import RequestError
from utils.youtube_network import PublicHTTPSConnection, RestrictedYoutubeDL, validate_destination


class YouTubeNetworkTests(unittest.TestCase):
    def test_destination_allowlist(self):
        for url in ["https://www.youtube.com/watch?v=test", "https://rr1.googlevideo.com/videoplayback", "https://i.ytimg.com/image", "https://youtubei.googleapis.com/youtubei/v1/player"]:
            validate_destination(url)
        for url in ["https://127.0.0.1/", "https://[::1]/", "https://evilgooglevideo.com/", "https://www.youtube.com.evil.example/", "http://www.youtube.com/", "file:///tmp/video", "https://www.youtube.com:8000/", "https://user@www.youtube.com/", "https://example.com/"]:
            with self.subTest(url=url), self.assertRaises(RequestError):
                validate_destination(url)

    def test_dns_private_loopback_metadata_and_multicast_are_blocked_before_connect(self):
        for ip in ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "fc00::1", "224.0.0.1", "0.0.0.0"]:
            with self.subTest(ip=ip), patch("utils.youtube_network.socket.getaddrinfo", return_value=[(2, 1, 6, "", (ip, 443))]):
                with patch("utils.youtube_network.socket.socket") as sock:
                    with self.assertRaises(RequestError):
                        PublicHTTPSConnection("www.youtube.com")._new_conn()
                    sock.assert_not_called()

    def test_connection_uses_validated_numeric_address_without_second_dns_lookup(self):
        address = ("142.250.190.78", 443)
        with patch("utils.youtube_network.socket.getaddrinfo", return_value=[(2, 1, 6, "", address)]) as dns:
            with patch("utils.youtube_network.socket.socket") as factory:
                connection = PublicHTTPSConnection("www.youtube.com", timeout=5)
                self.assertIs(connection._new_conn(), factory.return_value)
                factory.return_value.connect.assert_called_once_with(address)
                dns.assert_called_once()
                self.assertEqual(connection.host, "www.youtube.com")

    def test_redirect_is_rejected_before_sending_to_untrusted_host(self):
        with RestrictedYoutubeDL({"quiet": True, "proxy": ""}) as ydl:
            handlers = list(ydl._request_director.handlers.values())
            self.assertEqual(len(handlers), 1)
            session = handlers[0]._get_instance(cookiejar=ydl.cookiejar)
            response = requests.Response()
            response.status_code = 302
            response.headers["Location"] = "https://127.0.0.1/private"
            response.url = "https://www.youtube.com/watch?v=BaW_jenozKc"
            response._content = b""
            response.raw = Mock(_original_response=None)
            response.request = requests.Request("GET", response.url).prepare()
            with patch("requests.adapters.HTTPAdapter.send", return_value=response) as send:
                with self.assertRaises(RequestError):
                    session.get(response.url)
                self.assertEqual(send.call_count, 1)
