"""
safety.py — Abuse detection and emergency stop.

Monitors GitHub API responses for abuse signals.
Sets a global stop flag that all executors respect.
"""

import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Global automation state
# ---------------------------------------------------------------------------

_automation_stopped: bool = False
_stop_reason: str = ""
_stop_time: str = ""


def is_stopped() -> bool:
    """Returns True if automation has been stopped."""
    return _automation_stopped


def get_stop_reason() -> str:
    return _stop_reason


def get_stop_time() -> str:
    return _stop_time


def trigger_emergency_stop(reason: str):
    """
    Immediately stops all automation.
    Called automatically on abuse detection or by the user pressing STOP.
    """
    global _automation_stopped, _stop_reason, _stop_time
    _automation_stopped = True
    _stop_reason = reason
    _stop_time = datetime.utcnow().isoformat()
    logger.error("🛑 EMERGENCY STOP: %s", reason)


def reset_stop():
    """Allow re-enabling automation (only after user explicitly confirms)."""
    global _automation_stopped, _stop_reason, _stop_time
    _automation_stopped = False
    _stop_reason = ""
    _stop_time = ""
    logger.info("Automation stop flag cleared by user.")


# ---------------------------------------------------------------------------
# Response analysis
# ---------------------------------------------------------------------------

# Signals that indicate GitHub abuse detection or restriction
ABUSE_SIGNALS = {
    "secondary rate limit",
    "rate limit",
    "abuse detection",
    "abuse rate limit",
    "you have triggered an abuse detection",
    "automated requests",
    "captcha",
    "suspicious activity",
    "account suspended",
    "forbidden",
}


def analyze_response(status_code: int, body: dict | str, account_id: int) -> str | None:
    """
    Analyze a GitHub API response for abuse/restriction signals.
    Returns a stop reason string if automation should stop, else None.

    NEVER logs tokens or authentication headers.
    """
    from config import get_safety_config
    cfg = get_safety_config()

    # Build searchable text from response body (tokens must never appear here)
    if isinstance(body, dict):
        message = (body.get("message") or "").lower()
        documentation = (body.get("documentation_url") or "").lower()
        search_text = message + " " + documentation
    else:
        search_text = str(body).lower()

    # HTTP 403 — Forbidden
    if status_code == 403:
        for signal in ABUSE_SIGNALS:
            if signal in search_text:
                reason = f"GitHub abuse/restriction detected (403): {search_text[:120]}"
                if cfg.stop_on_abuse_detection:
                    trigger_emergency_stop(reason)
                return reason
        # If it's a generic 403 (e.g., lack of permissions, feature not available on free tier)
        # do NOT trigger an emergency stop. Let the caller handle the HTTPStatusError.
        return None

    # HTTP 429 — Too Many Requests
    if status_code == 429:
        reason = f"GitHub rate limit (429) for account {account_id}"
        if cfg.stop_on_rate_limit:
            trigger_emergency_stop(reason)
        return reason

    # HTTP 401/422 — Authentication or validation errors
    if status_code == 401:
        reason = f"Authentication failure for account {account_id} — check token"
        trigger_emergency_stop(reason)
        return reason

    # Check body for abuse signals even on 200 responses (GitHub can embed signals)
    for signal in ABUSE_SIGNALS:
        if signal in search_text:
            reason = f"Abuse signal in response body: {search_text[:120]}"
            if cfg.stop_on_abuse_detection:
                trigger_emergency_stop(reason)
            return reason

    return None


def assert_not_stopped():
    """
    Raises RuntimeError if automation has been stopped.
    Call this at the start of every executor action.
    """
    if _automation_stopped:
        raise RuntimeError(f"Automation stopped: {_stop_reason}")


# ---------------------------------------------------------------------------
# Pre-action safety checklist
# ---------------------------------------------------------------------------

def pre_action_check(
    action: str,
    account_id: int,
    mutation_type: str | None = None,
) -> tuple[bool, str]:
    """
    Run pre-action safety checks. Returns (ok, reason).
    """
    if is_stopped():
        return False, f"Automation stopped: {_stop_reason}"

    # Import here to avoid circular imports
    from rate_limiter import check_mutation_budget, check_issue_budget, check_pr_budget
    from rate_limiter import BudgetExceededError, RateLimitError

    try:
        check_mutation_budget(account_id)
        if mutation_type == "issue":
            check_issue_budget(account_id)
        elif mutation_type == "pr":
            check_pr_budget(account_id)
    except (BudgetExceededError, RateLimitError) as e:
        return False, str(e)

    return True, "OK"
