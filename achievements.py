"""
achievements.py — Achievement registry and scanner.

GitHub does NOT expose a public Achievements API.
We infer achievement progress from:
  - Merged PRs (Pull Shark, YOLO, Pair Extraordinaire)
  - Issue/PR close timing (Quickdraw)
  - Co-authored commits (Pair Extraordinaire)
  - Stars received (Starstruck)
  - Public profile (Sponsor, Arctic Code Vault)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class AchievementStatus(str, Enum):
    EARNED            = "EARNED"
    IN_PROGRESS       = "IN_PROGRESS"
    ELIGIBLE          = "ELIGIBLE"
    AUTOMATABLE       = "AUTOMATABLE"
    SEMI_AUTO         = "SEMI_AUTO"
    HUMAN_REQUIRED    = "HUMAN_REQUIRED"
    COMMUNITY_REQUIRED = "COMMUNITY_REQUIRED"
    PAYMENT_REQUIRED  = "PAYMENT_REQUIRED"
    UNOBTAINABLE      = "UNOBTAINABLE"
    PENDING_VERIFICATION = "PENDING_VERIFICATION"
    UNKNOWN           = "UNKNOWN"


class RiskLevel(str, Enum):
    NONE   = "NONE"
    LOW    = "LOW"
    MEDIUM = "MEDIUM"
    HIGH   = "HIGH"


class AutomationClass(str, Enum):
    AUTO         = "AUTO"
    SEMI_AUTO    = "SEMI_AUTO"
    HUMAN        = "HUMAN"
    PAYMENT      = "PAYMENT"
    UNOBTAINABLE = "UNOBTAINABLE"


# ---------------------------------------------------------------------------
# Tier definitions
# ---------------------------------------------------------------------------

@dataclass
class Tier:
    name: str        # e.g. "Bronze", "Silver", "Gold"
    requirement: int  # e.g. 2 PRs merged


@dataclass
class AchievementDef:
    key: str
    display_name: str
    description: str
    emoji: str
    tiers: list[Tier]
    automation_class: AutomationClass
    risk_level: RiskLevel
    requires_second_account: bool = False
    requires_payment: bool = False
    human_action_hint: str = ""
    api_action: str = ""             # executor method name


# ---------------------------------------------------------------------------
# Achievement registry
# ---------------------------------------------------------------------------

ACHIEVEMENTS: dict[str, AchievementDef] = {
    "quickdraw": AchievementDef(
        key="quickdraw",
        display_name="Quickdraw",
        emoji="🤠",
        description="Close an issue or PR within 5 minutes of opening it.",
        tiers=[Tier("Default", 1)],
        automation_class=AutomationClass.AUTO,
        risk_level=RiskLevel.LOW,
        api_action="quickdraw",
    ),
    "pull_shark": AchievementDef(
        key="pull_shark",
        display_name="Pull Shark",
        emoji="🦈",
        description="Open a pull request that gets merged (2× for default, higher for tiers).",
        tiers=[
            Tier("Default", 2),
            Tier("Bronze",  16),
            Tier("Silver",  128),
            Tier("Gold",    1024),
        ],
        automation_class=AutomationClass.AUTO,
        risk_level=RiskLevel.LOW,
        api_action="pull_shark",
    ),
    "yolo": AchievementDef(
        key="yolo",
        display_name="YOLO",
        emoji="🤘",
        description="Merge a pull request without a review (repo must allow it).",
        tiers=[Tier("Default", 1)],
        automation_class=AutomationClass.AUTO,
        risk_level=RiskLevel.LOW,
        api_action="yolo",
    ),
    "pair_extraordinaire": AchievementDef(
        key="pair_extraordinaire",
        display_name="Pair Extraordinaire",
        emoji="👥",
        description="Co-author a merged pull request.",
        tiers=[
            Tier("Default", 1),
            Tier("Bronze",  10),
            Tier("Silver",  24),
            Tier("Gold",    48),
        ],
        automation_class=AutomationClass.SEMI_AUTO,
        risk_level=RiskLevel.LOW,
        requires_second_account=True,
        human_action_hint="Requires a second GitHub account configured as GITHUB_TOKEN_2.",
        api_action="pair_extraordinaire",
    ),
    "galaxy_brain": AchievementDef(
        key="galaxy_brain",
        display_name="Galaxy Brain",
        emoji="🧠",
        description="Have a Discussion answer marked as the accepted answer.",
        tiers=[
            Tier("Default", 1),
            Tier("Bronze",  8),
            Tier("Silver",  16),
            Tier("Gold",    32),
        ],
        automation_class=AutomationClass.HUMAN,
        risk_level=RiskLevel.HIGH,
        human_action_hint=(
            "Find a public GitHub Discussion where you can provide a genuinely helpful answer. "
            "The original author must accept your answer — this cannot be automated."
        ),
    ),
    "starstruck": AchievementDef(
        key="starstruck",
        display_name="Starstruck",
        emoji="🌟",
        description="Have a repository receive 16+ stars.",
        tiers=[
            Tier("Default", 16),
            Tier("Bronze",  128),
            Tier("Silver",  512),
            Tier("Gold",    4096),
        ],
        automation_class=AutomationClass.HUMAN,
        risk_level=RiskLevel.HIGH,
        human_action_hint=(
            "Improve your top repository's README/documentation and share it on social media, "
            "Hacker News, Reddit, or dev communities. Stars must be organic."
        ),
    ),
    "public_sponsor": AchievementDef(
        key="public_sponsor",
        display_name="Public Sponsor",
        emoji="💝",
        description="Sponsor an open-source contributor or organization via GitHub Sponsors.",
        tiers=[Tier("Default", 1)],
        automation_class=AutomationClass.PAYMENT,
        risk_level=RiskLevel.NONE,
        requires_payment=True,
        human_action_hint="Visit https://github.com/sponsors and choose a developer to sponsor.",
    ),
    "arctic_code_vault": AchievementDef(
        key="arctic_code_vault",
        display_name="Arctic Code Vault Contributor",
        emoji="🧊",
        description="Contributed to the 2020 GitHub Archive Program (January 2020 snapshot).",
        tiers=[Tier("Default", 1)],
        automation_class=AutomationClass.UNOBTAINABLE,
        risk_level=RiskLevel.NONE,
        human_action_hint="This achievement was only earnable by contributing to active repositories before 2020.",
    ),
    "mars_helicopter": AchievementDef(
        key="mars_helicopter",
        display_name="Mars 2020 Helicopter Contributor",
        emoji="🚁",
        description="Contributed to open-source code used in the Mars 2020 Helicopter mission.",
        tiers=[Tier("Default", 1)],
        automation_class=AutomationClass.UNOBTAINABLE,
        risk_level=RiskLevel.NONE,
        human_action_hint="This achievement was awarded to contributors of specific historical repositories.",
    ),
}


# ---------------------------------------------------------------------------
# Progress inference helpers
# ---------------------------------------------------------------------------

def _count_merged_prs(repos: list[dict], events: list[dict], username: str) -> int:
    """Estimate merged PR count from user events."""
    count = 0
    for event in events:
        if event.get("type") == "PullRequestEvent":
            payload = event.get("payload", {})
            if payload.get("action") == "closed":
                pr = payload.get("pull_request", {})
                if pr.get("merged") and pr.get("user", {}).get("login") == username:
                    count += 1
    return count


def _count_coauthored_prs(events: list[dict]) -> int:
    """Count PushEvents with Co-authored-by trailer (approximate)."""
    count = 0
    for event in events:
        if event.get("type") == "PushEvent":
            commits = event.get("payload", {}).get("commits", [])
            for commit in commits:
                if "co-authored-by" in (commit.get("message") or "").lower():
                    count += 1
    return count


def _check_quickdraw(events: list[dict], username: str) -> bool:
    """
    Check if any issue/PR was opened and closed within 5 minutes by the user.
    Very approximate from public events.
    """
    from datetime import datetime, timezone
    opens: dict[str, datetime] = {}

    for event in events:
        etype = event.get("type")
        payload = event.get("payload", {})
        created = event.get("created_at", "")
        try:
            ts = datetime.fromisoformat(created.rstrip("Z")).replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue

        if etype == "IssuesEvent":
            issue_url = payload.get("issue", {}).get("url", "")
            if payload.get("action") == "opened":
                opens[issue_url] = ts
            elif payload.get("action") == "closed" and issue_url in opens:
                delta = (ts - opens[issue_url]).total_seconds()
                if delta <= 300:
                    return True

        elif etype == "PullRequestEvent":
            pr_url = payload.get("pull_request", {}).get("url", "")
            if payload.get("action") == "opened":
                opens[pr_url] = ts
            elif payload.get("action") == "closed" and pr_url in opens:
                delta = (ts - opens[pr_url]).total_seconds()
                if delta <= 300:
                    return True

    return False


def _max_repo_stars(repos: list[dict]) -> int:
    if not repos:
        return 0
    return max(r.get("stargazers_count", 0) for r in repos)


def _has_sponsorship_activity(events: list[dict]) -> bool:
    for event in events:
        if event.get("type") == "SponsorshipEvent":
            return True
    return False


# ---------------------------------------------------------------------------
# Main scanner
# ---------------------------------------------------------------------------

async def scan_achievements(
    account_id: int,
    client: Any,   # GitHubClient
) -> list[dict]:
    """
    Fetch profile/repo/event data and infer achievement progress.
    Returns a list of achievement dicts ready for the planner.
    """
    import database as db

    logger.info("Scanning achievements for account %d", account_id)

    # --- Fetch data ---
    user = await client.get_user()
    username = user.get("login", "")
    repos = await client.get_repos()
    events = await client.get_user_events(username)

    logger.info("Fetched %d repos and %d events for %s", len(repos), len(events), username)

    # --- Compute signals from GitHub API (might be delayed) ---
    merged_pr_count   = _count_merged_prs(repos, events, username)
    coauthored_count  = _count_coauthored_prs(events)
    quickdraw_earned  = _check_quickdraw(events, username)
    max_stars         = _max_repo_stars(repos)
    has_sponsor       = _has_sponsorship_activity(events)

    # --- Supplement with local DB Audit Log (instant) ---
    all_logs = db.get_audit_log(limit=500)
    local_logs = [l for l in all_logs if l["account_id"] == account_id and l["dry_run"] == 0]
    
    local_quickdraw = any(l["achievement"] == "quickdraw" and l["result"] == "SUCCESS" for l in local_logs)
    local_yolo = any(l["achievement"] == "yolo" and l["result"] == "SUCCESS" for l in local_logs)
    local_pair = any(l["achievement"] == "pair_extraordinaire" and l["result"] == "SUCCESS" for l in local_logs)
    local_pull_sharks = sum(1 for l in local_logs if l["achievement"] == "pull_shark" and l["result"] == "SUCCESS")

    quickdraw_earned = quickdraw_earned or local_quickdraw
    merged_pr_count = max(merged_pr_count, local_pull_sharks)

    results = []

    for key, ach_def in ACHIEVEMENTS.items():
        status = AchievementStatus.UNKNOWN
        progress = {"current": 0, "required": ach_def.tiers[0].requirement}
        tier_name = None

        # --- UNOBTAINABLE ---
        if ach_def.automation_class == AutomationClass.UNOBTAINABLE:
            status = AchievementStatus.UNOBTAINABLE
            tier_name = "N/A"

        # --- PAYMENT ---
        elif ach_def.automation_class == AutomationClass.PAYMENT:
            status = AchievementStatus.PAYMENT_REQUIRED

        # --- Quickdraw ---
        elif key == "quickdraw":
            if quickdraw_earned:
                status = AchievementStatus.EARNED
                progress = {"current": 1, "required": 1}
            else:
                status = AchievementStatus.AUTOMATABLE
                progress = {"current": 0, "required": 1}

        # --- Pull Shark ---
        elif key == "pull_shark":
            progress = {"current": merged_pr_count, "required": 2}
            if merged_pr_count >= 1024:
                status = AchievementStatus.EARNED
                tier_name = "Gold"
            elif merged_pr_count >= 128:
                status = AchievementStatus.EARNED
                tier_name = "Silver"
                progress["required"] = 1024
            elif merged_pr_count >= 16:
                status = AchievementStatus.EARNED
                tier_name = "Bronze"
                progress["required"] = 128
            elif merged_pr_count >= 2:
                status = AchievementStatus.EARNED
                tier_name = "Default"
                progress["required"] = 16
            elif merged_pr_count == 1:
                status = AchievementStatus.IN_PROGRESS
            else:
                status = AchievementStatus.AUTOMATABLE

        # --- YOLO ---
        elif key == "yolo":
            if local_yolo:
                status = AchievementStatus.EARNED
                progress = {"current": 1, "required": 1}
            else:
                status = AchievementStatus.AUTOMATABLE
                progress = {"current": 0, "required": 1}

        # --- Pair Extraordinaire ---
        elif key == "pair_extraordinaire":
            if local_pair or coauthored_count > 0:
                count = max(coauthored_count, 1 if local_pair else 0)
                progress = {"current": count, "required": 24}
                if count >= 48:
                    status = AchievementStatus.EARNED
                    tier_name = "Gold"
                elif count >= 24:
                    status = AchievementStatus.EARNED
                    tier_name = "Silver"
                elif count >= 10:
                    status = AchievementStatus.EARNED
                    tier_name = "Bronze"
                    progress["required"] = 24
                elif count >= 1:
                    status = AchievementStatus.EARNED
                    tier_name = "Default"
                    progress["required"] = 10
            else:
                status = AchievementStatus.SEMI_AUTO
                progress = {"current": 0, "required": 1}

        # --- Galaxy Brain ---
        elif key == "galaxy_brain":
            status = AchievementStatus.HUMAN_REQUIRED
            progress = {"current": 0, "required": 1}

        # --- Starstruck ---
        elif key == "starstruck":
            progress = {"current": max_stars, "required": 16}
            if max_stars >= 4096:
                status = AchievementStatus.EARNED
                tier_name = "Gold"
            elif max_stars >= 512:
                status = AchievementStatus.EARNED
                tier_name = "Silver"
            elif max_stars >= 128:
                status = AchievementStatus.EARNED
                tier_name = "Bronze"
            elif max_stars >= 16:
                status = AchievementStatus.EARNED
                tier_name = "Default"
            else:
                status = AchievementStatus.COMMUNITY_REQUIRED

        # --- Persist to DB ---
        db.upsert_achievement(
            account_id=account_id,
            name=key,
            status=status.value,
            progress=progress,
            tier=tier_name,
        )

        results.append({
            "key": key,
            "display_name": ach_def.display_name,
            "emoji": ach_def.emoji,
            "description": ach_def.description,
            "status": status.value,
            "progress": progress,
            "tier": tier_name,
            "automation_class": ach_def.automation_class.value,
            "risk_level": ach_def.risk_level.value,
            "human_action_hint": ach_def.human_action_hint,
            "requires_second_account": ach_def.requires_second_account,
            "requires_payment": ach_def.requires_payment,
            "tiers": [{"name": t.name, "requirement": t.requirement} for t in ach_def.tiers],
        })

    logger.info("Achievement scan complete for account %d", account_id)
    return results
