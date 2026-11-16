"""Where the Pinterest token lives.

Credentials are kept in one JSON file outside the repo (mode 0600):

    ~/.config/pinterest-ads-mcp/credentials.json

    {
      "access_token":  "pina_...",
      "refresh_token": "pinr_...",      # optional, enables auto-refresh
      "app_id":        "1234567",       # optional, needed for refresh
      "app_secret":    "...",           # optional, needed for refresh
      "ad_account_id": "549000000000",  # default for {ad_account_id} in paths
      "expires_at":    1790000000,      # unix seconds, optional
      "scope":         "ads:read,ads:write"
    }

PINTEREST_ACCESS_TOKEN and PINTEREST_AD_ACCOUNT_ID in the environment override
the file, which is handy for one-off runs and for tests.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

DEFAULT_PATH = "~/.config/pinterest-ads-mcp/credentials.json"


def path() -> Path:
    return Path(os.environ.get("PINTEREST_MCP_CREDENTIALS", DEFAULT_PATH)).expanduser()


def load() -> dict:
    """Return stored credentials with environment overrides applied."""
    data: dict = {}
    p = path()
    if p.exists():
        data = json.loads(p.read_text())
    if os.environ.get("PINTEREST_ACCESS_TOKEN"):
        data["access_token"] = os.environ["PINTEREST_ACCESS_TOKEN"]
        # A token injected through the environment is not ours to refresh.
        data["from_env"] = True
    if os.environ.get("PINTEREST_AD_ACCOUNT_ID"):
        data["ad_account_id"] = os.environ["PINTEREST_AD_ACCOUNT_ID"]
    return data


def save(data: dict) -> Path:
    """Write credentials atomically with owner-only permissions."""
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(p.parent, 0o700)
    clean = {k: v for k, v in data.items() if k != "from_env" and v not in (None, "")}
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".credentials-")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(clean, fh, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, p)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return p


def update(**fields) -> dict:
    """Merge fields into the stored file (ignores environment overrides)."""
    p = path()
    data = json.loads(p.read_text()) if p.exists() else {}
    data.update(fields)
    save(data)
    return data
