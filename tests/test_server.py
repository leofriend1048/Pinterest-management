import base64
import json

import httpx
import pytest

from pinterest_ads_mcp import server, spec as spec_module
from pinterest_ads_mcp.client import Client, PinterestError, expand_files

ACCOUNT = "549000000000"


@pytest.fixture(scope="module")
def spec():
    return spec_module.load()


def make_client(spec, handler, **creds):
    base = {"access_token": "tok", "ad_account_id": ACCOUNT}
    base.update(creds)
    return Client(
        spec, creds=base, transport=httpx.MockTransport(handler), sleep=lambda _: None
    )


# ── spec ──────────────────────────────────────────────────────────────────────


def test_spec_covers_the_api(spec):
    assert len(spec.operations) > 250
    for op_id in ("campaigns/create", "ad_groups/update", "ads/create", "pins/create"):
        assert spec.get(op_id) is not None, op_id


def test_literal_path_beats_placeholder(spec):
    op = spec.match("POST", f"/ad_accounts/{ACCOUNT}/ad_groups/audience_sizing")
    assert op.id == "ad_groups/audience_sizing"
    op = spec.match("GET", f"/ad_accounts/{ACCOUNT}/ad_groups/123")
    assert op.id == "ad_groups/get"


def test_match_accepts_unfilled_placeholder(spec):
    assert spec.match("GET", "/ad_accounts/{ad_account_id}/campaigns").id == "campaigns/list"


@pytest.mark.parametrize(
    "op_id",
    [
        "campaigns/create",
        "campaigns/update",
        "ad_groups/create",
        "ads/create",
        "ads/update",
        "audiences/create",
        "keywords/create",
        "pins/create",
        "pins/delete",
        "bulk_upsert/create",
        "notification/post",
        "oauth/token",
        "token/revoke",
    ],
)
def test_mutating_operations_are_write(spec, op_id):
    assert spec.get(op_id).read_only is False


@pytest.mark.parametrize(
    "op_id",
    [
        "campaigns/list",
        "analytics/create_report",
        "ad_groups/audience_sizing",
        "ad_groups_bid_floor/get",
        "get_campaign_delivery_estimates",
    ],
)
def test_non_mutating_operations_are_read(spec, op_id):
    assert spec.get(op_id).read_only is True


def test_every_read_only_non_get_needs_only_read_scopes(spec):
    for op in spec.operations:
        if op.read_only and op.method != "GET":
            assert op.scopes and all("write" not in s for s in op.scopes), op.id


def test_search_and_describe(spec):
    found = spec.search("campaign budget", access="write")
    assert found and all(not op.read_only for op in found)
    described = spec.describe(spec.get("campaigns/create"))
    assert described["use_tool"] == "pinterest_api_write"
    assert described["request_body"]["schema"]["type"] == "array"
    assert "$ref" not in json.dumps(described)


# ── client ────────────────────────────────────────────────────────────────────


def test_path_normalisation_and_account_fill(spec):
    client = make_client(spec, lambda r: httpx.Response(200, json={}))
    expected = f"/ad_accounts/{ACCOUNT}/campaigns"
    for given in (
        "/ad_accounts/{ad_account_id}/campaigns",
        "ad_accounts/{ad_account_id}/campaigns",
        "/v5/ad_accounts/{ad_account_id}/campaigns",
        "https://api.pinterest.com/v5/ad_accounts/{ad_account_id}/campaigns",
    ):
        assert client.normalize_path(given) == expected
    with pytest.raises(ValueError):
        client.normalize_path("/ad_accounts/{ad_account_id}/campaigns/{campaign_id}")
    with pytest.raises(ValueError):
        client.normalize_path("https://evil.example/v5/pins")


def test_request_sends_token_query_and_json_body(spec):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content) if request.content else None
        return httpx.Response(200, json={"items": []})

    client = make_client(spec, handler)
    client.request(
        "GET",
        "/ad_accounts/{ad_account_id}/campaigns",
        query={"campaign_ids": ["1", "2"], "page_size": 5, "skip": None},
    )
    assert seen["auth"] == "Bearer tok"
    assert "page_size=5" in seen["url"] and "skip" not in seen["url"]
    assert ("campaign_ids=1%2C2" in seen["url"]) or (
        "campaign_ids=1&campaign_ids=2" in seen["url"]
    )

    client.request(
        "PATCH", "/ad_accounts/{ad_account_id}/campaigns", body=[{"id": "1", "status": "PAUSED"}]
    )
    assert seen["body"] == [{"id": "1", "status": "PAUSED"}]


def test_error_carries_pinterest_message(spec):
    client = make_client(
        spec, lambda r: httpx.Response(400, json={"code": 1, "message": "bad budget"})
    )
    with pytest.raises(PinterestError) as err:
        client.request("PATCH", "/ad_accounts/{ad_account_id}/campaigns", body=[{}])
    assert err.value.status == 400 and "bad budget" in str(err.value)


def test_expired_token_is_refreshed_once(spec):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, request.headers.get("authorization")))
        if request.url.path.endswith("/oauth/token"):
            return httpx.Response(
                200, json={"access_token": "new", "expires_in": 2592000, "scope": "ads:read"}
            )
        if request.headers["authorization"] == "Bearer old":
            return httpx.Response(401, json={"message": "expired"})
        return httpx.Response(200, json={"ok": True})

    client = make_client(
        spec, handler, access_token="old", refresh_token="r", app_id="1", app_secret="s"
    )
    assert client.request("GET", "/ad_accounts/{ad_account_id}/campaigns") == {"ok": True}
    assert [c[1].rsplit("/", 1)[-1] for c in calls] == ["campaigns", "token", "campaigns"]
    assert calls[-1][2] == "Bearer new"


def test_401_without_refresh_token_explains_itself(spec):
    client = make_client(spec, lambda r: httpx.Response(401, json={"message": "nope"}))
    with pytest.raises(PinterestError) as err:
        client.request("GET", "/ad_accounts/{ad_account_id}/campaigns")
    assert "pinterest-ads-mcp auth" in str(err.value)


def test_writes_are_not_retried_on_server_error(spec):
    count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        count["n"] += 1
        return httpx.Response(502, text="bad gateway")

    client = make_client(spec, handler)
    with pytest.raises(PinterestError):
        client.request("POST", "/ad_accounts/{ad_account_id}/campaigns", body=[{}])
    assert count["n"] == 1

    count["n"] = 0
    with pytest.raises(PinterestError):
        client.request("GET", "/ad_accounts/{ad_account_id}/campaigns")
    assert count["n"] == 3


def test_expand_files(tmp_path):
    image = tmp_path / "pin.png"
    image.write_bytes(b"\x89PNG")
    out = expand_files({"media_source": {"data": f"@base64:{image}"}, "n": [1, "x"]})
    assert out["media_source"]["data"] == base64.b64encode(b"\x89PNG").decode()
    assert out["n"] == [1, "x"]
    with pytest.raises(ValueError):
        expand_files("@base64:/no/such/file")


# ── tools ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def wired(spec, monkeypatch):
    """Point the tools at a fake Pinterest and record what reaches it."""
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        return httpx.Response(200, json={"items": [], "method": request.method})

    monkeypatch.setattr(server, "_client", make_client(spec, handler))
    return requests


def test_read_tool_refuses_mutations(wired):
    with pytest.raises(ValueError, match="pinterest_api_write"):
        server.pinterest_api_read(
            "/ad_accounts/{ad_account_id}/campaigns", method="POST", body=[{"name": "x"}]
        )
    with pytest.raises(ValueError):
        server.pinterest_api_read("/not/in/the/spec", method="POST")
    assert wired == []


def test_read_tool_allows_get_and_read_only_post(wired):
    server.pinterest_api_read("/ad_accounts/{ad_account_id}/campaigns")
    server.pinterest_api_read(
        "/ad_accounts/{ad_account_id}/ad_groups/audience_sizing", method="POST", body={}
    )
    assert [m for m, _ in wired] == ["GET", "POST"]


def test_write_tool_accepts_json_string_body(wired):
    out = server.pinterest_api_write(
        "PATCH", "/ad_accounts/{ad_account_id}/ads", body='[{"id": "9", "status": "PAUSED"}]'
    )
    assert json.loads(out)["method"] == "PATCH"
    assert wired == [("PATCH", f"/v5/ad_accounts/{ACCOUNT}/ads")]


def test_large_responses_are_saved_to_a_file(spec, monkeypatch, tmp_path):
    big = {"items": [{"id": str(i), "name": "x" * 200} for i in range(1000)], "bookmark": "b"}
    monkeypatch.setattr(server, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(
        server, "_client", make_client(spec, lambda r: httpx.Response(200, json=big))
    )
    out = json.loads(server.pinterest_api_read("/ad_accounts/{ad_account_id}/campaigns"))
    assert out["item_count"] == 1000 and out["bookmark"] == "b"
    assert json.loads(open(out["saved_to"]).read()) == big


def test_search_tool_lists_areas_then_operations():
    areas = json.loads(server.pinterest_search_endpoints())
    assert "campaigns" in areas["tags"]
    hits = json.loads(server.pinterest_search_endpoints(query="create", tag="campaigns"))
    assert any(op["operation_id"] == "campaigns/create" for op in hits["operations"])
