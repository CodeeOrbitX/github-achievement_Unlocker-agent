"""
planner.py — Classifies achievements and builds an action plan.

Applies the core decision function:
  EARNED          → SKIP
  UNOBTAINABLE    → SKIP
  PAYMENT         → ASK_USER
  HUMAN_REQUIRED  → SHOW_HUMAN_ACTION
  AUTOMATABLE     → EXECUTE_SAFE_ACTION
  else            → SHOW_MANUAL_INSTRUCTIONS
"""

from __future__ import annotations
import logging
from dataclasses import dataclass

from achievements import AchievementStatus, AutomationClass, ACHIEVEMENTS

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class PlannedAction:
    achievement_key: str
    display_name: str
    emoji: str
    classification: str          # AUTO / SEMI_AUTO / HUMAN / PAYMENT / SKIP
    status: str
    progress: dict
    risk_level: str
    description: str             # What will happen
    human_hint: str
    is_executable: bool          # Can the executor run this?
    requires_confirmation: bool  # Must user confirm first?
    requires_second_account: bool
    requires_payment: bool
    tiers: list[dict]


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------

def build_action_plan(
    achievements: list[dict],
    second_account_available: bool = False,
) -> list[PlannedAction]:
    """
    Given a list of scanned achievements, produce a prioritised action plan.
    """
    plan: list[PlannedAction] = []

    for ach in achievements:
        key = ach["key"]
        status = ach["status"]
        auto_class = ach.get("automation_class", "HUMAN")

        # --- Core decision function ---

        if status == AchievementStatus.UNOBTAINABLE.value:
            classification = "SKIP"
            is_executable = False
            requires_confirmation = False
            desc = "Unobtainable — no action needed."
            
        elif status == AchievementStatus.EARNED.value:
            # Check if there's a higher tier available
            progress = ach.get("progress", {})
            current = progress.get("current", 0)
            required = progress.get("required", 1)
            
            if current >= required:
                classification = "SKIP"
                is_executable = False
                requires_confirmation = False
                desc = "Already fully earned — no action needed."
            else:
                # Treat it as automatable to reach the next tier
                if auto_class == AutomationClass.AUTO.value:
                    classification = "AUTO"
                    is_executable = True
                    requires_confirmation = False
                    desc = f"Progressing towards next tier ({current}/{required}). " + _describe_auto(key, ach)
                elif auto_class == AutomationClass.SEMI_AUTO.value:
                    if ach.get("requires_second_account") and not second_account_available:
                        classification = "HUMAN"
                        is_executable = False
                        requires_confirmation = False
                        desc = "Requires a second GitHub account."
                    else:
                        classification = "SEMI_AUTO"
                        is_executable = True
                        requires_confirmation = True
                        desc = f"Progressing towards next tier ({current}/{required}). " + _describe_semi_auto(key, ach)
                else:
                    classification = "HUMAN"
                    is_executable = False
                    requires_confirmation = False
                    desc = f"Progressing towards next tier ({current}/{required}). Manual action required."

        elif status == AchievementStatus.PAYMENT_REQUIRED.value:
            classification = "PAYMENT"
            is_executable = False
            requires_confirmation = True
            desc = "Requires a real GitHub sponsorship payment — cannot be automated."

        elif status in (
            AchievementStatus.HUMAN_REQUIRED.value,
            AchievementStatus.COMMUNITY_REQUIRED.value,
        ):
            classification = "HUMAN"
            is_executable = False
            requires_confirmation = False
            desc = ach.get("human_action_hint", "Requires genuine human interaction.")

        elif status == AchievementStatus.SEMI_AUTO.value:
            # Semi-auto: executable only if second account is available
            if ach.get("requires_second_account") and not second_account_available:
                classification = "HUMAN"
                is_executable = False
                requires_confirmation = False
                desc = (
                    "Requires a second GitHub account. "
                    "Set GITHUB_TOKEN_2 in your .env file to enable this."
                )
            else:
                classification = "SEMI_AUTO"
                is_executable = True
                requires_confirmation = True
                desc = _describe_semi_auto(key, ach)

        elif status in (
            AchievementStatus.AUTOMATABLE.value,
            AchievementStatus.ELIGIBLE.value,
            AchievementStatus.IN_PROGRESS.value,
        ):
            if auto_class == AutomationClass.AUTO.value:
                classification = "AUTO"
                is_executable = True
                requires_confirmation = False
                desc = _describe_auto(key, ach)
            elif auto_class == AutomationClass.SEMI_AUTO.value:
                classification = "SEMI_AUTO"
                is_executable = True
                requires_confirmation = True
                desc = _describe_semi_auto(key, ach)
            else:
                classification = "HUMAN"
                is_executable = False
                requires_confirmation = False
                desc = ach.get("human_action_hint", "")

        else:
            # UNKNOWN / PENDING_VERIFICATION
            classification = "UNKNOWN"
            is_executable = False
            requires_confirmation = False
            desc = "Status could not be determined from available API data."

        plan.append(PlannedAction(
            achievement_key=key,
            display_name=ach["display_name"],
            emoji=ach["emoji"],
            classification=classification,
            status=status,
            progress=ach.get("progress", {}),
            risk_level=ach.get("risk_level", "LOW"),
            description=desc,
            human_hint=ach.get("human_action_hint", ""),
            is_executable=is_executable,
            requires_confirmation=requires_confirmation,
            requires_second_account=ach.get("requires_second_account", False),
            requires_payment=ach.get("requires_payment", False),
            tiers=ach.get("tiers", []),
        ))

    # Sort: AUTO first, then SEMI_AUTO, HUMAN, PAYMENT, SKIP
    order = {"AUTO": 0, "SEMI_AUTO": 1, "HUMAN": 2, "PAYMENT": 3, "UNKNOWN": 4, "SKIP": 5}
    plan.sort(key=lambda a: order.get(a.classification, 99))

    logger.info("Action plan: %s", [(a.achievement_key, a.classification) for a in plan])
    return plan


def _describe_auto(key: str, ach: dict) -> str:
    if key == "quickdraw":
        return "Open an issue on an owned repository and close it within 5 minutes."
    elif key == "pull_shark":
        current = ach.get("progress", {}).get("current", 0)
        required = ach.get("progress", {}).get("required", 2)
        remaining = max(0, required - current)
        return f"Create and merge {remaining} qualifying pull request(s) with a small legitimate change."
    elif key == "yolo":
        return "Create a PR on a repository without branch protection and merge it without requesting a review."
    return "Execute safe automated action."


def _describe_semi_auto(key: str, ach: dict) -> str:
    if key == "pair_extraordinaire":
        return (
            "Account 1 will create a PR with a co-authored commit crediting Account 2. "
            "Account 2's real name and email will be used from their GitHub profile."
        )
    return "Semi-automated action — requires user confirmation before executing."


# ---------------------------------------------------------------------------
# Plan summary for dry-run display
# ---------------------------------------------------------------------------

def summarize_plan(plan: list[PlannedAction]) -> dict:
    counts = {
        "auto": 0, "semi_auto": 0, "human": 0,
        "payment": 0, "skip": 0, "unknown": 0
    }
    for a in plan:
        c = a.classification.lower()
        counts[c] = counts.get(c, 0) + 1

    return {
        "total": len(plan),
        "counts": counts,
        "executable": [a for a in plan if a.is_executable],
        "human_required": [a for a in plan if a.classification == "HUMAN"],
        "payment_required": [a for a in plan if a.classification == "PAYMENT"],
        "skipped": [a for a in plan if a.classification == "SKIP"],
    }
