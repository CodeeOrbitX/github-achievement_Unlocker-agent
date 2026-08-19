"""
verifier.py — Before/after achievement state comparison.

After every automated action:
  1. Wait for GitHub to process
  2. Re-scan achievement state
  3. Compare with pre-action snapshot
  4. Update database with result
"""

import asyncio
import logging
from datetime import datetime
from typing import Any

import database as db

logger = logging.getLogger(__name__)

# Default wait before re-checking (GitHub can take a few seconds to process)
DEFAULT_VERIFY_WAIT_SECONDS = 30


async def take_snapshot(account_id: int) -> dict[str, dict]:
    """
    Return current achievement state as a dict keyed by achievement name.
    """
    achievements = db.get_achievements(account_id)
    return {a["name"]: a for a in achievements}


async def verify_achievement(
    account_id: int,
    achievement_key: str,
    client: Any,              # GitHubClient
    wait_seconds: int = DEFAULT_VERIFY_WAIT_SECONDS,
) -> dict:
    """
    Wait, then re-scan and compare achievement state.
    Returns a verification result dict.
    """
    import achievements as ach_module
    import safety

    logger.info(
        "Verifying achievement '%s' for account %d (waiting %ds)",
        achievement_key, account_id, wait_seconds
    )

    # Snapshot before
    before = await take_snapshot(account_id)

    # Wait for GitHub to process
    await asyncio.sleep(wait_seconds)

    if safety.is_stopped():
        return {
            "achievement": achievement_key,
            "verified": False,
            "status": "STOPPED",
            "message": "Automation stopped during verification wait.",
            "before": before.get(achievement_key, {}).get("status"),
            "after": None,
        }

    # Re-scan
    try:
        updated = await ach_module.scan_achievements(account_id, client)
    except Exception as e:
        logger.error("Re-scan failed during verification: %s", e)
        return {
            "achievement": achievement_key,
            "verified": False,
            "status": "PENDING_VERIFICATION",
            "message": str(e),
            "before": before.get(achievement_key, {}).get("status"),
            "after": None,
        }

    # Compare
    after_map = {a["key"]: a for a in updated}
    before_status = before.get(achievement_key, {}).get("status", "UNKNOWN")
    after_status  = after_map.get(achievement_key, {}).get("status", "UNKNOWN")

    if after_status == "EARNED" and before_status != "EARNED":
        result_msg = "✅ Achievement EARNED!"
    elif after_status == before_status:
        result_msg = (
            "⏳ Status unchanged — GitHub may still be processing. "
            "Check your profile in a few minutes."
        )
        after_status = "PENDING_VERIFICATION"
    else:
        result_msg = f"Status changed: {before_status} → {after_status}"

    logger.info("Verification result for '%s': %s", achievement_key, result_msg)

    db.upsert_achievement(
        account_id=account_id,
        name=achievement_key,
        status=after_status,
        progress=after_map.get(achievement_key, {}).get("progress"),
    )

    return {
        "achievement": achievement_key,
        "verified": after_status == "EARNED",
        "status": after_status,
        "message": result_msg,
        "before": before_status,
        "after": after_status,
        "timestamp": datetime.utcnow().isoformat(),
    }
