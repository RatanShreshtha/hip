import time

import pytest

from dummyserver.server import HAS_IPV6
from dummyserver.testcase import HTTPDummyServerTestCase, IPv6HTTPDummyServerTestCase
from hip.poolmanager import PoolManager
from hip.exceptions import MaxRetryError, NewConnectionError
from hip.util.retry import Retry, RequestHistory

# Retry failed tests
pytestmark = pytest.mark.flaky


class TestRetry(HTTPDummyServerTestCase):
    @classmethod
    def setup_class(self):
        super(TestRetry, self).setup_class()
        self.base_url = "http://%s:%d" % (self.host, self.port)
        self.base_url_alt = "http://%s:%d" % (self.host_alt, self.port)

    def test_max_retry(self):
        with PoolManager() as http:
            with pytest.raises(MaxRetryError):
                http.request(
                    "GET",
                    "%s/redirect" % self.base_url,
                    fields={"target": "/"},
                    retries=0,
                )

    def test_disabled_retry(self):
        """ Disabled retries should disable redirect handling. """
        with PoolManager() as http:
            r = http.request(
                "GET",
                "%s/redirect" % self.base_url,
                fields={"target": "/"},
                retries=False,
            )
            assert r.status == 303

            r = http.request(
                "GET",
                "%s/redirect" % self.base_url,
                fields={"target": "/"},
                retries=Retry(redirect=False),
            )
            assert r.status == 303

            with pytest.raises(NewConnectionError):
                http.request(
                    "GET",
                    "http://thishostdoesnotexist.invalid/",
                    timeout=0.001,
                    retries=False,
                )

    def test_read_retries(self):
        """ Should retry for status codes in the whitelist """
        retry = Retry(read=1, status_forcelist=[418])

        with PoolManager() as http:
            resp = http.request(
                "GET",
                "%s/successful_retry" % self.base_url,
                headers={"test-name": "test_read_retries"},
                retries=retry,
            )
            assert resp.status == 200

    def test_read_total_retries(self):
        """ HTTP response w/ status code in the whitelist should be retried """
        headers = {"test-name": "test_read_total_retries"}
        retry = Retry(total=1, status_forcelist=[418])

        with PoolManager() as http:
            resp = http.request(
                "GET",
                "%s/successful_retry" % self.base_url,
                headers=headers,
                retries=retry,
            )
            assert resp.status == 200

    def test_retries_wrong_whitelist(self):
        """HTTP response w/ status code not in whitelist shouldn't be retried"""
        retry = Retry(total=1, status_forcelist=[202])

        with PoolManager() as http:
            resp = http.request(
                "GET",
                "%s/successful_retry" % self.base_url,
                headers={"test-name": "test_wrong_whitelist"},
                retries=retry,
            )
            assert resp.status == 418

    def test_default_method_whitelist_retried(self):
        """Hip should retry methods in the default method whitelist"""
        retry = Retry(total=1, status_forcelist=[418])

        with PoolManager() as http:
            resp = http.request(
                "OPTIONS",
                "%s/successful_retry" % self.base_url,
                headers={"test-name": "test_default_whitelist"},
                retries=retry,
            )
            assert resp.status == 200

    def test_retries_wrong_method_list(self):
        """Method not in our whitelist should not be retried, even if code matches"""
        headers = {"test-name": "test_wrong_method_whitelist"}
        retry = Retry(total=1, status_forcelist=[418], method_whitelist=["POST"])

        with PoolManager() as http:
            resp = http.request(
                "GET",
                "%s/successful_retry" % self.base_url,
                headers=headers,
                retries=retry,
            )
            assert resp.status == 418

    def test_read_retries_unsuccessful(self):
        headers = {"test-name": "test_read_retries_unsuccessful"}

        with PoolManager() as http:
            resp = http.request(
                "GET", "%s/successful_retry" % self.base_url, headers=headers, retries=1
            )
            assert resp.status == 418

    def test_retry_reuse_safe(self):
        """ It should be possible to reuse a Retry object across requests """
        headers = {"test-name": "test_retry_safe"}
        retry = Retry(total=1, status_forcelist=[418])

        with PoolManager() as http:
            resp = http.request(
                "GET",
                "%s/successful_retry" % self.base_url,
                headers=headers,
                retries=retry,
            )
            assert resp.status == 200
            resp = http.request(
                "GET",
                "%s/successful_retry" % self.base_url,
                headers=headers,
                retries=retry,
            )
            assert resp.status == 200

    def test_retry_return_in_response(self):
        headers = {"test-name": "test_retry_return_in_response"}
        retry = Retry(total=2, status_forcelist=[418])

        with PoolManager() as http:
            resp = http.request(
                "GET",
                "%s/successful_retry" % self.base_url,
                headers=headers,
                retries=retry,
            )
            assert resp.status == 200
            assert resp.retries.total == 1
            assert resp.retries.history == (
                RequestHistory("GET", "/successful_retry", None, 418, None),
            )

    def test_retry_redirect_history(self):
        with PoolManager() as http:
            resp = http.request(
                "GET", "%s/redirect" % self.base_url, fields={"target": "/"}
            )
            assert resp.status == 200
            assert resp.retries.history == (
                RequestHistory(
                    "GET", self.base_url + "/redirect?target=%2F", None, 303, "/"
                ),
            )

    def test_multi_redirect_history(self):
        with PoolManager() as http:
            r = http.request(
                "GET",
                "%s/multi_redirect" % self.base_url,
                fields={"redirect_codes": "303,302,200"},
                redirect=False,
            )
            assert r.status == 303
            assert r.retries.history == tuple()

            r = http.request(
                "GET",
                "%s/multi_redirect" % self.base_url,
                retries=10,
                fields={"redirect_codes": "303,302,301,307,302,200"},
            )
            assert r.status == 200
            assert r.data == b"Done redirecting"

            expected = [
                (303, "/multi_redirect?redirect_codes=302,301,307,302,200"),
                (302, "/multi_redirect?redirect_codes=301,307,302,200"),
                (301, "/multi_redirect?redirect_codes=307,302,200"),
                (307, "/multi_redirect?redirect_codes=302,200"),
                (302, "/multi_redirect?redirect_codes=200"),
            ]
            actual = [
                (history.status, history.redirect_location)
                for history in r.retries.history
            ]
            assert actual == expected


class TestRetryAfter(HTTPDummyServerTestCase):
    @classmethod
    def setup_class(self):
        super(TestRetryAfter, self).setup_class()
        self.base_url = "http://%s:%d" % (self.host, self.port)
        self.base_url_alt = "http://%s:%d" % (self.host_alt, self.port)

    def test_retry_after(self):
        url = "%s/retry_after" % self.base_url
        with PoolManager() as http:
            # Request twice in a second to get a 429 response.
            r = http.request(
                "GET", url, fields={"status": "429 Too Many Requests"}, retries=False
            )
            r = http.request(
                "GET", url, fields={"status": "429 Too Many Requests"}, retries=False
            )
            assert r.status == 429

            r = http.request(
                "GET", url, fields={"status": "429 Too Many Requests"}, retries=True
            )
            assert r.status == 200

            # Request twice in a second to get a 503 response.
            r = http.request(
                "GET", url, fields={"status": "503 Service Unavailable"}, retries=False
            )
            r = http.request(
                "GET", url, fields={"status": "503 Service Unavailable"}, retries=False
            )
            assert r.status == 503

            r = http.request(
                "GET", url, fields={"status": "503 Service Unavailable"}, retries=True
            )
            assert r.status == 200

            # Ignore Retry-After header on status which is not defined in
            # Retry.RETRY_AFTER_STATUS_CODES.
            r = http.request(
                "GET", url, fields={"status": "418 I'm a teapot"}, retries=True
            )
            assert r.status == 418

    def test_redirect_after(self):
        with PoolManager() as http:
            r = http.request("GET", "%s/redirect_after" % self.base_url, retries=False)
            assert r.status == 303

            t = time.time()
            r = http.request("GET", "%s/redirect_after" % self.base_url)
            assert r.status == 200
            delta = time.time() - t
            assert delta >= 1

            t = time.time()
            timestamp = t + 2
            r = http.request(
                "GET", self.base_url + "/redirect_after?date=" + str(timestamp)
            )
            assert r.status == 200
            delta = time.time() - t
            assert delta >= 1

            # Retry-After is past
            t = time.time()
            timestamp = t - 1
            r = http.request(
                "GET", self.base_url + "/redirect_after?date=" + str(timestamp)
            )
            delta = time.time() - t
            assert r.status == 200
            assert delta < 1


@pytest.mark.skipif(not HAS_IPV6, reason="IPv6 is not supported on this system")
class TestIPv6PoolManager(IPv6HTTPDummyServerTestCase):
    @classmethod
    def setup_class(cls):
        super(TestIPv6PoolManager, cls).setup_class()
        cls.base_url = "http://[%s]:%d" % (cls.host, cls.port)

    def test_ipv6(self):
        with PoolManager() as http:
            http.request("GET", self.base_url)
