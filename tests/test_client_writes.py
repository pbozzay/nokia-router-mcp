import unittest
from unittest.mock import patch

import requests

from nokia_router_mcp.client import NokiaRouter, RouterConfig, RouterError


class ClientWriteTests(unittest.TestCase):
    def test_redirect_does_not_replay_post(self):
        sent = []

        class RedirectAdapter(requests.adapters.BaseAdapter):
            def send(self, req, **kwargs):
                sent.append(req)
                response = requests.Response()
                response.request = req
                response.url = req.url
                response.status_code = 307 if len(sent) == 1 else 200
                response.headers["Location"] = "/redirected_web_app.cgi"
                response._content = b'{"result": 0}'
                return response

            def close(self):
                pass

        router = NokiaRouter(RouterConfig())
        router.http.mount("http://", RedirectAdapter())
        with patch.object(router, "ensure_session"):
            with self.assertRaises(RouterError):
                router.post("dns_web_app.cgi?add", "domainName=app.test&domainAddr=192.168.1.9")
        self.assertEqual(len(sent), 1)

    def test_ambiguous_html_response_does_not_replay_post(self):
        router = NokiaRouter(RouterConfig(username="test", password="test"))
        response = requests.Response()
        response.status_code = 200
        response._content = b"<html>Session expired</html>"
        with patch.object(router, "ensure_session"), patch.object(router, "_csrf_body", return_value="encrypted"), \
             patch.object(router, "_raw_post", return_value=response) as send, patch.object(router, "login") as login:
            with self.assertRaises(RouterError):
                router.post("dns_web_app.cgi?add", "domainName=app.test&domainAddr=192.168.1.9")
            self.assertEqual(send.call_count, 1)
            self.assertEqual(login.call_count, 0)

    def test_timeout_is_not_replayed(self):
        router = NokiaRouter(RouterConfig())
        with patch.object(router, "ensure_session"), patch.object(router, "_raw_post", side_effect=requests.Timeout) as send:
            with self.assertRaises(requests.Timeout):
                router.post("dns_web_app.cgi?add", "domainName=app.test&domainAddr=192.168.1.9")
            self.assertEqual(send.call_count, 1)
