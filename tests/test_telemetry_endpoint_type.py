"""Unit tests for _classify_endpoint (issue #1525)."""

from axonflow.telemetry import _build_payload, _classify_endpoint


class TestClassifyEndpoint:
    def test_empty_string_is_unknown(self):
        assert _classify_endpoint("") == "unknown"

    def test_none_is_unknown(self):
        assert _classify_endpoint(None) == "unknown"

    def test_localhost_variants(self):
        assert _classify_endpoint("http://localhost:8080") == "localhost"
        assert _classify_endpoint("https://localhost") == "localhost"
        assert _classify_endpoint("http://127.0.0.1") == "localhost"
        assert _classify_endpoint("http://127.0.0.1:8080") == "localhost"
        assert _classify_endpoint("http://[::1]") == "localhost"
        assert _classify_endpoint("http://0.0.0.0:8080") == "localhost"
        assert _classify_endpoint("http://agent.localhost") == "localhost"

    def test_private_network_ipv4(self):
        # RFC1918
        assert _classify_endpoint("http://10.0.0.1") == "private_network"
        assert _classify_endpoint("http://10.1.2.3") == "private_network"
        assert _classify_endpoint("http://172.16.0.1") == "private_network"
        assert _classify_endpoint("http://172.31.255.254") == "private_network"
        assert _classify_endpoint("http://192.168.1.1") == "private_network"
        # Link-local
        assert _classify_endpoint("http://169.254.169.254") == "private_network"

    def test_rfc1918_172_boundary(self):
        # Review finding L4: explicit 172.15/172.32 boundary tests. Python
        # delegates to stdlib ipaddress.is_private which gets this right,
        # but the boundary wasn't asserted explicitly in the v6.2.0 test
        # suite. Cross-SDK parity with the TS/Go/Java suites.
        assert _classify_endpoint("http://172.15.0.1") == "remote"
        assert _classify_endpoint("http://172.32.0.1") == "remote"
        assert _classify_endpoint("http://172.16.0.0") == "private_network"
        assert _classify_endpoint("http://172.31.255.255") == "private_network"

    def test_private_network_ipv6(self):
        # Python uses stdlib ipaddress which classifies these correctly
        # — add explicit tests for cross-SDK parity and documentation.
        assert _classify_endpoint("http://[fd00::1]:8080") == "private_network"
        assert _classify_endpoint("http://[fd12:3456:789a::1]") == "private_network"
        assert _classify_endpoint("http://[fc00::1]") == "private_network"
        assert _classify_endpoint("http://[fe80::1]") == "private_network"

    def test_public_ipv6(self):
        assert _classify_endpoint("http://[2001:4860:4860::8888]") == "remote"
        assert _classify_endpoint("http://[2606:4700:4700::1111]") == "remote"

    def test_ipv6_loopback_and_unspecified(self):
        # ::1 is loopback (localhost). :: is IN6ADDR_ANY (bind-all); Python's
        # stdlib classifies :: as unspecified (which is_loopback=False but
        # also not is_private). Most clients bind to :: in the same semantic
        # as 0.0.0.0, so treat it as localhost for SDK endpoint classification.
        assert _classify_endpoint("http://[::1]") == "localhost"

    def test_private_network_hostnames(self):
        assert _classify_endpoint("http://agent.internal") == "private_network"
        assert _classify_endpoint("http://agent.local") == "private_network"
        assert _classify_endpoint("http://agent.lan") == "private_network"
        assert _classify_endpoint("http://agent.intranet") == "private_network"

    def test_remote(self):
        assert _classify_endpoint("https://production-us.getaxonflow.com") == "remote"
        assert _classify_endpoint("https://checkpoint.getaxonflow.com") == "remote"
        assert _classify_endpoint("https://api.example.com") == "remote"
        assert _classify_endpoint("http://8.8.8.8") == "remote"

    def test_malformed_url(self):
        # urlparse treats "not-a-url" as a scheme-less path, hostname=None → unknown.
        assert _classify_endpoint("not-a-url") == "unknown"
        assert _classify_endpoint("://nohost") == "unknown"

    def test_case_insensitive(self):
        assert _classify_endpoint("http://LOCALHOST:8080") == "localhost"
        assert _classify_endpoint("http://AGENT.INTERNAL") == "private_network"


class TestPayloadIncludesEndpointType:
    def test_payload_default_endpoint_type_is_unknown(self):
        payload = _build_payload("production")
        assert payload["endpoint_type"] == "unknown"

    def test_payload_with_explicit_endpoint_type(self):
        payload = _build_payload("production", None, "localhost")
        assert payload["endpoint_type"] == "localhost"

    def test_payload_does_not_contain_raw_url(self):
        """Critical non-goal: the SDK must never send the configured URL."""
        payload = _build_payload("production", None, "remote")
        payload_str = str(payload)
        # Assert no URL-like strings in the payload.
        assert "http://" not in payload_str
        assert "https://" not in payload_str
        assert "getaxonflow.com" not in payload_str
