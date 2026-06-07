from types import SimpleNamespace

from beatodds.data.gamma_client import GammaClient


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self.payload


class FakeHttpClient:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get(self, path, params):
        self.calls.append((path, dict(params)))
        offset = int(params.get("offset", 0))
        return FakeResponse(self.pages.get(offset, []))


def test_get_liquid_markets_paginates_until_requested_limit() -> None:
    client = object.__new__(GammaClient)
    client.cfg = SimpleNamespace(scanner_gamma_page_limit=2)
    client._client = FakeHttpClient({
        0: [
            {"conditionId": "a"},
            {"conditionId": "b"},
        ],
        2: [
            {"conditionId": "c"},
            {"conditionId": "d"},
        ],
        4: [
            {"conditionId": "e"},
        ],
    })

    markets = client.get_liquid_markets(limit=5, min_volume_24h=10.0)

    assert [item["conditionId"] for item in markets] == ["a", "b", "c", "d", "e"]
    assert [call[1]["offset"] for call in client._client.calls] == [0, 2, 4]
    assert [call[1]["limit"] for call in client._client.calls] == [2, 2, 1]


def test_get_liquid_markets_deduplicates_condition_ids() -> None:
    client = object.__new__(GammaClient)
    client.cfg = SimpleNamespace(scanner_gamma_page_limit=2)
    client._client = FakeHttpClient({
        0: [
            {"conditionId": "a"},
            {"conditionId": "b"},
        ],
        2: [
            {"conditionId": "b"},
            {"conditionId": "c"},
        ],
    })

    markets = client.get_liquid_markets(limit=4, min_volume_24h=10.0)

    assert [item["conditionId"] for item in markets] == ["a", "b", "c"]
