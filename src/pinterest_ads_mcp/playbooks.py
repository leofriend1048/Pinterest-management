"""Media-buying playbooks, loaded on demand.

The server's instructions list each playbook in a line; the full text is only
pulled into the conversation when a decision calls for it.

This module has no dependency on the server so it can be imported on its own;
server.py registers `pinterest_playbook` as a tool.
"""

from __future__ import annotations

import json
from importlib import resources


# The playbooks were written for an autonomous system that had its own tools.
# This note travels with every playbook so its advice lands on today's tools.
PREFACE = """\
> How to read this playbook
> - It was written for an earlier autonomous system (PAMBS). Here nothing acts
>   on its own: treat every "do X" as a recommendation to put to the user, and
>   make a change only after they agree.
> - Tool names map as follows: adjust_campaign_budget -> pinterest_set_daily_budget;
>   pause_/resume_ creative or campaign -> pinterest_set_status;
>   graduate_creative -> pinterest_launch_ad with copy_from_ad_id;
>   add_keyword -> the keywords/create endpoint via pinterest_api_write;
>   propose_hypothesis -> write the hypothesis out for the user in the format shown.
> - "Northbeam ROAS" means ROAS from the account's attribution source of truth.
>   pinterest_performance returns Pinterest-attributed ROAS, which reads
>   differently; say which one a number is.
> - Targets and thresholds are examples. Use the target ROAS/CPA from
>   pinterest_account_context, and where a threshold is a share of target
>   (e.g. "90% of target") apply it to that account's target.
"""


def _parse(text: str, fallback: str) -> dict:
    name, description, body = fallback, "", text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            body = parts[2].strip()
            for line in parts[1].splitlines():
                if line.startswith("name:"):
                    name = line.split(":", 1)[1].strip()
                elif line.startswith("description:"):
                    description = line.split(":", 1)[1].strip()
    return {
        "name": name,
        "description": description.replace(" for PAMBS", ""),
        "raw_description": description,
        "content": body,
    }


def load_all() -> list[dict]:
    folder = resources.files("pinterest_ads_mcp").joinpath("playbooks")
    books = []
    for entry in sorted(folder.iterdir(), key=lambda e: e.name):
        if entry.name.endswith(".md"):
            books.append(_parse(entry.read_text(encoding="utf-8"), entry.name[:-3]))
    return books


def index() -> str:
    """One line per playbook, for the server instructions."""
    return "\n".join(
        f"- {b['name']}: {b['description']}" for b in load_all()
    )


def pinterest_playbook(name: str | None = None) -> str:
    """Read a media-buying playbook: how to decide, not just what the numbers are.

    Call with no name to list the playbooks. Read the relevant one BEFORE
    recommending a budget change, pausing or resuming anything, judging whether
    a creative is fatigued or ready to scale, diagnosing why ROAS moved, or
    proposing a test. They cover priority triage, trend windows, scale / hold /
    cut tiers and their guards, minimum-spend gates, creative lifecycle and
    decay rules, campaign pause and resume rules, and how to write a hypothesis.
    """
    books = load_all()
    if not name:
        return json.dumps(
            {"playbooks": [{"name": b["name"], "description": b["description"]} for b in books]},
            indent=1,
        )
    wanted = name.strip().lower().replace("_", "-")
    for book in books:
        if book["name"].lower() == wanted or wanted in book["name"].lower():
            # Only the playbooks inherited from PAMBS need the translation note.
            preface = PREFACE + "\n" if "PAMBS" in book["raw_description"] else ""
            return f"# Playbook: {book['name']}\n\n{preface}{book['content']}"
    raise ValueError(
        f"No playbook '{name}'. Available: " + ", ".join(b["name"] for b in books)
    )
