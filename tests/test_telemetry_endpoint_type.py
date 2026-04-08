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
