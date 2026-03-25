"""
Skills loader — progressive loading of PAMBS domain playbooks.

Level 1 (metadata):  Each skill's name + description is always available.
                     ~100 tokens per skill, loaded at orchestrator startup.
Level 2 (content):   Full skill markdown is loaded on demand when relevant.
                     Only the skills needed for a given run enter the context.

Usage:
    from skills.loader import build_skill_context, list_skill_metadata

    # Always include in system prompt (Level 1 — lightweight)
    metadata_block = list_skill_metadata()

    # Inject into user context before calling Claude (Level 2 — full content)
    playbook = build_skill_context(["budget-optimization", "creative-management"])
"""

import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

_SKILLS_DIR = Path(__file__).parent


def _parse_skill_file(path: Path) -> dict:
    """Return {name, description, content} parsed from a SKILL.md file."""
    raw = path.read_text(encoding="utf-8")
    name = path.stem  # fallback: filename without extension
    description = ""
    body = raw

    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) >= 3:
            frontmatter = parts[1]
            body = parts[2].strip()
            # Simple key: value parse — no yaml dep required
            for line in frontmatter.splitlines():
                if line.startswith("name:"):
                    name = line.split(":", 1)[1].strip()
                elif line.startswith("description:"):
                    description = line.split(":", 1)[1].strip()

    return {"name": name, "description": description, "content": body, "path": path}


def _all_skills() -> list[dict]:
    """Return parsed metadata + content for every *.md file in the skills dir."""
    skills = []
    for path in sorted(_SKILLS_DIR.glob("*.md")):
        try:
            skills.append(_parse_skill_file(path))
        except Exception as exc:
            log.warning("Could not parse skill file %s: %s", path.name, exc)
    return skills


# ── Public API ────────────────────────────────────────────────────────────────

def list_skill_metadata() -> str:
    """
    Return a compact metadata block for ALL skills (Level 1 loading).
    Inject this into the orchestrator system prompt so Claude knows what
    playbooks exist without paying the full content cost upfront.

    ~100 tokens total for three skills.
    """
    skills = _all_skills()
    if not skills:
        return ""
    lines = ["## Available Playbooks\n"]
    for s in skills:
        lines.append(f"- **{s['name']}**: {s['description']}")
    return "\n".join(lines)


def load_skill(name: str) -> str:
    """
    Return the full content of a single named skill (Level 2 loading).
    Raises FileNotFoundError if the skill does not exist.
    """
    for skill in _all_skills():
        if skill["name"] == name:
            return skill["content"]
    raise FileNotFoundError(f"Skill '{name}' not found in {_SKILLS_DIR}")


def build_skill_context(names: list[str]) -> str:
    """
    Build a combined playbook context block from a list of skill names.
    Returns a single string ready to inject into the user message or system prompt.

    Example:
        build_skill_context(["budget-optimization", "campaign-operations"])
    """
    if not names:
        return ""

    sections = []
    for name in names:
        try:
            content = load_skill(name)
            sections.append(f"<playbook name=\"{name}\">\n{content}\n</playbook>")
        except FileNotFoundError:
            log.warning("Requested skill '%s' not found — skipping.", name)

    if not sections:
        return ""

    return (
        "<playbooks>\n"
        "The following playbooks define the exact rules and thresholds "
        "you must follow when taking actions. Do not deviate from these rules.\n\n"
        + "\n\n".join(sections)
        + "\n</playbooks>"
    )


def select_skills_for_snapshot(snapshot: dict) -> list[str]:
    """
    Determine which skills are relevant for a given account snapshot.
    Returns a list of skill names to load (Level 2 selective loading).

    Always loads:
      - growth-marketer-playbook — foundational decision framework for every run
      - budget-optimization — budget decisions happen every run

    Conditionally loads:
      - creative-management — always, as creatives are evaluated every run
      - campaign-operations — if paused campaigns, pending hypotheses, or recent
                              campaign-level actions exist
    """
    # Core skills loaded on every run
    skills = ["growth-marketer-playbook", "budget-optimization", "creative-management"]

    campaigns = snapshot.get("campaigns", [])
    paused_campaigns = [c for c in campaigns if c.get("status") == "paused"]
    pending_hypotheses = snapshot.get("pending_hypotheses", [])
    recent_actions = snapshot.get("recent_agent_actions", [])

    # Campaign operations: load if paused campaigns exist or hypotheses are pending
    if paused_campaigns or pending_hypotheses:
        skills.append("campaign-operations")
    else:
        campaign_action_names = {"pause_campaign", "resume_campaign", "propose_hypothesis", "add_keyword"}
        if any(a.get("tool_name") in campaign_action_names for a in recent_actions):
            skills.append("campaign-operations")

    return skills
