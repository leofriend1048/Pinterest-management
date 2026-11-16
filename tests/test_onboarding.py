import io
import json

import httpx
import pytest
from rich.console import Console

from pinterest_ads_mcp import credentials, oauth, onboarding, playbooks, spec as spec_module
from pinterest_ads_mcp.client import Client


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("PINTEREST_MCP_CREDENTIALS", str(tmp_path / "credentials.json"))
    monkeypatch.delenv("PINTEREST_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("PINTEREST_AD_ACCOUNT_ID", raising=False)
    return tmp_path


def quiet() -> Console:
    return Console(file=io.StringIO(), width=100)


def text(console: Console) -> str:
    """Printed output with line wrapping undone."""
    return " ".join(console.file.getvalue().split())


def fake_api(monkeypatch, handler):
    def make():
        return Client(spec_module.load(), transport=httpx.MockTransport(handler), sleep=lambda _: None)

    monkeypatch.setattr(onboarding, "_client", make)


# ── setup codes ───────────────────────────────────────────────────────────────


def test_invite_round_trip_carries_app_account_and_notes():
    code = onboarding.make_invite("123", "s3cret", "549", "Target ROAS: 2.8")
    assert code.startswith("pam1_") and "s3cret" not in code
    assert onboarding.read_invite(f"  {code[:20]}\n{code[20:]} ") == {
        "app_id": "123", "app_secret": "s3cret", "ad_account_id": "549",
        "context": "Target ROAS: 2.8"}


def test_invite_never_contains_a_login(home):
    credentials.save({"app_id": "1", "app_secret": "s", "access_token": "pina_private",
                      "refresh_token": "pinr_private", "ad_account_id": "9"})
    console = quiet()
    assert onboarding.invite(console=console) == 0
    code = next(w for w in console.file.getvalue().split() if w.startswith("pam1_"))
    payload = onboarding.read_invite(code)
    assert set(payload) == {"app_id", "app_secret", "ad_account_id"}
    assert "pina_private" not in json.dumps(payload)


@pytest.mark.parametrize("bad", ["hello", "pam1_!!!!", "pam1_" + "A" * 12])
def test_bad_invites_are_rejected_with_a_reason(bad):
    with pytest.raises(ValueError):
        onboarding.read_invite(bad)


def test_invite_requires_a_stored_app(home):
    console = quiet()
    assert onboarding.invite(console=console) == 1
    assert "setup" in text(console)


# ── connecting Claude ─────────────────────────────────────────────────────────


def test_desktop_config_is_merged_not_replaced(tmp_path):
    path = tmp_path / "claude_desktop_config.json"
    path.write_text(json.dumps({"theme": "dark", "mcpServers": {"other": {"command": "x"}}}))
    ok, _ = onboarding.register_claude_desktop(["/bin/pinterest-ads-mcp", "serve"], path)
    config = json.loads(path.read_text())
    assert ok and config["theme"] == "dark" and "other" in config["mcpServers"]
    assert config["mcpServers"]["pinterest-ads"] == {
        "command": "/bin/pinterest-ads-mcp", "args": ["serve"]}


def test_desktop_config_that_is_not_json_is_left_alone(tmp_path):
    path = tmp_path / "claude_desktop_config.json"
    path.write_text("{ not json")
    ok, message = onboarding.register_claude_desktop(["x"], path)
    assert not ok and path.read_text() == "{ not json" and "left alone" in message


def test_server_command_is_absolute_for_a_source_checkout():
    command = onboarding.server_command()
    assert command[-1] == "pinterest-ads-mcp" and "--project" in command
    assert command[command.index("--project") + 1].startswith("/")


# ── wizard steps ──────────────────────────────────────────────────────────────


def test_single_ad_account_is_chosen_automatically(home, monkeypatch):
    credentials.save({"access_token": "t"})
    fake_api(monkeypatch, lambda r: httpx.Response(200, json={"items": [
        {"id": "549", "name": "Acme", "currency": "USD"}]}))
    console = quiet()
    assert onboarding.choose_ad_account(console, None) is True
    assert credentials.load()["ad_account_id"] == "549" and "Acme" in text(console)


def test_team_account_is_preferred_when_the_login_can_see_it(home, monkeypatch):
    credentials.save({"access_token": "t"})
    fake_api(monkeypatch, lambda r: httpx.Response(200, json={"items": [
        {"id": "1", "name": "Other", "currency": "USD"},
        {"id": "549", "name": "Acme", "currency": "USD"}]}))
    assert onboarding.choose_ad_account(quiet(), "549") is True
    assert credentials.load()["ad_account_id"] == "549"


def test_no_ad_accounts_explains_what_to_do(home, monkeypatch):
    credentials.save({"access_token": "t"})
    fake_api(monkeypatch, lambda r: httpx.Response(200, json={"items": []}))
    console = quiet()
    assert onboarding.choose_ad_account(console, None) is False
    assert "Business Manager" in text(console)


def test_several_accounts_without_a_terminal_does_not_guess(home, monkeypatch):
    credentials.save({"access_token": "t"})
    fake_api(monkeypatch, lambda r: httpx.Response(200, json={"items": [
        {"id": "1", "name": "A"}, {"id": "2", "name": "B"}]}))
    assert onboarding.choose_ad_account(quiet(), None, interactive=False) is False
    assert "ad_account_id" not in credentials.load()


def test_team_notes_are_installed_from_the_invite(home):
    console = quiet()
    onboarding.write_context(console, {"context": "Target ROAS: 2.8"}, interactive=False)
    assert onboarding.context_path().read_text() == "Target ROAS: 2.8"


def test_existing_notes_are_kept_when_there_is_no_invite(home):
    onboarding.context_path().write_text("mine")
    onboarding.write_context(quiet(), None, interactive=False)
    assert onboarding.context_path().read_text() == "mine"


def test_sign_in_falls_back_to_ads_only_scopes(home, monkeypatch):
    asked = []

    def fake_sign_in(app_id, app_secret, scopes, on_url=None):
        asked.append(scopes)
        if scopes == oauth.FULL_SCOPES:
            raise oauth.SignInError("denied", "invalid_scope")
        return {"app_id": app_id, "app_secret": app_secret, "access_token": "pina_x",
                "refresh_token": "pinr_x", "scope": scopes}

    monkeypatch.setattr(oauth, "sign_in", fake_sign_in)
    console = quiet()
    invite = {"app_id": "1", "app_secret": "s"}
    assert onboarding.connect_pinterest(console, invite, None, interactive=False) is True
    assert asked == [oauth.FULL_SCOPES, oauth.ADS_SCOPES]
    assert credentials.load()["access_token"] == "pina_x"
    assert "new files" in text(console)


def test_sign_in_timeout_stops_instead_of_retrying(home, monkeypatch):
    calls = []

    def fake_sign_in(*args, **kwargs):
        calls.append(1)
        raise oauth.SignInError("Sign-in did not complete (timeout).", "timeout")

    monkeypatch.setattr(oauth, "sign_in", fake_sign_in)
    assert onboarding.connect_pinterest(
        quiet(), {"app_id": "1", "app_secret": "s"}, None, interactive=False) is False
    assert len(calls) == 1 and "access_token" not in credentials.load()


def test_setup_without_terminal_or_code_fails_clearly(home, monkeypatch):
    monkeypatch.setattr(onboarding, "claude_code_available", lambda: False)
    monkeypatch.setattr(onboarding, "claude_desktop_available", lambda: False)
    console = quiet()
    assert onboarding.setup(assume_yes=True, console=console) == 1
    assert "Not signed in" in text(console)


def test_doctor_reports_a_missing_sign_in(home, monkeypatch):
    monkeypatch.setattr(onboarding, "claude_code_registered", lambda: False)
    monkeypatch.setattr(onboarding, "claude_desktop_available", lambda: False)
    console = quiet()
    assert onboarding.doctor(console=console) == 1
    assert "pinterest-ads-mcp setup" in text(console)


# ── oauth ─────────────────────────────────────────────────────────────────────


def test_authorize_url_carries_scopes_and_state():
    url = oauth.authorize_url("123", "ads:read,ads:write", "xyz")
    assert url.startswith("https://www.pinterest.com/oauth/?")
    assert "client_id=123" in url and "state=xyz" in url and "ads%3Aread%2Cads%3Awrite" in url
    assert "redirect_uri=http%3A%2F%2Flocalhost%3A8080" in url


# ── playbooks ─────────────────────────────────────────────────────────────────


def test_playbooks_ship_with_the_package_and_carry_the_preface():
    names = [b["name"] for b in playbooks.load_all()]
    assert names == ["analysis-and-presentation", "attribution-northbeam", "budget-optimization",
                     "campaign-operations", "creative-management", "growth-marketer-playbook"]
    listing = json.loads(playbooks.pinterest_playbook())
    assert len(listing["playbooks"]) == 6 and all(p["description"] for p in listing["playbooks"])
    book = playbooks.pinterest_playbook("creative")
    assert book.startswith("# Playbook: creative-management")
    assert "pinterest_set_status" in book and "Creative Lifecycle" in book
    with pytest.raises(ValueError, match="Available"):
        playbooks.pinterest_playbook("nope")


def test_instructions_list_the_playbooks():
    from pinterest_ads_mcp import server

    instructions = server.mcp._mcp_server.instructions
    assert "budget-optimization" in instructions and "pinterest_playbook" in instructions


def test_northbeam_playbook_gives_the_exact_join(monkeypatch):
    from pinterest_ads_mcp import server

    book = playbooks.pinterest_playbook("northbeam")
    for needle in ("breakdown:Platform (Northbeam)", '"Pinterest"', "campaignId", "adsetId",
                   "adId", "small `limit`", "Never average or add"):
        assert needle in book, needle
    # The preface about PAMBS tool names does not belong on this one.
    assert "PAMBS" not in book
    instructions = server.mcp._mcp_server.instructions
    assert "Northbeam" in instructions and "do not ask the user to install" in instructions


# ── Claude Code registration ──────────────────────────────────────────────────


class FakeClaude:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def __call__(self, argv, **kwargs):
        import subprocess

        self.calls.append((argv[1:], kwargs))
        code, err = self.results.pop(0)
        return subprocess.CompletedProcess(argv, code, "", err)


def patch_claude(monkeypatch, results):
    fake = FakeClaude(results)
    monkeypatch.setattr(onboarding.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(onboarding.subprocess, "run", fake)
    return fake


def test_claude_cli_never_inherits_the_terminal(monkeypatch):
    import subprocess

    fake = patch_claude(monkeypatch, [(0, "")])
    assert onboarding.register_claude_code(["/bin/pinterest-ads-mcp"]) == (True, "Added to Claude Code.")
    argv, kwargs = fake.calls[0]
    assert argv == ["mcp", "add", "--scope", "user", "pinterest-ads", "--", "/bin/pinterest-ads-mcp"]
    # Inheriting a /dev/tty stdin crashes the Claude CLI (EINVAL kqueue).
    assert kwargs["stdin"] is subprocess.DEVNULL


def test_existing_registration_is_replaced(monkeypatch):
    fake = patch_claude(monkeypatch, [(1, "MCP server pinterest-ads already exists in user config"),
                                      (0, ""), (0, "")])
    ok, _ = onboarding.register_claude_code(["/bin/x"])
    assert ok and [c[0][1] for c in fake.calls] == ["add", "remove", "add"]


def test_registration_failure_gives_one_line_and_the_manual_command(monkeypatch):
    crash = "45 | return applyHandlers()\n46 | }\nerror: EINVAL: invalid argument, kqueue\n  at emitError (node:events:50:13)"
    patch_claude(monkeypatch, [(1, crash)])
    ok, message = onboarding.register_claude_code(["/Users/a b/bin/pinterest-ads-mcp"])
    assert not ok and "applyHandlers" not in message
    assert "error: EINVAL" in message
    assert 'claude mcp add --scope user pinterest-ads -- "/Users/a b/bin/pinterest-ads-mcp"' in message


# ── Claude desktop app ────────────────────────────────────────────────────────


def desktop(monkeypatch, tmp_path, running):
    config = tmp_path / "claude_desktop_config.json"
    events = []
    monkeypatch.setattr(onboarding, "claude_code_available", lambda: False)
    monkeypatch.setattr(onboarding, "claude_desktop_available", lambda: True)
    monkeypatch.setattr(onboarding, "desktop_config_path", lambda: config)
    monkeypatch.setattr(onboarding, "claude_desktop_running", lambda: running)
    monkeypatch.setattr(onboarding, "quit_claude_desktop", lambda: events.append("quit") or True)
    monkeypatch.setattr(onboarding, "open_claude_desktop", lambda: events.append("open"))
    return config, events


def test_open_desktop_app_is_restarted_around_the_config_change(monkeypatch, tmp_path):
    config, events = desktop(monkeypatch, tmp_path, running=True)
    monkeypatch.setattr(onboarding.Confirm, "ask", lambda *a, **k: True)
    console = quiet()
    assert onboarding.connect_claude(console, assume_yes=False) is True
    assert events == ["quit", "open"]
    assert "pinterest-ads" in json.loads(config.read_text())["mcpServers"]


def test_open_desktop_app_without_restart_is_reported_as_not_connected(monkeypatch, tmp_path):
    config, events = desktop(monkeypatch, tmp_path, running=True)
    console = quiet()
    # --yes never closes someone's app; it warns that the change may not stick.
    assert onboarding.connect_claude(console, assume_yes=True) is False
    assert events == [] and "may not stick" in text(console)


def test_closed_desktop_app_just_gets_the_entry(monkeypatch, tmp_path):
    config, events = desktop(monkeypatch, tmp_path, running=False)
    assert onboarding.connect_claude(quiet(), assume_yes=True) is True
    assert events == [] and "pinterest-ads" in json.loads(config.read_text())["mcpServers"]
