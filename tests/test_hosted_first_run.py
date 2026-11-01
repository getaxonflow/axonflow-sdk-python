"""Website-first Hosted registration and legacy Community compatibility."""

from __future__ import annotations

import httpx
import pytest

from axonflow import community


@pytest.mark.parametrize(
    "endpoint",
    [
        community.TRY_ENDPOINT,
        "https://try.getaxonflow.com/",
        "https://try-staging.getaxonflow.com",
    ],
)
def test_hosted_registration_directs_to_verified_portal_before_network(monkeypatch, endpoint):
    def unexpected_post(*_args, **_kwargs):
        pytest.fail("Hosted registration reached the network")

    monkeypatch.setattr(community.httpx, "post", unexpected_post)

    with pytest.raises(ValueError, match="verified") as error:
        community.register_try("new buyer", endpoint=endpoint)

    assert community.HOSTED_START_URL in str(error.value)
    assert "link an API client" in str(error.value)


def test_custom_self_hosted_community_registration_still_works(monkeypatch):
    seen = []

    def post(url, *, json, timeout):
        seen.append((url, json, timeout))
        return httpx.Response(
            201,
            json={"tenant_id": "cs_local", "secret": "local-secret"},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(community.httpx, "post", post)

    result = community.register_try("local", endpoint="http://localhost:8080/")

    assert result == {"tenant_id": "cs_local", "secret": "local-secret"}
    assert seen == [("http://localhost:8080/api/v1/register", {"label": "local"}, 10.0)]
