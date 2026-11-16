"""The guided setup: from a fresh install to a working Claude connection."""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import time
import zlib
from pathlib import Path

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Table
from rich.text import Text

from . import __version__, credentials, oauth, spec as spec_module
from .client import Client, PinterestError

SERVER_NAME = "pinterest-ads"
INVITE_PREFIX = "pam1_"
RED = "#E60023"  # Pinterest red
STEPS = 5
EXAMPLES = [
    "How did Pinterest do yesterday compared with the last 7 days?",
    "Which ads are winning this month? Include hook rate.",
    "What should I turn off?",
    "Scale the best campaign 20%.",
]


# ── small UI helpers ──────────────────────────────────────────────────────────


def banner(console: Console) -> None:
    title = Text("Pinterest Ads MCP", style=f"bold {RED}")
    title.append(f"  v{__version__}", style="dim")
    body = Text("Run your Pinterest ad account by talking to Claude.", style="white")
    console.print()
    console.print(Panel(Text.assemble(title, "\n", body), box=box.ROUNDED, border_style=RED, padding=(1, 3)))


def step(console: Console, number: int, title: str) -> None:
    console.print()
    console.print(Text.assemble((f" {number}/{STEPS} ", f"bold white on {RED}"), ("  " + title, "bold")))


def ok(console: Console, message: str) -> None:
    console.print(f"  [green]✓[/green] {message}")


def warn(console: Console, message: str) -> None:
    console.print(f"  [yellow]![/yellow] {message}")


def fail(console: Console, message: str) -> None:
    console.print(f"  [red]✗[/red] {message}")


def info(console: Console, message: str) -> None:
    console.print(f"    [dim]{message}[/dim]")


# ── team setup codes ──────────────────────────────────────────────────────────


def make_invite(app_id: str, app_secret: str, ad_account_id: str | None, context: str | None) -> str:
    """Pack what a teammate needs into one pasteable string.

    It carries the app secret, so it must be shared privately. It does NOT
    carry anyone's Pinterest login: each person still signs in as themselves.
    """
    payload = {"app_id": app_id, "app_secret": app_secret}
    if ad_account_id:
        payload["ad_account_id"] = ad_account_id
    if context:
        payload["context"] = context
    packed = zlib.compress(json.dumps(payload, separators=(",", ":")).encode(), 9)
    return INVITE_PREFIX + base64.urlsafe_b64encode(packed).decode().rstrip("=")


def read_invite(code: str) -> dict:
    code = "".join(code.split())
    if not code.startswith(INVITE_PREFIX):
        raise ValueError("That is not a setup code (it should start with pam1_).")
    body = code[len(INVITE_PREFIX):]
    try:
        packed = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
        payload = json.loads(zlib.decompress(packed))
    except Exception as exc:
        raise ValueError("That setup code is damaged. Copy it again in full.") from exc
    if not payload.get("app_id") or not payload.get("app_secret"):
        raise ValueError("That setup code is missing the Pinterest app details.")
    return payload


# ── connecting to Claude ──────────────────────────────────────────────────────


def server_command() -> list[str]:
    """The command Claude should run to start this server, as absolute paths."""
    project = Path(__file__).resolve().parents[2]
    if (project / "pyproject.toml").exists() and (project / "src").is_dir():
        # Running from a source checkout: keep following the working tree.
        uv = shutil.which("uv") or "uv"
        return [uv, "run", "--project", str(project), "pinterest-ads-mcp"]
    entry = Path(sys.argv[0])
    if entry.name.startswith("pinterest-ads-mcp") and entry.exists():
        return [str(entry.absolute())]
    found = shutil.which("pinterest-ads-mcp")
    if found:
        return [found]
    return [sys.executable, "-m", "pinterest_ads_mcp.cli"]


def desktop_config_path() -> Path | None:
    if sys.platform == "darwin":
        return Path("~/Library/Application Support/Claude/claude_desktop_config.json").expanduser()
    if sys.platform.startswith("win"):
        return Path(os.environ.get("APPDATA", "")) / "Claude" / "claude_desktop_config.json"
    return Path("~/.config/Claude/claude_desktop_config.json").expanduser()


def claude_code_available() -> bool:
    return shutil.which("claude") is not None


def claude_desktop_available() -> bool:
    path = desktop_config_path()
    return bool(path and path.parent.is_dir())


def _claude(*args: str) -> subprocess.CompletedProcess:
    """Run the Claude Code CLI detached from the terminal.

    Setup is often started with its input redirected from /dev/tty (the
    installer is piped into sh). The Claude CLI crashes on start if it inherits
    that ("EINVAL: invalid argument, kqueue"), so it never gets our stdin.
    """
    return subprocess.run(
        [shutil.which("claude") or "claude", *args],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=90,
    )


def manual_command(command: list[str]) -> str:
    quoted = " ".join(f'"{part}"' if " " in part else part for part in command)
    return f"claude mcp add --scope user {SERVER_NAME} -- {quoted}"


def register_claude_code(command: list[str]) -> tuple[bool, str]:
    if not shutil.which("claude"):
        return False, "Claude Code is not installed."
    add = ("mcp", "add", "--scope", "user", SERVER_NAME, "--", *command)
    try:
        done = _claude(*add)
        if done.returncode != 0 and "already exists" in (done.stderr + done.stdout).lower():
            # Point an earlier registration at this install.
            _claude("mcp", "remove", "--scope", "user", SERVER_NAME)
            done = _claude(*add)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return False, f"Could not run Claude Code ({type(exc).__name__}). Run this yourself:\n      {manual_command(command)}"
    if done.returncode != 0:
        lines = [l.strip() for l in (done.stderr or done.stdout).splitlines() if l.strip()]
        reason = next((l for l in lines if l.lower().startswith("error")), lines[-1] if lines else "unknown error")
        return False, (f"Claude Code did not accept it ({reason[:120]}). Run this yourself:\n"
                       f"      {manual_command(command)}")
    return True, "Added to Claude Code."


def claude_code_registered() -> bool | None:
    if not shutil.which("claude"):
        return None
    try:
        done = _claude("mcp", "get", SERVER_NAME)
    except (subprocess.TimeoutExpired, OSError):
        return None
    return done.returncode == 0


def claude_desktop_running() -> bool:
    if sys.platform != "darwin":
        return False
    done = subprocess.run(["pgrep", "-x", "Claude"], stdin=subprocess.DEVNULL, capture_output=True)
    return done.returncode == 0


def quit_claude_desktop(wait_seconds: int = 20) -> bool:
    """Ask the desktop app to quit and wait until it has."""
    subprocess.run(["osascript", "-e", 'tell application "Claude" to quit'],
                   stdin=subprocess.DEVNULL, capture_output=True)
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        if not claude_desktop_running():
            return True
        time.sleep(0.5)
    return False


def open_claude_desktop() -> None:
    subprocess.run(["open", "-a", "Claude"], stdin=subprocess.DEVNULL, capture_output=True)


def register_claude_desktop(command: list[str], path: Path | None = None) -> tuple[bool, str]:
    path = path or desktop_config_path()
    try:
        config = json.loads(path.read_text()) if path.exists() and path.read_text().strip() else {}
    except ValueError:
        return False, f"{path} is not valid JSON, so it was left alone."
    servers = config.setdefault("mcpServers", {})
    servers[SERVER_NAME] = {"command": command[0], "args": command[1:]}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2) + "\n")
    return True, "Added to the Claude desktop app."


# ── wizard steps ──────────────────────────────────────────────────────────────


def _client() -> Client:
    return Client(spec_module.load())


def _working_account(console: Console) -> dict | None:
    """The ad account the stored token can reach, or None."""
    creds = credentials.load()
    if not creds.get("access_token") or not creds.get("ad_account_id"):
        return None
    try:
        return _client().request("GET", "/ad_accounts/{ad_account_id}")
    except (PinterestError, Exception):
        return None


def connect_pinterest(console: Console, invite: dict | None, scopes: str | None,
                      interactive: bool = True) -> bool:
    stored = credentials.load()
    current = _working_account(console)
    if current and not invite:
        ok(console, f"Already connected to [bold]{current.get('name')}[/bold].")
        refreshable = bool(stored.get("refresh_token") and stored.get("app_secret"))
        if not refreshable:
            warn(console, "This sign-in cannot renew itself and will expire.")
        if not interactive or not Confirm.ask(
            "    Sign in again?", default=not refreshable, console=console
        ):
            return True

    app_id = (invite or {}).get("app_id") or stored.get("app_id")
    app_secret = (invite or {}).get("app_secret") or stored.get("app_secret")
    if invite:
        ok(console, "Team setup code accepted.")
    if (not app_id or not app_secret) and not interactive:
        fail(console, "Not signed in. Run `pinterest-ads-mcp setup` in a terminal, "
                      "or pass --code with a team setup code.")
        return False
    if not app_id or not app_secret:
        console.print(
            "    You need a Pinterest developer app. If your team has one, ask for a\n"
            "    [bold]setup code[/bold] and re-run with it. Otherwise create an app at\n"
            "    [link=https://developers.pinterest.com/apps/]developers.pinterest.com/apps[/link]"
            f" with redirect URI [bold]{oauth.REDIRECT_URI}[/bold]."
        )
        app_id = Prompt.ask("    App id", default=app_id or None, console=console).strip()
        app_secret = Prompt.ask("    App secret", password=True, console=console).strip()
        if not app_id or not app_secret:
            fail(console, "An app id and secret are required.")
            return False

    attempts = [scopes] if scopes else [oauth.FULL_SCOPES, oauth.ADS_SCOPES]
    for index, scope in enumerate(attempts):
        console.print("    Opening Pinterest in your browser. Approve access there.")
        try:
            with console.status("    Waiting for you to approve in the browser…", spinner="dots"):
                fields = oauth.sign_in(
                    app_id, app_secret, scope,
                    on_url=lambda url: info(console, f"If nothing opens, visit: {url}"),
                )
        except oauth.SignInError as exc:
            more = index + 1 < len(attempts)
            if more and exc.reason not in ("timeout", "port_in_use", "exchange_failed"):
                warn(console, "Pinterest did not grant creative access. Trying ads-only access.")
                continue
            fail(console, str(exc))
            return False
        credentials.update(**fields)
        granted = fields.get("scope", "")
        ok(console, "Signed in to Pinterest.")
        if "pins:write" not in granted:
            warn(console, "Reporting and edits will work. Building ads from new files needs "
                          "pin access your Pinterest app does not have yet.")
        if not fields.get("refresh_token"):
            warn(console, "Pinterest did not issue a renewable sign-in; you will need to "
                          "repeat this when it expires.")
        return True
    return False


def choose_ad_account(console: Console, preferred: str | None, interactive: bool = True) -> bool:
    api = _client()
    accounts: list[dict] = []
    query: dict = {"page_size": 100, "include_shared_accounts": True}
    try:
        for _ in range(10):
            page = api.request("GET", "/ad_accounts", query=query)
            accounts.extend(page.get("items") or [])
            if not page.get("bookmark"):
                break
            query["bookmark"] = page["bookmark"]
    except PinterestError as exc:
        fail(console, f"Could not list ad accounts: HTTP {exc.status}")
        return False
    if not accounts:
        fail(console, "This Pinterest login has no ad accounts. Ask for access to the "
                      "ad account in Pinterest Business Manager, then run setup again.")
        return False

    by_id = {str(a.get("id")): a for a in accounts}
    preferred = preferred or credentials.load().get("ad_account_id")
    if preferred and str(preferred) in by_id:
        chosen = by_id[str(preferred)]
    elif len(accounts) == 1:
        chosen = accounts[0]
    elif not interactive:
        fail(console, "Several ad accounts are available. Run setup in a terminal to choose one.")
        return False
    else:
        table = Table(box=box.SIMPLE_HEAD, padding=(0, 2), show_edge=False)
        table.add_column("#", style="dim", justify="right")
        table.add_column("Ad account")
        table.add_column("Id", style="dim")
        table.add_column("Currency", style="dim")
        for i, account in enumerate(accounts, 1):
            table.add_row(str(i), account.get("name") or "", str(account.get("id")), account.get("currency") or "")
        console.print(table)
        pick = IntPrompt.ask(
            "    Which ad account?", console=console,
            choices=[str(i) for i in range(1, len(accounts) + 1)], show_choices=False,
        )
        chosen = accounts[pick - 1]
    if preferred and str(preferred) not in by_id:
        warn(console, f"Your login cannot see ad account {preferred}; using a different one.")
    credentials.update(ad_account_id=str(chosen.get("id")))
    ok(console, f"Using [bold]{chosen.get('name')}[/bold] ({chosen.get('id')}, {chosen.get('currency')}).")
    return True


def context_path() -> Path:
    return credentials.path().parent / "context.md"


def write_context(console: Console, invite: dict | None, interactive: bool) -> None:
    path = context_path()
    shared = (invite or {}).get("context")
    if shared:
        if path.exists() and path.read_text().strip() and path.read_text() != shared:
            if not Confirm.ask("    Replace your account notes with your team's?", default=True, console=console):
                ok(console, "Kept your existing account notes.")
                return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(shared)
        ok(console, "Installed your team's account notes (targets, naming, rules).")
        return
    if path.exists() and path.read_text().strip():
        ok(console, f"Account notes found at {path}.")
        return
    if not interactive:
        warn(console, "No account notes yet. Ask Claude to \"set up my Pinterest account context\".")
        return
    console.print("    A few answers so Claude judges results against [bold]your[/bold] goals. "
                  "Press Enter to skip any.")
    brand = Prompt.ask("    Brand and hero product", default="", console=console)
    roas = Prompt.ask("    Target ROAS (e.g. 2.5)", default="", console=console)
    cpa = Prompt.ask("    Target CPA in dollars", default="", console=console)
    windows = Prompt.ask("    Attribution windows: click / engagement / view days",
                         default="30 / 30 / 1", console=console)
    landing = Prompt.ask("    Default landing page URL, with tracking parameters", default="", console=console)
    truth = Prompt.ask("    Attribution source of truth (e.g. Northbeam), if any", default="", console=console)
    if not any((brand, roas, cpa, landing, truth)):
        warn(console, "Skipped. Ask Claude to \"set up my Pinterest account context\" any time.")
        return
    notes = f"""# Pinterest account context

## Brand and offer
- Brand and hero product: {brand or "(not set)"}
- Default landing page (with tracking parameters): {landing or "(not set)"}

## Goals
- Target ROAS: {roas or "(not set)"}
- Target CPA: {("$" + cpa.lstrip("$")) if cpa else "(not set)"}
- Attribution windows to report on (click / engagement / view days): {windows}
- Attribution source of truth: {truth or "Pinterest"}

## Rules
- Confirm before any budget increase and before setting anything ACTIVE.
- Create new campaigns, ad groups and ads as PAUSED.
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(notes)
    ok(console, f"Saved to {path}. Edit it any time, or ask Claude to.")


def connect_claude(console: Console, assume_yes: bool) -> bool:
    command = server_command()
    connected = False
    if claude_code_available():
        success, message = register_claude_code(command)
        (ok if success else fail)(console, message)
        connected = connected or success
    else:
        info(console, "Claude Code not found (install it from claude.com/claude-code).")
    if claude_desktop_available():
        if assume_yes or Confirm.ask("    Add it to the Claude desktop app too?", default=True, console=console):
            # While it is open the app rewrites its settings file and drops our
            # entry, so the change only sticks if the app is closed first.
            was_running = claude_desktop_running()
            restarted = False
            if was_running and not assume_yes and Confirm.ask(
                "    The Claude desktop app is open and must restart to load the tools. "
                "Restart it now?", default=True, console=console,
            ):
                restarted = quit_claude_desktop()
                if not restarted:
                    warn(console, "Claude did not quit. Quit it yourself (Cmd+Q), then run setup again.")
            success, message = register_claude_desktop(command)
            if success and was_running and not restarted:
                warn(console, "The Claude desktop app is open, so this may not stick. Quit it "
                              "(Cmd+Q), then run [bold]pinterest-ads-mcp setup[/bold] again.")
            elif success:
                ok(console, message + ("" if not was_running else " Reopening it now."))
            else:
                fail(console, message)
            if restarted:
                open_claude_desktop()
            connected = connected or (success and (restarted or not was_running))
    if not connected:
        warn(console, "Not connected to Claude yet. Install Claude Code, then run "
                      "[bold]pinterest-ads-mcp setup[/bold] again.")
    return connected


def results_table(account: str, totals: dict, live_campaigns: int) -> Table:
    table = Table(box=box.ROUNDED, border_style="dim", padding=(0, 2),
                  title=f"{account} · last 7 days", title_style="bold")
    for column in ("Spend", "Revenue", "ROAS", "Purchases", "CPA", "Live campaigns"):
        table.add_column(column, justify="right")
    table.add_row(
        money(totals["spend"]), money(totals["revenue"]),
        "—" if totals["roas"] is None else f"{totals['roas']:.2f}",
        f"{totals['purchases']:,}", money(totals["cpa"]), str(live_campaigns),
    )
    return table


def money(value) -> str:
    return "—" if value is None else f"${value:,.0f}"


def verify(console: Console) -> bool:
    from . import buyer  # deferred: importing it builds the MCP server

    try:
        with console.status("    Pulling the last 7 days…", spinner="dots"):
            data = json.loads(buyer.pinterest_account_overview())
    except Exception as exc:  # Setup succeeded; a failed preview should not undo that.
        warn(console, f"Connected, but could not pull a preview: {str(exc)[:160]}")
        return False
    console.print(results_table(data["account"]["name"], data["account_totals"], data["active_campaigns"]))
    info(console, "Pinterest-attributed, 30-day click / 1-day view.")
    return True


def finish(console: Console, connected: bool) -> None:
    lines = Text()
    if connected:
        lines.append("You're set. Open a new Claude session and try:\n\n", style="bold")
    else:
        lines.append("Pinterest is connected. Once Claude is connected, try:\n\n", style="bold")
    for example in EXAMPLES:
        lines.append("  › ", style=RED)
        lines.append(example + "\n")
    lines.append("\nNothing changes in your ad account unless you ask for it.", style="dim")
    console.print()
    console.print(Panel(lines, box=box.ROUNDED, border_style="green" if connected else "yellow", padding=(1, 3)))
    console.print("  [dim]pinterest-ads-mcp doctor   check the connection\n"
                  "  pinterest-ads-mcp invite   onboard a teammate\n"
                  "  pinterest-ads-mcp update   get the latest version[/dim]\n")


def setup(code: str | None = None, scopes: str | None = None, assume_yes: bool = False,
          console: Console | None = None) -> int:
    console = console or Console()
    interactive = sys.stdin.isatty() and not assume_yes
    banner(console)

    invite = None
    if code:
        try:
            invite = read_invite(code)
        except ValueError as exc:
            fail(console, str(exc))
            return 1
    elif interactive and not _working_account(console) and not credentials.load().get("app_secret"):
        console.print()
        pasted = Prompt.ask("  Paste your team [bold]setup code[/bold], or press Enter if you don't have one",
                            default="", show_default=False, console=console)
        if pasted.strip():
            try:
                invite = read_invite(pasted)
            except ValueError as exc:
                fail(console, str(exc))
                return 1

    step(console, 1, "Check your computer")
    ok(console, f"Pinterest Ads MCP {__version__} installed.")
    if claude_code_available():
        ok(console, "Claude Code found.")
    if claude_desktop_available():
        ok(console, "Claude desktop app found.")
    if not claude_code_available() and not claude_desktop_available():
        warn(console, "Claude is not installed yet. Setup will continue; connect it afterwards.")

    step(console, 2, "Connect Pinterest")
    if not connect_pinterest(console, invite, scopes, interactive):
        return 1

    step(console, 3, "Choose your ad account")
    if not choose_ad_account(console, (invite or {}).get("ad_account_id"), interactive):
        return 1

    step(console, 4, "Tell Claude about the account")
    write_context(console, invite, interactive)

    step(console, 5, "Connect to Claude")
    connected = connect_claude(console, assume_yes or not interactive)

    console.print()
    verify(console)
    finish(console, connected)
    return 0


# ── other commands ────────────────────────────────────────────────────────────


def invite(include_context: bool = True, console: Console | None = None) -> int:
    console = console or Console()
    creds = credentials.load()
    if not creds.get("app_id") or not creds.get("app_secret"):
        fail(console, "No Pinterest app on file. Run [bold]pinterest-ads-mcp setup[/bold] first.")
        return 1
    notes = context_path().read_text() if include_context and context_path().exists() else None
    code = make_invite(creds["app_id"], creds["app_secret"], creds.get("ad_account_id"), notes)
    banner(console)
    console.print("\n  Send your teammate these two things.\n")
    console.print("  [bold]1. Install command[/bold] (paste into Terminal)\n")
    console.print(f"     [cyan]{INSTALL_COMMAND}[/cyan]\n", soft_wrap=True)
    console.print("  [bold]2. Setup code[/bold] (paste when setup asks for it)\n")
    console.print(f"     [cyan]{code}[/cyan]\n", soft_wrap=True)
    console.print(Panel(
        "The setup code contains your Pinterest app secret. Send it privately "
        "(password manager or direct message), never in a public channel.\n"
        "It does not contain your Pinterest login: each person signs in as themselves "
        "and needs their own access to the ad account."
        + ("\nIt includes your account notes, so they get the same targets and rules." if notes else ""),
        box=box.ROUNDED, border_style="yellow", padding=(0, 2)))
    return 0


INSTALL_COMMAND = (
    "curl -fsSL https://raw.githubusercontent.com/leofriend1048/Pinterest-management-/"
    "mcp-server/install.sh | sh"
)


def doctor(console: Console | None = None) -> int:
    console = console or Console()
    banner(console)
    creds = credentials.load()
    rows: list[tuple[str, bool | None, str]] = []

    def check(label: str, good: bool | None, detail: str) -> None:
        rows.append((label, good, detail))

    path = credentials.path()
    check("Credentials file", path.exists(), str(path) if path.exists() else "Missing. Run: pinterest-ads-mcp setup")
    if path.exists():
        private = (path.stat().st_mode & 0o077) == 0
        check("Readable only by you", private, "Yes" if private else f"Run: chmod 600 {path}")

    account = None
    problem = ""
    if creds.get("access_token") and creds.get("ad_account_id"):
        try:
            account = _client().request("GET", "/ad_accounts/{ad_account_id}")
        except PinterestError as exc:
            problem = f"HTTP {exc.status}. Run: pinterest-ads-mcp setup"
        except Exception as exc:
            problem = f"{type(exc).__name__}: check your internet connection"
    else:
        problem = "Not signed in. Run: pinterest-ads-mcp setup"
    check("Pinterest sign-in", bool(account),
          f"{account.get('name')} ({account.get('id')})" if account else problem)

    scope = creds.get("scope") or ""
    renew = bool(creds.get("refresh_token") and creds.get("app_secret"))
    check("Renews itself", renew, "Yes" if renew else "No, it will expire. Run: pinterest-ads-mcp setup")
    if creds.get("expires_at"):
        days = (creds["expires_at"] - time.time()) / 86400
        check("Token expiry", days > 0 or renew, f"in {days:.0f} days" + (" (renews automatically)" if renew else ""))
    creative = "pins:write" in scope
    check("Can build ads from new files", creative if scope else None,
          "Yes" if creative else ("Unknown: signed in before setup existed" if not scope
                                  else "No: the Pinterest app lacks pin access"))
    notes = context_path().exists()
    check("Account notes", notes or None, str(context_path()) if notes
          else "None yet. Ask Claude to \"set up my Pinterest account context\"")

    registered = claude_code_registered()
    check("Claude Code", registered,
          "Connected" if registered else ("Not installed" if registered is None
                                          else "Not connected. Run: pinterest-ads-mcp setup"))
    desktop = desktop_config_path()
    if claude_desktop_available():
        try:
            in_desktop = SERVER_NAME in json.loads(desktop.read_text()).get("mcpServers", {})
        except Exception:
            in_desktop = False
        check("Claude desktop app", in_desktop or None,
              "Connected" if in_desktop else "Not connected (optional). Run: pinterest-ads-mcp setup")
    check("Pinterest API description", True,
          f"v{spec_module.load().version}, {len(spec_module.load().operations)} endpoints")

    table = Table(box=box.SIMPLE_HEAD, padding=(0, 2), show_edge=False)
    table.add_column("")
    table.add_column("Check", style="bold")
    table.add_column("Result")
    marks = {True: "[green]✓[/green]", False: "[red]✗[/red]", None: "[yellow]![/yellow]"}
    for label, good, detail in rows:
        table.add_row(marks[good], label, detail)
    console.print()
    console.print(table)
    broken = [label for label, good, _ in rows if good is False]
    console.print()
    if broken:
        console.print(f"  [red]{len(broken)} thing(s) need attention.[/red]\n")
        return 1
    console.print("  [green]Everything needed is working.[/green]\n")
    return 0


def update(console: Console | None = None) -> int:
    console = console or Console()
    project = Path(__file__).resolve().parents[2]
    if (project / ".git").exists():
        console.print(f"  This is a source checkout. Update it with: [bold]git -C {project} pull && uv sync[/bold]")
        return 0
    uv = shutil.which("uv")
    if not uv:
        fail(console, "uv is not installed. Re-run the install command from the README.")
        return 1
    with console.status("  Updating…", spinner="dots"):
        done = subprocess.run([uv, "tool", "upgrade", "pinterest-ads-mcp"], capture_output=True, text=True)
    if done.returncode != 0:
        fail(console, (done.stderr or done.stdout).strip()[:400])
        return 1
    ok(console, "Up to date. Open a new Claude session to use the new version.")
    return 0
