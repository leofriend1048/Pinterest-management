#!/usr/bin/env python3
"""
One-time Pinterest OAuth2 token generator.

Run this script locally to obtain an access token with ads:read + ads:write
scopes and write it directly into your .env file.

Usage
-----
    python get_pinterest_token.py

Requirements
------------
  - App ID and App Secret from developers.pinterest.com/apps/1512290
  - Redirect URI http://localhost:8080 must be registered (it already is)
  - Run on the machine where you can open a browser

The token written to .env does NOT auto-refresh.  Pinterest access tokens
last ~30 days.  Re-run this script when the token expires.
"""

import base64
import http.server
import os
import re
import secrets
import threading
import urllib.parse
import urllib.request
import webbrowser

# ── Config ────────────────────────────────────────────────────────────────────

APP_ID       = "1512290"                    # from developer portal
REDIRECT_URI = "http://localhost:8080"
SCOPES       = "ads:read,ads:write"         # minimum required by PAMBS
TOKEN_URL    = "https://api.pinterest.com/v5/oauth/token"
AUTH_URL     = "https://www.pinterest.com/oauth/"
ENV_FILE     = os.path.join(os.path.dirname(__file__), ".env")

# ── OAuth2 flow ───────────────────────────────────────────────────────────────

_callback_code: str | None = None
_server_error:  str | None = None


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        global _callback_code, _server_error
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if "error" in params:
            _server_error = params["error"][0]
            msg = f"Authorization denied: {_server_error}"
        elif "code" in params:
            _callback_code = params["code"][0]
            msg = "Authorization successful — you can close this tab."
        else:
            msg = "Unexpected callback — no code or error parameter."

        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(
            f"<html><body><h2>{msg}</h2></body></html>".encode()
        )

    def log_message(self, *_):
        pass   # suppress access log noise


def _exchange_code(code: str, app_secret: str) -> dict:
    credentials = base64.b64encode(f"{APP_ID}:{app_secret}".encode()).decode()
    data = urllib.parse.urlencode({
        "grant_type":   "authorization_code",
        "code":         code,
        "redirect_uri": REDIRECT_URI,
    }).encode()
    req = urllib.request.Request(
        TOKEN_URL,
        data=data,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type":  "application/x-www-form-urlencoded",
        },
    )
    with urllib.request.urlopen(req) as resp:
        import json
        return json.loads(resp.read())


def _update_env(token: str) -> None:
    if os.path.exists(ENV_FILE):
        content = open(ENV_FILE).read()
        if re.search(r"^PINTEREST_ACCESS_TOKEN\s*=", content, re.MULTILINE):
            content = re.sub(
                r"^(PINTEREST_ACCESS_TOKEN\s*=).*",
                rf"\g<1>{token}",
                content,
                flags=re.MULTILINE,
            )
        else:
            content += f"\nPINTEREST_ACCESS_TOKEN={token}\n"
        open(ENV_FILE, "w").write(content)
    else:
        open(ENV_FILE, "w").write(f"PINTEREST_ACCESS_TOKEN={token}\n")
    print(f"  .env updated: PINTEREST_ACCESS_TOKEN written")


def main():
    print("Pinterest OAuth2 token generator")
    print("=" * 40)

    app_secret = os.environ.get("PINTEREST_APP_SECRET") or input(
        "Enter your App Secret (from developers.pinterest.com/apps/1512290): "
    ).strip()
    if not app_secret:
        raise SystemExit("App secret is required.")

    state = secrets.token_urlsafe(16)
    auth_params = urllib.parse.urlencode({
        "client_id":     APP_ID,
        "redirect_uri":  REDIRECT_URI,
        "response_type": "code",
        "scope":         SCOPES,
        "state":         state,
    })
    auth_link = f"{AUTH_URL}?{auth_params}"

    # Start local callback server
    server = http.server.HTTPServer(("localhost", 8080), _CallbackHandler)
    thread = threading.Thread(target=server.handle_request)  # handles one request then stops
    thread.start()

    print(f"\nOpening browser for Pinterest authorization...")
    print(f"Scopes requested: {SCOPES}")
    print(f"\nIf the browser doesn't open, visit:\n  {auth_link}\n")
    webbrowser.open(auth_link)

    thread.join(timeout=120)

    if _server_error:
        raise SystemExit(f"Authorization failed: {_server_error}")
    if not _callback_code:
        raise SystemExit("Timed out waiting for authorization callback.")

    print("Exchanging code for access token...")
    try:
        token_data = _exchange_code(_callback_code, app_secret)
    except Exception as exc:
        raise SystemExit(f"Token exchange failed: {exc}")

    access_token  = token_data.get("access_token")
    token_type    = token_data.get("token_type", "")
    expires_in    = token_data.get("expires_in", "unknown")
    granted_scopes = token_data.get("scope", "")

    if not access_token:
        raise SystemExit(f"No access_token in response: {token_data}")

    print(f"\nToken received:")
    print(f"  Type:    {token_type}")
    print(f"  Expires: {expires_in}s (~{int(expires_in)//86400} days)" if isinstance(expires_in, int) else f"  Expires: {expires_in}")
    print(f"  Scopes:  {granted_scopes}")

    # Verify ads:write was actually granted
    missing = [s for s in SCOPES.split(",") if s not in granted_scopes]
    if missing:
        print(f"\n  WARNING: the following scopes were NOT granted: {missing}")
        print(  "  The app may still be in Trial access — click 'Upgrade access'")
        print(  "  in the developer portal and re-run this script.\n")
    else:
        print(  "  ads:read + ads:write confirmed.")

    _update_env(access_token)
    print("\nDone. Restart PAMBS to pick up the new token.")


if __name__ == "__main__":
    main()
