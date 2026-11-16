"""Command line entry point.

    pinterest-ads-mcp setup      guided setup: sign in, pick an account, connect Claude
    pinterest-ads-mcp doctor     check that everything is working
    pinterest-ads-mcp invite     make a setup code for a teammate
    pinterest-ads-mcp update     get the latest version
    pinterest-ads-mcp            (started by Claude) run the MCP server over stdio
"""

from __future__ import annotations

import argparse
import getpass
import os
import subprocess
import sys

from . import __version__, credentials, oauth


def _ask_secret(app_id: str) -> str:
    """Prompt for the app secret without echoing it.

    In a terminal this is a normal hidden prompt. Without one (for example when
    an agent runs the command) macOS shows a native password dialog instead, so
    the secret is typed by the person and never appears in a transcript.
    """
    prompt = f"App secret for Pinterest app {app_id} (developers.pinterest.com/apps/{app_id})"
    if sys.stdin.isatty():
        return getpass.getpass(prompt + ": ").strip()
    if sys.platform == "darwin":
        script = (
            f'display dialog "{prompt}" default answer "" with hidden answer '
            'with title "pinterest-ads-mcp" buttons {"Cancel", "Continue"} '
            'default button "Continue"\ntext returned of result'
        )
        done = subprocess.run(
            ["osascript", "-e", 'tell application "System Events" to activate', "-e", script],
            capture_output=True,
            text=True,
        )
        return done.stdout.strip()
    raise SystemExit("No terminal to prompt in. Set PINTEREST_APP_SECRET and re-run.")


def auth(args: argparse.Namespace) -> int:
    """Sign in only, without the rest of setup. Scriptable."""
    stored = credentials.load()
    app_id = args.app_id or stored.get("app_id") or os.environ.get("PINTEREST_APP_ID")
    if not app_id:
        raise SystemExit("Pass --app-id (from developers.pinterest.com/apps).")
    app_secret = stored.get("app_secret") if not args.reset_secret else None
    app_secret = app_secret or os.environ.get("PINTEREST_APP_SECRET") or _ask_secret(app_id)
    if not app_secret:
        raise SystemExit("An app secret is required.")

    print(f"Requesting scopes: {args.scopes}")
    print("If Pinterest rejects a scope, re-run with: --scopes ads:read,ads:write")
    try:
        fields = oauth.sign_in(
            app_id, app_secret, args.scopes,
            on_url=lambda url: print(f"\nOpening the browser. If it does not open, visit:\n  {url}\n"),
        )
    except oauth.SignInError as exc:
        raise SystemExit(str(exc)) from None
    if args.ad_account_id:
        fields["ad_account_id"] = args.ad_account_id
    saved = credentials.update(**fields)

    granted = set(fields["scope"].replace(" ", ",").split(","))
    missing = [s for s in args.scopes.split(",") if s not in granted]
    print(f"Saved to {credentials.path()}")
    print(f"  scopes granted : {fields['scope']}")
    if missing:
        print(f"  NOT granted    : {', '.join(missing)}")
    print(f"  auto-refresh   : {'yes' if fields.get('refresh_token') else 'no'}")
    print(f"  ad account     : {saved.get('ad_account_id') or 'not set (pass --ad-account-id)'}")
    return 0


def status(_: argparse.Namespace) -> int:
    from .server import pinterest_auth_status

    print(pinterest_auth_status())
    return 0


def welcome() -> int:
    """Shown when a person runs the bare command in a terminal."""
    from rich.console import Console

    from . import onboarding

    console = Console()
    onboarding.banner(console)
    console.print(
        "\n  [bold]pinterest-ads-mcp setup[/bold]    connect Pinterest and Claude (start here)\n"
        "  [bold]pinterest-ads-mcp doctor[/bold]   check that everything is working\n"
        "  [bold]pinterest-ads-mcp invite[/bold]   make a setup code for a teammate\n"
        "  [bold]pinterest-ads-mcp update[/bold]   get the latest version\n"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="pinterest-ads-mcp",
        description="Run your Pinterest ad account by talking to Claude.",
    )
    parser.add_argument("--version", action="version", version=f"pinterest-ads-mcp {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="command")

    p_setup = sub.add_parser("setup", help="guided setup: sign in, pick an account, connect Claude")
    p_setup.add_argument("--code", help="team setup code from `pinterest-ads-mcp invite`")
    p_setup.add_argument("--scopes", help="override the Pinterest OAuth scopes to request")
    p_setup.add_argument("--yes", action="store_true", help="accept defaults without asking")

    sub.add_parser("doctor", help="check that everything is working")

    p_invite = sub.add_parser("invite", help="make a setup code for a teammate")
    p_invite.add_argument("--no-context", action="store_true", help="leave your account notes out")

    sub.add_parser("update", help="get the latest version")
    sub.add_parser("serve", help="run the MCP server over stdio (what Claude starts)")

    p_auth = sub.add_parser("auth", help="sign in only (advanced)")
    p_auth.add_argument("--scopes", default=oauth.FULL_SCOPES, help="comma-separated OAuth scopes")
    p_auth.add_argument("--app-id", help="Pinterest app id (remembered after the first sign-in)")
    p_auth.add_argument("--ad-account-id", help="default ad account for {ad_account_id}")
    p_auth.add_argument("--reset-secret", action="store_true", help="prompt for the app secret again")

    sub.add_parser("status", help="print the connection status as JSON (advanced)")

    args = parser.parse_args()
    if args.command == "setup":
        from . import onboarding

        try:
            return onboarding.setup(code=args.code, scopes=args.scopes, assume_yes=args.yes)
        except KeyboardInterrupt:
            print("\n  Setup cancelled. Run `pinterest-ads-mcp setup` to pick it up again.")
            return 130
    if args.command == "doctor":
        from . import onboarding

        return onboarding.doctor()
    if args.command == "invite":
        from . import onboarding

        return onboarding.invite(include_context=not args.no_context)
    if args.command == "update":
        from . import onboarding

        return onboarding.update()
    if args.command == "auth":
        return auth(args)
    if args.command == "status":
        return status(args)
    if args.command is None and sys.stdin.isatty():
        # A person typed the bare command. Claude starts it with piped stdin.
        return welcome()

    from .server import run

    run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
