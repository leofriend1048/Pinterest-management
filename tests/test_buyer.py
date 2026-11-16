import json
from datetime import date

import httpx
import pytest

from pinterest_ads_mcp import buyer, server, spec as spec_module
from pinterest_ads_mcp.client import Client

ACCOUNT = "549000000000"


class FakePinterest:
    """Enough of the API to exercise the buyer tools."""

    def __init__(self):
        self.campaigns = [
            {"id": "1", "name": "Prospecting | Best Sellers Video", "status": "ACTIVE",
             "daily_spend_cap": 500_000_000, "is_campaign_budget_optimization": True},
            {"id": "2", "name": "Retargeting", "status": "PAUSED",
             "daily_spend_cap": None, "lifetime_spend_cap": 9_000_000_000},
        ]
        self.report_rows = {
            "1": [{"CAMPAIGN_ID": 1, "CAMPAIGN_NAME": "Prospecting | Best Sellers Video",
                   "CAMPAIGN_ENTITY_STATUS": "ACTIVE", "SPEND_IN_DOLLAR": 1000.0,
                   "IMPRESSION_1": 50000, "CLICKTHROUGH_1": 600, "OUTBOUND_CLICK_1": 500,
                   "TOTAL_CHECKOUT": 20, "TOTAL_CHECKOUT_VALUE_IN_MICRO_DOLLAR": 2_500_000_000,
                   "VIDEO_3SEC_VIEWS_1": 25000, "VIDEO_P95_COMBINED_1": 500, "DATE": "2026-11-09"}],
            "2": [{"CAMPAIGN_ID": 2, "CAMPAIGN_NAME": "Retargeting",
                   "CAMPAIGN_ENTITY_STATUS": "PAUSED", "SPEND_IN_DOLLAR": 200.0,
                   "IMPRESSION_1": 10000, "OUTBOUND_CLICK_1": 100, "TOTAL_CHECKOUT": 0,
                   "TOTAL_CHECKOUT_VALUE_IN_MICRO_DOLLAR": 0, "DATE": "2026-11-09"}],
        }
        self.patches = []
        self.report_bodies = []
        self.patch_response = {"items": [{"data": {}, "exceptions": []}]}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.url.host == "reports.example":
            return httpx.Response(200, json=self.report_rows)
        if path.endswith("/reports"):
            if request.method == "POST":
                self.report_bodies.append(json.loads(request.content))
                return httpx.Response(200, json={"token": "t"})
            return httpx.Response(
                200, json={"report_status": "FINISHED", "url": "https://reports.example/r.json"}
            )
        if path.endswith("/campaigns"):
            if request.method == "PATCH":
                self.patches.append(json.loads(request.content))
                return httpx.Response(200, json=self.patch_response)
            wanted = request.url.params.get("campaign_ids")
            statuses = request.url.params.get_list("entity_statuses") or ["ACTIVE"]
            statuses = [s for chunk in statuses for s in chunk.split(",")]
            items = [
                c for c in self.campaigns
                if c["status"] in statuses and (not wanted or c["id"] in wanted.split(","))
            ]
            return httpx.Response(200, json={"items": items})
        if path.endswith("/ad_groups"):
            return httpx.Response(200, json={"items": [
                {"id": "10", "name": "Broad US", "status": "ACTIVE", "campaign_id": "1"}]})
        return httpx.Response(200, json={"id": ACCOUNT, "name": "Test", "currency": "USD"})


@pytest.fixture
def fake(monkeypatch):
    api = FakePinterest()
    client = Client(
        spec_module.load(),
        creds={"access_token": "tok", "ad_account_id": ACCOUNT},
        transport=httpx.MockTransport(api),
        sleep=lambda _: None,
    )
    monkeypatch.setattr(server, "_client", client)
    return api


# ── dates and maths ───────────────────────────────────────────────────────────


def test_date_presets_use_complete_days():
    today = date(2026, 11, 16)
    assert buyer.resolve_dates("last_7_days", None, None, today) == ("2026-11-09", "2026-11-15")
    assert buyer.resolve_dates("yesterday", None, None, today) == ("2026-11-15", "2026-11-15")
    assert buyer.resolve_dates("this_month", None, None, today) == ("2026-11-01", "2026-11-16")
    assert buyer.resolve_dates("last_month", None, None, today) == ("2026-10-01", "2026-10-31")
    assert buyer.resolve_dates("last_7_days", "2026-01-01", "2026-01-31", today) == (
        "2026-01-01", "2026-01-31")
    with pytest.raises(ValueError):
        buyer.resolve_dates("custom", "2026-01-01", None, today)


def test_summarize_computes_buyer_metrics():
    out = buyer.summarize(
        {"SPEND_IN_DOLLAR": 1000, "IMPRESSION_1": 50000, "OUTBOUND_CLICK_1": 500,
         "TOTAL_CHECKOUT": 20, "TOTAL_CHECKOUT_VALUE_IN_MICRO_DOLLAR": 2_500_000_000,
         "VIDEO_3SEC_VIEWS_1": 25000, "VIDEO_P95_COMBINED_1": 500},
        video=True,
    )
    assert (out["roas"], out["cpa"], out["aov"]) == (2.5, 50.0, 125.0)
    assert (out["cpm"], out["ctr"], out["cpc"], out["cvr"]) == (20.0, 1.0, 2.0, 4.0)
    assert (out["hook_rate"], out["hold_rate"]) == (50.0, 2.0)


def test_summarize_handles_zero_denominators():
    out = buyer.summarize({"SPEND_IN_DOLLAR": 50})
    assert out["roas"] == 0.0 and out["cpa"] is None and out["ctr"] is None


# ── reading ───────────────────────────────────────────────────────────────────


def test_performance_rows_totals_and_report_request(fake):
    out = json.loads(
        buyer.pinterest_performance(
            level="campaign", date_range="custom", start_date="2026-11-09",
            end_date="2026-11-15", click_window_days=7, view_window_days=1,
        )
    )
    assert [r["name"] for r in out["rows"]] == ["Prospecting | Best Sellers Video", "Retargeting"]
    assert out["rows"][0]["roas"] == 2.5 and out["rows"][0]["id"] == "1"
    assert out["rows"][0]["campaign_id"] == "1" and "Northbeam" in out["attribution_source"]
    assert out["totals"]["spend"] == 1200.0 and out["totals"]["roas"] == 2.08
    body = fake.report_bodies[0]
    assert body["level"] == "CAMPAIGN" and body["granularity"] == "TOTAL"
    assert body["click_window_days"] == 7 and "engagement_window_days" not in body


def test_performance_filters_and_sorts(fake):
    out = json.loads(buyer.pinterest_performance(name_contains="sellers video"))
    assert out["row_count"] == 1 and out["totals"]["spend"] == 1000.0

    out = json.loads(buyer.pinterest_performance(sort_by="cpa", ascending=True))
    # Retargeting has no purchases, so its CPA is undefined and it sorts last.
    assert [r["cpa"] for r in out["rows"]] == [50.0, None]

    out = json.loads(buyer.pinterest_performance(min_spend=500))
    assert [r["id"] for r in out["rows"]] == ["1"]


def test_performance_segment_uses_targeting_level(fake):
    buyer.pinterest_performance(level="account", segment_by="age")
    body = fake.report_bodies[-1]
    assert body["level"] == "ADVERTISER_TARGETING" and body["targeting_types"] == ["AGE_BUCKET"]


def test_find_matches_words_and_reports_dollars(fake):
    out = json.loads(buyer.pinterest_find(name="sellers prospecting"))
    assert out["matches"] == 1
    assert out["items"][0]["daily_budget"] == 500.0
    assert out["items"][0]["budget_set_at"] == "campaign"
    assert json.loads(buyer.pinterest_find(name="retargeting"))["matches"] == 0
    assert json.loads(buyer.pinterest_find(name="retargeting", status="any"))["matches"] == 1


def test_overview(fake):
    out = json.loads(buyer.pinterest_account_overview())
    assert out["active_campaigns"] == 1 and out["total_daily_budget"] == 500.0
    assert out["campaigns"][0]["roas"] == 2.5 and out["campaigns"][0]["active_ad_groups"] == 1
    assert out["spent_in_period_but_no_longer_active"][0]["name"] == "Retargeting"


# ── changing ──────────────────────────────────────────────────────────────────


def test_set_status_sends_array_and_reports_before_after(fake):
    out = json.loads(buyer.pinterest_set_status("campaign", ["1"], "PAUSED"))
    assert fake.patches == [[{"id": "1", "status": "PAUSED"}]]
    assert out["changed"][0] == {
        "id": "1", "name": "Prospecting | Best Sellers Video", "from": "ACTIVE", "to": "PAUSED"}


def test_set_status_refuses_unknown_ids(fake):
    with pytest.raises(ValueError, match="No campaign found"):
        buyer.pinterest_set_status("campaign", ["999"], "PAUSED")
    assert fake.patches == []


def test_set_budget_converts_dollars_to_micros(fake):
    out = json.loads(buyer.pinterest_set_daily_budget("campaign", "1", 600))
    assert fake.patches == [[{"id": "1", "daily_spend_cap": 600_000_000}]]
    assert (out["daily_budget_from"], out["daily_budget_to"], out["change_pct"]) == (
        500.0, 600.0, 20.0)


def test_set_budget_blocks_more_than_double_until_confirmed(fake):
    with pytest.raises(ValueError, match="more than double"):
        buyer.pinterest_set_daily_budget("campaign", "1", 5000)
    assert fake.patches == []
    buyer.pinterest_set_daily_budget("campaign", "1", 5000, allow_large_increase=True)
    assert fake.patches == [[{"id": "1", "daily_spend_cap": 5_000_000_000}]]


def test_set_budget_refuses_campaign_without_daily_budget(fake):
    with pytest.raises(ValueError, match="no daily budget"):
        buyer.pinterest_set_daily_budget("campaign", "2", 100)
    with pytest.raises(ValueError, match="positive"):
        buyer.pinterest_set_daily_budget("campaign", "1", 0)
    assert fake.patches == []


def test_item_level_rejection_is_an_error(fake):
    fake.patch_response = {"items": [{"data": None, "exceptions": [{"message": "bad status"}]}]}
    with pytest.raises(ValueError, match="bad status"):
        buyer.pinterest_set_status("campaign", ["1"], "PAUSED")


# ── context ───────────────────────────────────────────────────────────────────


def test_context_reads_saved_notes(monkeypatch, tmp_path):
    monkeypatch.setenv("PINTEREST_MCP_CREDENTIALS", str(tmp_path / "credentials.json"))
    empty = json.loads(buyer.pinterest_account_context())
    assert empty["context"] is None and "Target ROAS" in empty["template"]
    (tmp_path / "context.md").write_text("Target ROAS: 2.8")
    assert json.loads(buyer.pinterest_account_context())["context"] == "Target ROAS: 2.8"


# ── creatives ─────────────────────────────────────────────────────────────────


class CreativePinterest(FakePinterest):
    """Adds ads, ad groups, pins, boards and media to the fake."""

    def __init__(self, pin_scope=True, boards=1):
        super().__init__()
        self.pin_scope = pin_scope
        self.boards = [{"id": f"b{i}", "name": f"Board {i}"} for i in range(boards)]
        self.ads = [{"id": "50", "name": "Winner", "status": "ACTIVE", "pin_id": "900",
                     "creative_type": "VIDEO", "review_status": "APPROVED",
                     "destination_url": "https://shop.example/lp?utm_source=pinterest",
                     "customizable_cta_type": "SHOP_NOW", "ad_group_id": "10",
                     "campaign_id": "1"}]
        self.created_ads = []
        self.created_pins = []
        self.ad_response = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        denied = httpx.Response(401, json={"code": 3, "message": "Missing scopes: ['pins:read']"})
        if request.url.host == "i.example":
            return httpx.Response(200, content=b"\xff\xd8jpeg", headers={"content-type": "image/jpeg"})
        if request.url.host == "upload.example":
            return httpx.Response(204)
        if path.endswith("/ads"):
            if method == "POST":
                body = json.loads(request.content)
                self.created_ads.append(body)
                return httpx.Response(200, json=self.ad_response or {"items": [
                    {"data": {**body[0], "id": "777", "review_status": "PENDING"}, "exceptions": []}]})
            wanted = (request.url.params.get("ad_ids") or "").split(",")
            return httpx.Response(200, json={"items": [a for a in self.ads if a["id"] in wanted]})
        if path.endswith("/ad_groups"):
            wanted = (request.url.params.get("ad_group_ids") or "10").split(",")
            groups = [{"id": "10", "name": "Broad US", "status": "ACTIVE", "campaign_id": "1"}]
            return httpx.Response(200, json={"items": [g for g in groups if g["id"] in wanted]})
        if path.endswith("/ad_previews"):
            return httpx.Response(200, json={"url": "https://ads.pinterest.com/ad-preview/x/"})
        if path.endswith("/boards"):
            return httpx.Response(200, json={"items": self.boards}) if self.pin_scope else denied
        if path.endswith("/media"):
            return httpx.Response(200, json={
                "media_id": "m1", "upload_url": "https://upload.example/", "upload_parameters": {"k": "v"}})
        if path.endswith("/media/m1"):
            return httpx.Response(200, json={"status": "succeeded"})
        if path.endswith("/pins"):
            self.created_pins.append(json.loads(request.content))
            return httpx.Response(201, json={"id": "901"})
        if "/pins/" in path:
            if not self.pin_scope:
                return denied
            return httpx.Response(200, json={
                "title": "At-home dermaplaning", "description": "Glow", "link": "https://shop.example",
                "media": {"media_type": "video", "duration": 30000, "width": 720, "height": 1280,
                          "images": {"600x": {"url": "https://i.example/cover.jpg"}}}})
        return super().__call__(request)


def wire(monkeypatch, api):
    client = Client(spec_module.load(), creds={"access_token": "tok", "ad_account_id": ACCOUNT},
                    transport=httpx.MockTransport(api), sleep=lambda _: None)
    monkeypatch.setattr(server, "_client", client)
    return api


def test_creative_returns_copy_preview_and_image(monkeypatch):
    wire(monkeypatch, CreativePinterest())
    text, image = buyer.pinterest_creative(ad_id="50")
    out = json.loads(text)
    assert out["ad"]["destination_url"].startswith("https://shop.example/lp")
    assert out["preview_url"] and out["pin"]["title"] == "At-home dermaplaning"
    assert out["pin"]["duration_seconds"] == 30.0 and out["pin"]["dimensions"] == "720x1280"
    assert image.data == b"\xff\xd8jpeg"


def test_creative_degrades_without_pin_scope(monkeypatch):
    wire(monkeypatch, CreativePinterest(pin_scope=False))
    content = buyer.pinterest_creative(ad_id="50")
    out = json.loads(content[0])
    assert len(content) == 1 and out["pin"] is None
    assert out["preview_url"] and "pinterest-ads-mcp auth" in out["note"]


def test_launch_by_copying_a_winner_defaults_to_paused(monkeypatch):
    api = wire(monkeypatch, CreativePinterest())
    out = json.loads(buyer.pinterest_launch_ad(ad_group_id="10", copy_from_ad_id="50"))
    assert api.created_ads == [[{
        "ad_group_id": "10", "pin_id": "900", "creative_type": "VIDEO",
        "destination_url": "https://shop.example/lp?utm_source=pinterest",
        "status": "PAUSED", "name": "Winner", "customizable_cta_type": "SHOP_NOW"}]]
    assert out["ad_id"] == "777" and out["status"] == "PAUSED" and not out["new_pin_created"]
    assert api.created_pins == []


def test_launch_from_video_file_uploads_creates_pin_then_ad(monkeypatch, tmp_path):
    api = wire(monkeypatch, CreativePinterest())
    video = tmp_path / "hook.mp4"
    video.write_bytes(b"mp4")
    out = json.loads(buyer.pinterest_launch_ad(
        ad_group_id="10", video_path=str(video), name="Hook test",
        destination_url="https://shop.example/lp", title="Try it", status="ACTIVE"))
    pin = api.created_pins[0]
    assert pin["board_id"] == "b0" and pin["link"] == "https://shop.example/lp"
    assert pin["media_source"] == {
        "source_type": "video_id", "media_id": "m1", "cover_image_key_frame_time": 1}
    ad = api.created_ads[0][0]
    assert (ad["pin_id"], ad["creative_type"], ad["status"]) == ("901", "VIDEO", "ACTIVE")
    assert out["new_pin_created"] is True


def test_launch_from_image_file_sends_base64(monkeypatch, tmp_path):
    api = wire(monkeypatch, CreativePinterest())
    image = tmp_path / "static.png"
    image.write_bytes(b"\x89PNG")
    buyer.pinterest_launch_ad(ad_group_id="10", image_path=str(image),
                              destination_url="https://shop.example", title="T")
    source = api.created_pins[0]["media_source"]
    assert source["source_type"] == "image_base64" and source["content_type"] == "image/png"
    assert api.created_ads[0][0]["creative_type"] == "REGULAR"


def test_launch_validates_before_touching_the_account(monkeypatch, tmp_path):
    api = wire(monkeypatch, CreativePinterest(boards=3))
    with pytest.raises(ValueError, match="exactly one"):
        buyer.pinterest_launch_ad(ad_group_id="10")
    with pytest.raises(ValueError, match="exactly one"):
        buyer.pinterest_launch_ad(ad_group_id="10", pin_id="1", copy_from_ad_id="50")
    with pytest.raises(ValueError, match="No ad group"):
        buyer.pinterest_launch_ad(ad_group_id="404", copy_from_ad_id="50")
    with pytest.raises(ValueError, match="destination_url"):
        buyer.pinterest_launch_ad(ad_group_id="10", pin_id="900")
    with pytest.raises(ValueError, match="destination_url and title"):
        buyer.pinterest_launch_ad(ad_group_id="10", image_url="https://x/y.jpg")
    with pytest.raises(ValueError, match="File not found"):
        buyer.pinterest_launch_ad(ad_group_id="10", video_path=str(tmp_path / "nope.mp4"),
                                  destination_url="https://s", title="T")
    with pytest.raises(ValueError, match="Pass board_id"):
        buyer.pinterest_launch_ad(ad_group_id="10", image_url="https://x/y.jpg",
                                  destination_url="https://s", title="T")
    assert api.created_ads == [] and api.created_pins == []


def test_launch_new_creative_without_scope_explains(monkeypatch):
    api = wire(monkeypatch, CreativePinterest(pin_scope=False))
    with pytest.raises(ValueError, match="pinterest-ads-mcp auth"):
        buyer.pinterest_launch_ad(ad_group_id="10", image_url="https://x/y.jpg",
                                  destination_url="https://s", title="T")
    assert api.created_ads == []


def test_launch_reports_orphaned_pin_when_ad_is_rejected(monkeypatch):
    api = wire(monkeypatch, CreativePinterest())
    api.ad_response = {"items": [{"data": None, "exceptions": [{"message": "bad url"}]}]}
    with pytest.raises(ValueError, match="pin_id 901"):
        buyer.pinterest_launch_ad(ad_group_id="10", image_url="https://x/y.jpg",
                                  destination_url="https://s", title="T")


# ── analysis and visuals ──────────────────────────────────────────────────────


def test_confidence_tiers():
    assert [buyer.confidence(n) for n in (0, 9, 10, 29, 30, 500)] == [
        "low", "low", "directional", "directional", "solid", "solid"]
    assert buyer.summarize({"TOTAL_CHECKOUT": 4})["confidence"] == "low"


def test_two_proportion_detects_a_real_gap_and_ignores_noise():
    real = buyer.two_proportion(300, 10000, 200, 10000)       # 3.0% vs 2.0%
    assert real["significant_at_95"] and real["relative_lift_pct"] == 50.0
    assert real["ci95_pct_points"][0] > 0
    noise = buyer.two_proportion(21, 1000, 20, 1000)           # 2.1% vs 2.0%
    assert not noise["significant_at_95"] and noise["p_value"] > 0.5
    assert buyer.two_proportion(1, 0, 1, 10) is None
    assert buyer.two_proportion(2, 100, 1, 100)["enough_data"] is False


def test_compare_refuses_to_pick_a_winner_on_low_data(fake):
    out = json.loads(buyer.pinterest_compare("campaign", "1", "2", date_range="custom",
                                             start_date="2026-11-09", end_date="2026-11-15"))
    assert out["a"]["name"] == "Prospecting | Best Sellers Video" and out["b"]["confidence"] == "low"
    assert out["click_through_rate_test"]["a_rate_pct"] == 1.0
    assert any("Do not pick a winner" in line for line in out["verdict"])
    with pytest.raises(ValueError, match="No delivery"):
        buyer.pinterest_compare("campaign", "1", "999")


def test_dashboard_writes_one_self_contained_page(fake, tmp_path):
    fake.report_rows = {
        "a": [{"DATE": "2026-11-14", "SPEND_IN_DOLLAR": 100.0, "IMPRESSION_1": 5000,
               "OUTBOUND_CLICK_1": 50, "TOTAL_CHECKOUT": 2,
               "TOTAL_CHECKOUT_VALUE_IN_MICRO_DOLLAR": 150_000_000, "CAMPAIGN_ID": 1,
               "CAMPAIGN_NAME": "Prospecting <script>alert(1)</script>", "AD_ID": 7,
               "AD_NAME": "Hook A", "TARGETING_VALUE": "35-44"},
              {"DATE": "2026-11-15", "SPEND_IN_DOLLAR": 300.0, "IMPRESSION_1": 9000,
               "OUTBOUND_CLICK_1": 90, "TOTAL_CHECKOUT": 12,
               "TOTAL_CHECKOUT_VALUE_IN_MICRO_DOLLAR": 900_000_000, "CAMPAIGN_ID": 1,
               "CAMPAIGN_NAME": "Prospecting <script>alert(1)</script>", "AD_ID": 8,
               "AD_NAME": "Hook B", "TARGETING_VALUE": "18-24"}],
    }
    out = json.loads(buyer.pinterest_dashboard(
        date_range="custom", start_date="2026-11-14", end_date="2026-11-15", target_roas=2.8,
        headline="Losing money", insights=["ROAS 2.63 against 2.8"],
        northbeam_campaigns=[{"campaign_id": "1", "spend": 400, "revenue": 800}],
        open_in_browser=False, output_dir=str(tmp_path)))
    page = open(out["file"]).read()
    assert out["opened_in_browser"] is False and out["totals"]["spend"] == 400.0
    # Self-contained: no scripts, styles, fonts or images fetched from anywhere.
    assert "http://" not in page.replace("http://www.w3.org/2000/svg", "") and "https://" not in page
    data = json.loads(page.split('<script id="data" type="application/json">')[1].split("</script>")[0])
    assert data["target_roas"] == 2.8 and data["headline"] == "Losing money"
    assert data["hero_label"] == "ROAS · Northbeam" and data["hero_roas"] == 2.0
    assert [d["date"] for d in data["daily"]] == ["2026-11-14", "2026-11-15"]
    assert [r["segment"] for r in data["age"]] == ["18-24", "35-44"]      # natural order
    # Names from the account can never break out of the data block.
    assert "<script>alert(1)" not in page and "alert(1)" in page
