"""
config.py — Central configuration loader.

Reads tokens from environment variables ONLY.
Never exposes tokens in logs or error messages.
"""

import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


# ---------------------------------------------------------------------------
# Token access — never log these values
# ---------------------------------------------------------------------------

def get_token(account_id: int) -> str | None:
    """Return the PAT for a given account index (1-based). Returns None if not set."""
    return os.getenv(f"GITHUB_TOKEN_{account_id}") or None


def get_configured_accounts() -> list[int]:
    """Return list of account IDs that have a configured token."""
    configured = []
    for i in range(1, 10):
        if get_token(i):
            configured.append(i)
    return configured


# ---------------------------------------------------------------------------
# Action budget — conservative defaults
# ---------------------------------------------------------------------------

@dataclass
class ActionBudget:
    max_mutations_per_hour: int = 5
    max_test_issues_per_day: int = 1
    max_automated_prs_per_day: int = 3
    max_discussion_posts: int = 0   # Galaxy Brain is always SEMI-AUTO/HUMAN
    max_star_actions: int = 0       # Stars are never automated


def get_action_budget() -> ActionBudget:
    return ActionBudget(
        max_mutations_per_hour=int(os.getenv("BUDGET_MUTATIONS_PER_HOUR", "5")),
        max_test_issues_per_day=int(os.getenv("BUDGET_ISSUES_PER_DAY", "1")),
        max_automated_prs_per_day=int(os.getenv("BUDGET_PRS_PER_DAY", "3")),
        max_discussion_posts=0,
        max_star_actions=0,
    )


# ---------------------------------------------------------------------------
# Safety configuration
# ---------------------------------------------------------------------------

@dataclass
class SafetyConfig:
    stop_on_rate_limit: bool = True
    stop_on_abuse_detection: bool = True
    stop_on_captcha: bool = True
    stop_on_suspicious_activity: bool = True
    # Exponential backoff delays in seconds
    backoff_delays: list[int] = field(default_factory=lambda: [30, 120, 300])
    # Maximum retries before giving up
    max_retries: int = 3


def get_safety_config() -> SafetyConfig:
    return SafetyConfig(
        stop_on_rate_limit=os.getenv("SAFETY_STOP_ON_RATE_LIMIT", "true").lower() == "true",
        stop_on_abuse_detection=os.getenv("SAFETY_STOP_ON_ABUSE", "true").lower() == "true",
        stop_on_captcha=os.getenv("SAFETY_STOP_ON_CAPTCHA", "true").lower() == "true",
        stop_on_suspicious_activity=os.getenv("SAFETY_STOP_ON_SUSPICIOUS", "true").lower() == "true",
    )


# ---------------------------------------------------------------------------
# App-level settings
# ---------------------------------------------------------------------------

APP_HOST = os.getenv("APP_HOST", "127.0.0.1")
APP_PORT = int(os.getenv("APP_PORT", "8000"))
DATABASE_PATH = os.getenv("DATABASE_PATH", "agent.db")

# Default repo prefix for automated actions
AUTOMATION_REPO_PREFIX = os.getenv("AUTOMATION_REPO_PREFIX", "achievement-automation")

# Quickdraw: close issue within this many seconds to qualify (GitHub = 5 min)
QUICKDRAW_WINDOW_SECONDS = 290  # stay safely under 5 minutes

# Pull Shark: qualifying PR branch prefix
PR_BRANCH_PREFIX = "achievement-pr"
