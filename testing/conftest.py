from unittest.mock import Mock

import pytest
import requests

from tools import TravelTools


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    def unexpected_request(*args, **kwargs):
        pytest.fail("Tests must mock outbound network requests")

    monkeypatch.setattr(requests.sessions.Session, "request", unexpected_request)
    monkeypatch.setattr("botocore.httpsession.URLLib3Session.send", unexpected_request)
    monkeypatch.setattr("redis.connection.Connection.connect", unexpected_request)


@pytest.fixture
def tools():
    return TravelTools(serpapi_key="test-serpapi", geoapify_key="test-geoapify", apify_token="test-apify")


@pytest.fixture
def http_get(monkeypatch):
    get = Mock()
    monkeypatch.setattr("tools.requests.get", get)
    return get


@pytest.fixture
def response():
    def build(payload, status=200):
        result = Mock(spec=requests.Response)
        result.status_code = status
        result.json.return_value = payload
        if status >= 400:
            result.raise_for_status.side_effect = requests.HTTPError(
                "Provider rejected the request", response=result
            )
        return result

    return build


@pytest.fixture
def http_post(monkeypatch):
    post = Mock()
    monkeypatch.setattr("tools.requests.post", post)
    return post
