"""Render the terminal screenshots used in the README (demo data, no network).

    uv run python scripts/render_screens.py
"""

from __future__ import annotations

import io
from pathlib import Path

from rich import box
from rich.console import Console
from rich.table import Table

from pinterest_ads_mcp import onboarding as ob

DOCS = Path(__file__).resolve().parent.parent / "docs"


def recorder(width: int) -> Console:
    return Console(record=True, width=width, file=io.StringIO(), force_terminal=True,
                   color_system="truecolor")


def setup_screen() -> Console:
    c = recorder(88)
    ob.banner(c)
    ob.step(c, 1, "Check your computer")
    ob.ok(c, "Pinterest Ads MCP 0.3.0 installed.")
    ob.ok(c, "Claude Code found.")
    ob.ok(c, "Claude desktop app found.")
    ob.step(c, 2, "Connect Pinterest")
    ob.ok(c, "Team setup code accepted.")
    c.print("    Opening Pinterest in your browser. Approve access there.")
    ob.ok(c, "Signed in to Pinterest.")
    ob.step(c, 3, "Choose your ad account")
    ob.ok(c, "Using [bold]Acme Skincare[/bold] (549000000000, USD).")
    ob.step(c, 4, "Tell Claude about the account")
    ob.ok(c, "Installed your team's account notes (targets, naming, rules).")
    ob.step(c, 5, "Connect to Claude")
    ob.ok(c, "Added to Claude Code.")
    ob.ok(c, "Added to the Claude desktop app. Reopening it now.")
    c.print()
    c.print(ob.results_table(
        "Acme Skincare",
        {"spend": 41120, "revenue": 120480, "roas": 2.93, "purchases": 1368, "cpa": 30}, 3))
    ob.info(c, "Pinterest-attributed, 30-day click / 1-day view.")
    ob.finish(c, True)
    return c


def doctor_screen() -> Console:
    c = recorder(96)
    ob.banner(c)
    rows = [
        (True, "Credentials file", "~/.config/pinterest-ads-mcp/credentials.json"),
        (True, "Readable only by you", "Yes"),
        (True, "Pinterest sign-in", "Acme Skincare (549000000000)"),
        (True, "Renews itself", "Yes"),
        (True, "Token expiry", "in 30 days (renews automatically)"),
        (True, "Can build ads from new files", "Yes"),
        (True, "Account notes", "~/.config/pinterest-ads-mcp/context.md"),
        (True, "Claude Code", "Connected"),
        (None, "Claude desktop app", "Not connected (optional). Run: pinterest-ads-mcp setup"),
        (True, "Pinterest API description", "v5.28.0, 266 endpoints"),
    ]
    table = Table(box=box.SIMPLE_HEAD, padding=(0, 2), show_edge=False)
    table.add_column("")
    table.add_column("Check", style="bold")
    table.add_column("Result")
    marks = {True: "[green]✓[/green]", False: "[red]✗[/red]", None: "[yellow]![/yellow]"}
    for good, label, detail in rows:
        table.add_row(marks[good], label, detail)
    c.print()
    c.print(table)
    c.print("\n  [green]Everything needed is working.[/green]\n")
    return c


def demo_dashboard() -> str:
    """A report page filled with made-up numbers for a made-up brand."""
    import math
    import random
    from datetime import date, timedelta

    from pinterest_ads_mcp import buyer, dashboard

    random.seed(7)
    start = date(2026, 3, 2)
    daily, raw_total = [], {}
    for i in range(14):
        spend = 2400 + 90 * i + random.uniform(-180, 180)
        clicks = spend / random.uniform(0.82, 0.98)
        purchases = round(clicks * random.uniform(0.026, 0.034))
        raw = {"SPEND_IN_DOLLAR": spend, "IMPRESSION_1": clicks / 0.0142, "OUTBOUND_CLICK_1": clicks,
               "TOTAL_CHECKOUT": purchases, "TOTAL_CHECKOUT_VALUE_IN_MICRO_DOLLAR": purchases * 88e6}
        for k, v in raw.items():
            raw_total[k] = raw_total.get(k, 0) + v
        daily.append({"date": (start + timedelta(days=i)).isoformat(), **buyer.summarize(raw)})
    totals = buyer.summarize(raw_total)
    previous = buyer.summarize({k: v * f for (k, v), f in zip(raw_total.items(), (0.84, 0.8, 0.83, 0.74, 0.74))})

    def row(name, spend, roas, ctr, cpm, **more):
        purchases = round(spend * roas / 88)
        impressions = spend / cpm * 1000
        return {"name": name, **buyer.summarize({
            "SPEND_IN_DOLLAR": spend, "IMPRESSION_1": impressions, "OUTBOUND_CLICK_1": impressions * ctr / 100,
            "TOTAL_CHECKOUT": purchases, "TOTAL_CHECKOUT_VALUE_IN_MICRO_DOLLAR": purchases * 88e6,
            "VIDEO_3SEC_VIEWS_1": impressions * more.get("hook", 0) / 100,
            "VIDEO_P95_COMBINED_1": impressions * more.get("hook", 0) / 100 * more.get("hold", 0) / 100,
        }, video="hook" in more), **{k: v for k, v in more.items() if k == "campaign"}}

    spend = totals["spend"]
    campaigns = [row("Prospecting | Best Sellers Video", spend * .42, 3.4, 1.62, 21.4),
                 row("Scale | Performance+", spend * .31, 2.9, 1.38, 24.8),
                 row("Scale | Actalike Purchasers 180d", spend * .17, 2.2, 1.21, 27.9),
                 row("Test | New hooks W10", spend * .08, 1.5, 1.05, 30.2),
                 row("Retargeting | Site visitors 30d", spend * .02, 4.6, 2.4, 33.0)]
    ads = [row("UGC | 3-minute glow routine", 6900, 3.9, 1.9, 20.1, hook=72.4, hold=2.9, campaign="Prospecting | Best Sellers Video"),
           row("Founder story | Why we made it", 5200, 3.1, 1.6, 22.7, hook=66.0, hold=2.1, campaign="Scale | Performance+"),
           row("Before and after | 30 days", 4300, 2.8, 1.5, 23.9, hook=69.3, hold=1.8, campaign="Prospecting | Best Sellers Video"),
           row("Dermatologist explains", 3600, 2.4, 1.3, 25.2, hook=58.8, hold=2.4, campaign="Scale | Performance+"),
           row("Unboxing | First impressions", 2900, 1.9, 1.2, 27.4, hook=61.2, hold=1.2, campaign="Scale | Actalike Purchasers 180d"),
           row("Static | Price anchor", 2100, 1.4, 0.9, 29.8, campaign="Test | New hooks W10"),
           row("Hook test | Question open", 900, 2.6, 1.7, 26.0, hook=74.9, hold=1.5, campaign="Test | New hooks W10")]
    shares = {"18-24": (.06, 1.9), "25-34": (.19, 2.6), "35-44": (.27, 3.3), "45-49": (.14, 3.2),
              "50-54": (.12, 3.0), "55-64": (.14, 2.7), "65+": (.08, 2.1)}
    age = [{"segment": k, "spend": round(spend * s, 2), "roas": r, "purchases": 40} for k, (s, r) in shares.items()]
    return dashboard.render({
        "account": "Acme Skincare", "start": daily[0]["date"], "end": daily[-1]["date"],
        "attribution": "Pinterest attribution: 7-day click, 7-day engagement, 1-day view",
        "source_note": "Demo data for a made-up brand.", "generated": "Mar 16, 2026 at 9:02 AM",
        "target_roas": 2.8, "hero_roas": totals["roas"], "hero_label": "ROAS · Pinterest-attributed",
        "roas_label": "Pinterest-attributed ROAS", "totals": totals, "previous": previous,
        "daily": daily, "campaigns": campaigns, "ads": ads, "age": age, "has_northbeam": False,
        "min_ad_spend": 500, "low_data_purchases": 10,
        "headline": "Pinterest is above target and scaling cleanly: ROAS 2.9 against 2.8 on $41K",
        "insights": [
            "Spend is up 19% on the prior two weeks and ROAS held, so there is room to keep scaling.",
            "Best Sellers Video carries 42% of spend at 3.4x. It is the place to add budget first.",
            "\"3-minute glow routine\" leads on both hook rate (72%) and ROAS (3.9x); brief more like it.",
            "New hooks W10 is at 1.5x on real spend. Cut the static, keep the question-open hook running.",
        ],
    })


def screenshot(html_path: Path, png_path: Path, height: int) -> bool:
    import shutil
    import subprocess

    chrome = shutil.which("google-chrome") or "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    if not Path(chrome).exists():
        return False
    subprocess.run([chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                    f"--window-size=1200,{height}", "--force-device-scale-factor=2",
                    f"--screenshot={png_path}", html_path.as_uri()], capture_output=True, timeout=120)
    return png_path.exists()


def main() -> None:
    DOCS.mkdir(exist_ok=True)
    for name, title, screen in (
        ("setup", "pinterest-ads-mcp setup", setup_screen()),
        ("doctor", "pinterest-ads-mcp doctor", doctor_screen()),
    ):
        path = DOCS / f"{name}.svg"
        path.write_text(screen.export_svg(title=title))
        print(f"{path}  {path.stat().st_size // 1024} KB")
    demo = DOCS / "demo-report.html"
    demo.write_text(demo_dashboard(), encoding="utf-8")
    print(f"{demo}  {demo.stat().st_size // 1024} KB")
    shot = DOCS / "dashboard.png"
    if screenshot(demo, shot, 1560):
        print(f"{shot}  {shot.stat().st_size // 1024} KB")
    else:
        print("Chrome not found: dashboard.png not regenerated.")


if __name__ == "__main__":
    main()
