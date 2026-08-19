"""
rate_limiter.py — Central rate-limit manager.

Every mutation MUST call check_and_reserve() before executing.
Reads GitHub response headers to track remaining limits.
Implements exponential backoff for transient failures.
"""

import asyncio
import time
import logging
from dataclasses import dataclass, field
from datetime import datetime

from config import get_action_budget, get_safety_config, SafetyConfig, ActionBudget
import database as db

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# GitHub rate-limit state (per account)
# ---------------------------------------------------------------------------

@dataclass
class RateLimitState:
    remaining: int = 5000
    limit: int = 5000
    reset_at: int = 0      # Unix timestamp
    used: int = 0
    secondary_limited: bool = False
    last_updated: float = field(default_factory=time.time)


# Global state dict: account_id → RateLimitState
_rate_states: dict[int, RateLimitState] = {}
_lock = asyncio.Lock()


def get_rate_state(account_id: int) -> RateLimitState:
    if account_id not in _rate_states:
        _rate_states[account_id] = RateLimitState()
    return _rate_states[account_id]


def update_from_headers(account_id: int, headers: dict):
    """Update rate-limit state from GitHub response headers."""
    state = get_rate_state(account_id)
    try:
        if "x-ratelimit-remaining" in headers:
            state.remaining = int(headers["x-ratelimit-remaining"])
        if "x-ratelimit-limit" in headers:
            state.limit = int(headers["x-ratelimit-limit"])
        if "x-ratelimit-reset" in headers:
            state.reset_at = int(headers["x-ratelimit-reset"])
        if "x-ratelimit-used" in headers:
            state.used = int(headers["x-ratelimit-used"])
        state.last_updated = time.time()
    except (ValueError, TypeError) as e:
        logger.warning("Failed to parse rate-limit headers: %s", e)


# ---------------------------------------------------------------------------
# Budget gate
# ---------------------------------------------------------------------------

class BudgetExceededError(Exception):
    """Raised when the action budget would be exceeded."""


class RateLimitError(Exception):
    """Raised when GitHub's rate limit is exhausted."""


def check_mutation_budget(account_id: int):
    """
    Check hourly mutation budget and GitHub remaining limit.
    Raises BudgetExceededError or RateLimitError if limits would be exceeded.
    """
    budget: ActionBudget = get_action_budget()
    state = get_rate_state(account_id)

    # GitHub remaining check — keep a safety buffer of 50 requests
    if state.remaining < 50:
        wait_secs = max(0, state.reset_at - int(time.time()))
        raise RateLimitError(
            f"GitHub API limit almost exhausted (remaining={state.remaining}). "
            f"Resets in {wait_secs}s."
        )

    # Hourly mutation budget
    used = db.get_hourly_mutations(account_id)
    if used >= budget.max_mutations_per_hour:
        raise BudgetExceededError(
            f"Hourly mutation budget reached ({used}/{budget.max_mutations_per_hour}). "
            "Wait until the next hour."
        )


def check_issue_budget(account_id: int):
    budget = get_action_budget()
    used = db.get_daily_issues(account_id)
    if used >= budget.max_test_issues_per_day:
        raise BudgetExceededError(
            f"Daily issue budget reached ({used}/{budget.max_test_issues_per_day})."
        )


def check_pr_budget(account_id: int):
    budget = get_action_budget()
    used = db.get_daily_prs(account_id)
    if used >= budget.max_automated_prs_per_day:
        raise BudgetExceededError(
            f"Daily PR budget reached ({used}/{budget.max_automated_prs_per_day})."
        )


# ---------------------------------------------------------------------------
# Exponential backoff
# ---------------------------------------------------------------------------

async def backoff_wait(attempt: int, safety_cfg: SafetyConfig | None = None):
    """
    Wait with exponential backoff. Stops automation after max_retries.
    Delays: 30s → 2m → 5m.
    """
    cfg = safety_cfg or get_safety_config()
    delays = cfg.backoff_delays

    if attempt >= len(delays):
        from safety import trigger_emergency_stop
        trigger_emergency_stop(
            f"Repeated failures after {len(delays)} retries — stopping automation."
        )
        raise RateLimitError("Max retries exceeded — automation stopped.")

    delay = delays[attempt]
    logger.warning("Backoff attempt %d: waiting %ds", attempt + 1, delay)
    await asyncio.sleep(delay)


# ---------------------------------------------------------------------------
# Summary for dashboard
# ---------------------------------------------------------------------------

def get_rate_summary(account_id: int) -> dict:
    state = get_rate_state(account_id)
    budget = get_action_budget()
    now = int(time.time())
    return {
        "remaining": state.remaining,
        "limit": state.limit,
        "used": state.used,
        "resets_in_seconds": max(0, state.reset_at - now),
        "secondary_limited": state.secondary_limited,
        "hourly_mutations_used": db.get_hourly_mutations(account_id),
        "hourly_mutations_max": budget.max_mutations_per_hour,
        "daily_issues_used": db.get_daily_issues(account_id),
        "daily_issues_max": budget.max_test_issues_per_day,
        "daily_prs_used": db.get_daily_prs(account_id),
        "daily_prs_max": budget.max_automated_prs_per_day,
    }
