"""HTTP client for the Pinterest v5 API: auth, token refresh, retries."""

from __future__ import annotations

import base64
import json
import re
import time
from pathlib import Path
from typing import Any

import httpx

from . import credentials
from .spec import Operation, Spec

BASE_URL = "https://api.pinterest.com/v5"
TOKEN_URL = f"{BASE_URL}/oauth/token"
FILE_PREFIX = "@base64:"
PLACEHOLDER = re.compile(r"\{([^/{}]+)\}")


class PinterestError(Exception):
    def __init__(self, status: int, payload: Any, hint: str = ""):
        self.status = status
        self.payload = payload
        self.hint = hint
        detail = payload if isinstance(payload, str) else json.dumps(payload)
        message = f"Pinterest API returned HTTP {status}: {detail}"
        if hint:
            message += f"\n{hint}"
        super().__init__(message)


def expand_files(value: Any) -> Any:
    """Replace "@base64:/path/to/file" strings with the file's base64 content.

    Lets a caller attach an image (e.g. `media_source.data` on pins/create)
    without pushing megabytes of base64 through the conversation.
    """
    if isinstance(value, dict):
        return {k: expand_files(v) for k, v in value.items()}
    if isinstance(value, list):
        return [expand_files(v) for v in value]
    if isinstance(value, str) and value.startswith(FILE_PREFIX):
        file_path = Path(value[len(FILE_PREFIX):]).expanduser()
        if not file_path.is_file():
            raise ValueError(f"File not found: {file_path}")
        return base64.b64encode(file_path.read_bytes()).decode()
    return value


class Client:
    def __init__(
        self,
        spec: Spec,
        creds: dict | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep=time.sleep,
    ):
        self.spec = spec
        self.creds = creds if creds is not None else credentials.load()
        self._persist = creds is None
        self._http = httpx.Client(timeout=60, transport=transport)
        self._sleep = sleep

    # ── request building ──────────────────────────────────────────────────────

    def normalize_path(self, path: str) -> str:
        """Accept '/campaigns', 'v5/campaigns', or a full api.pinterest.com URL."""
        path = path.strip()
        if path.startswith("http"):
            if not path.startswith(BASE_URL):
                raise ValueError(f"Only {BASE_URL} can be called, got: {path}")
            path = path[len(BASE_URL):]
        path = "/" + path.lstrip("/")
        if path == "/v5" or path.startswith("/v5/"):
            path = path[3:] or "/"

        def fill(match: re.Match) -> str:
            name = match.group(1)
            if name == "ad_account_id" and self.creds.get("ad_account_id"):
                return str(self.creds["ad_account_id"])
            raise ValueError(
                f"Path still contains the placeholder {{{name}}}. Replace it with a real value."
                + (
                    " No default ad account is configured."
                    if name == "ad_account_id"
                    else ""
                )
            )

        return PLACEHOLDER.sub(fill, path)

    def encode_query(self, op: Operation | None, query: dict | None) -> list[tuple[str, str]]:
        pairs: list[tuple[str, str]] = []
        for key, value in (query or {}).items():
            if value is None:
                continue
            if isinstance(value, (list, tuple)):
                # Always comma-joined. Verified against the live API: when a list
                # parameter is repeated (ids=1&ids=2) Pinterest silently uses only
                # the first value, whatever the published spec says about style.
                pairs.append((key, ",".join(self._scalar(v) for v in value)))
            else:
                pairs.append((key, self._scalar(value)))
        return pairs

    @staticmethod
    def _scalar(value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (dict, list)):
            return json.dumps(value)
        return str(value)

    # ── auth ──────────────────────────────────────────────────────────────────

    def can_refresh(self) -> bool:
        c = self.creds
        return bool(
            c.get("refresh_token") and c.get("app_id") and c.get("app_secret")
        ) and not c.get("from_env")

    def refresh(self) -> None:
        c = self.creds
        basic = base64.b64encode(f"{c['app_id']}:{c['app_secret']}".encode()).decode()
        resp = self._http.post(
            TOKEN_URL,
            headers={"Authorization": f"Basic {basic}"},
            data={"grant_type": "refresh_token", "refresh_token": c["refresh_token"]},
        )
        if resp.status_code != 200:
            raise PinterestError(
                resp.status_code,
                _parse(resp),
                "Token refresh failed. Run `pinterest-ads-mcp auth` to sign in again.",
            )
        data = resp.json()
        fields = {"access_token": data["access_token"]}
        if data.get("refresh_token"):
            fields["refresh_token"] = data["refresh_token"]
        if data.get("expires_in"):
            fields["expires_at"] = int(time.time()) + int(data["expires_in"])
        if data.get("scope"):
            fields["scope"] = data["scope"]
        self.creds.update(fields)
        if self._persist:
            credentials.update(**fields)

    def _token(self) -> str:
        token = self.creds.get("access_token")
        if not token:
            raise PinterestError(
                401, "No Pinterest access token configured.",
                "Run `pinterest-ads-mcp auth` in the repo to sign in.",
            )
        expires_at = self.creds.get("expires_at")
        if expires_at and expires_at - time.time() < 3600 and self.can_refresh():
            self.refresh()
            token = self.creds["access_token"]
        return token

    # ── calls ─────────────────────────────────────────────────────────────────

    def request(
        self,
        method: str,
        path: str,
        query: dict | None = None,
        body: Any = None,
    ) -> Any:
        method = method.upper()
        path = self.normalize_path(path)
        op = self.spec.match(method, path)
        params = self.encode_query(op, query)
        form = bool(
            op
            and "application/x-www-form-urlencoded"
            in op.raw.get("requestBody", {}).get("content", {})
        )
        # Only calls that cannot change anything are safe to repeat on a 5xx.
        idempotent = method == "GET" or bool(op and op.read_only)

        refreshed = False
        attempt = 0
        while True:
            attempt += 1
            kwargs: dict[str, Any] = {
                "params": params,
                "headers": {"Authorization": f"Bearer {self._token()}"},
            }
            if body is not None:
                kwargs["data" if form else "json"] = body
            resp = self._http.request(method, BASE_URL + path, **kwargs)

            if resp.status_code == 401 and not refreshed and self.can_refresh():
                refreshed = True
                self.refresh()
                continue
            if resp.status_code == 429 and attempt <= 2:
                wait = min(float(resp.headers.get("Retry-After") or 5), 20)
                self._sleep(wait)
                continue
            if resp.status_code >= 500 and idempotent and attempt <= 2:
                self._sleep(attempt)
                continue
            break

        payload = _parse(resp)
        if resp.status_code >= 400:
            raise PinterestError(resp.status_code, payload, self._hint(resp.status_code, op))
        return payload

    def _hint(self, status: int, op: Operation | None) -> str:
        if status == 401:
            if self.creds.get("scope") and op and op.scopes:
                return (
                    f"This endpoint needs scopes {op.scopes}; the stored token has "
                    f"'{self.creds['scope']}'. If the scope is missing, re-run "
                    "`pinterest-ads-mcp auth --scopes ...`. Otherwise the token has expired."
                )
            needs = f" This endpoint needs scopes {op.scopes}." if op and op.scopes else ""
            return (
                "The token is expired or lacks the scope for this endpoint." + needs
                + " Run `pinterest-ads-mcp auth` in the repo to sign in again."
            )
        if status == 403:
            return "The token's user does not have access to this resource or ad account."
        if status == 429:
            return "Rate limited by Pinterest. Wait a minute before retrying."
        return ""

    def download(self, url: str) -> httpx.Response:
        """Fetch a pre-signed result URL (reports). No auth header is sent."""
        resp = self._http.get(url, follow_redirects=True, timeout=120)
        resp.raise_for_status()
        return resp

    def upload_form(self, url: str, fields: dict, file_path: Path) -> None:
        """POST a file to the pre-signed upload URL returned by /media."""
        with file_path.open("rb") as fh:
            resp = self._http.post(
                url,
                data=fields,
                files={"file": (file_path.name, fh)},
                timeout=600,
            )
        if resp.status_code >= 300:
            raise PinterestError(resp.status_code, resp.text[:500], "Media upload was rejected.")


def upload_video(api: "Client", source: Path, wait_seconds: int = 300, sleep=time.sleep) -> dict:
    """Register a video, send the file, wait for processing. Returns its state."""
    registered = api.request("POST", "/media", body={"media_type": "video"})
    api.upload_form(registered["upload_url"], registered.get("upload_parameters") or {}, source)
    media_id = registered["media_id"]
    deadline = time.time() + max(10, min(wait_seconds, 900))
    while True:
        state = api.request("GET", f"/media/{media_id}")
        if state.get("status") in ("succeeded", "failed") or time.time() > deadline:
            break
        sleep(5)
    if state.get("status") == "failed":
        raise ValueError(f"Pinterest could not process the video: {json.dumps(state)}")
    return {"media_id": media_id, "status": state.get("status")}


def _parse(resp: httpx.Response) -> Any:
    if not resp.content:
        return {}
    try:
        return resp.json()
    except ValueError:
        return resp.text[:2000]
