"""Browser sign-in with Pinterest (OAuth 2 authorization code flow)."""

from __future__ import annotations

import base64
import http.server
import secrets
import threading
import time
import urllib.parse
import webbrowser

import httpx

from .client import TOKEN_URL

AUTH_URL = "https://www.pinterest.com/oauth/"
REDIRECT_URI = "http://localhost:8080"
ADS_SCOPES = "ads:read,ads:write"
# Ads plus what is needed to see creatives and build new ones.
FULL_SCOPES = (
    "ads:read,ads:write,pins:read,pins:write,boards:read,boards:write,"
    "user_accounts:read,catalogs:read"
)

DONE_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Pinterest Ads MCP</title>
<style>body{{font:16px/1.5 -apple-system,system-ui,sans-serif;display:grid;place-items:center;
height:100vh;margin:0;background:#fafafa;color:#111}}div{{text-align:center}}
h1{{font-size:22px;margin:0 0 6px}}p{{color:#666;margin:0}}</style></head>
<body><div><h1>{title}</h1><p>{detail}</p></div></body></html>"""


class SignInError(Exception):
    """Sign-in did not complete. `reason` is Pinterest's error code when known."""

    def __init__(self, message: str, reason: str = ""):
        super().__init__(message)
        self.reason = reason


def authorize_url(app_id: str, scopes: str, state: str) -> str:
    return AUTH_URL + "?" + urllib.parse.urlencode(
        {
            "client_id": app_id,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "scope": scopes,
            "state": state,
        }
    )


def wait_for_code(state: str, timeout: int = 300) -> str:
    """Serve localhost:8080 until Pinterest redirects back with a code."""
    result: dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if not params:
                # Not the OAuth callback (e.g. /favicon.ico).
                self.send_response(204)
                self.end_headers()
                return
            if params.get("state", [""])[0] != state:
                result["error"] = "state_mismatch"
            elif "code" in params:
                result["code"] = params["code"][0]
            else:
                result["error"] = params.get("error", ["no_code"])[0]
            ok = "code" in result
            page = DONE_PAGE.format(
                title="You're connected" if ok else "Sign-in didn't finish",
                detail="You can close this tab and go back to the terminal."
                if ok
                else f"Pinterest said: {result.get('error')}. Go back to the terminal.",
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(page.encode())

        def log_message(self, *_):
            pass

    try:
        server = http.server.HTTPServer(("localhost", 8080), Handler)
    except OSError as exc:
        raise SignInError(
            "Port 8080 is in use, and Pinterest needs it for sign-in. "
            "Close whatever is using it and try again.",
            "port_in_use",
        ) from exc
    server.timeout = 1
    deadline = time.time() + timeout

    def serve() -> None:
        while not result and time.time() < deadline:
            server.handle_request()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    thread.join(timeout + 5)
    server.server_close()
    if "code" not in result:
        reason = result.get("error", "timeout")
        raise SignInError(f"Sign-in did not complete ({reason}).", reason)
    return result["code"]


def exchange(app_id: str, app_secret: str, code: str) -> dict:
    """Trade the code for tokens. Returns fields ready for credentials.update()."""
    basic = base64.b64encode(f"{app_id}:{app_secret}".encode()).decode()
    resp = httpx.post(
        TOKEN_URL,
        headers={"Authorization": f"Basic {basic}"},
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            # Ask for a refresh token that renews itself on each refresh.
            "continuous_refresh": "true",
        },
        timeout=30,
    )
    if resp.status_code != 200:
        hint = " Check the app id and secret." if resp.status_code in (400, 401) else ""
        raise SignInError(
            f"Pinterest refused the sign-in (HTTP {resp.status_code}).{hint} {resp.text[:200]}",
            "exchange_failed",
        )
    data = resp.json()
    fields = {
        "app_id": app_id,
        "app_secret": app_secret,
        "access_token": data["access_token"],
        "scope": data.get("scope", ""),
    }
    if data.get("refresh_token"):
        fields["refresh_token"] = data["refresh_token"]
    if data.get("expires_in"):
        fields["expires_at"] = int(time.time()) + int(data["expires_in"])
    return fields


def sign_in(app_id: str, app_secret: str, scopes: str = FULL_SCOPES, on_url=None) -> dict:
    """Run the whole flow: open the browser, wait, exchange. Returns token fields."""
    state = secrets.token_urlsafe(16)
    url = authorize_url(app_id, scopes, state)
    if on_url:
        on_url(url)
    webbrowser.open(url)
    code = wait_for_code(state)
    return exchange(app_id, app_secret, code)
