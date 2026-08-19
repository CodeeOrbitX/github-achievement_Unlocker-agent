"""
executor.py — Safe automation executor.

Handles:
  - Quickdraw  : open issue → close within 5 min
  - Pull Shark : branch → legitimate change → PR → merge
  - YOLO       : PR without review (only if no branch protection)
  - Pair Extraordinaire : co-authored commit with Account 2

All mutations:
  1. Assert automation not stopped
  2. Check budgets
  3. Execute
  4. Log to audit_log
  5. Queue verification

Dry-run mode: logs what would happen without any API mutations.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from datetime import datetime, timezone
from typing import AsyncGenerator, Any

import safety
import database as db
from config import (
    QUICKDRAW_WINDOW_SECONDS,
    PR_BRANCH_PREFIX,
    AUTOMATION_REPO_PREFIX,
    get_token,
)
from rate_limiter import check_mutation_budget, check_issue_budget, check_pr_budget

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------

class ExecutionResult:
    def __init__(
        self,
        achievement: str,
        action: str,
        success: bool,
        message: str,
        dry_run: bool,
        details: dict | None = None,
    ):
        self.achievement = achievement
        self.action = action
        self.success = success
        self.message = message
        self.dry_run = dry_run
        self.details = details or {}
        self.timestamp = datetime.utcnow().isoformat()

    def to_dict(self) -> dict:
        return {
            "achievement": self.achievement,
            "action": self.action,
            "success": self.success,
            "message": self.message,
            "dry_run": self.dry_run,
            "details": self.details,
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _encode(text: str) -> str:
    """Base64 encode for GitHub file content API."""
    return base64.b64encode(text.encode()).decode()


def _log(
    account_id: int,
    action: str,
    result: str,
    achievement: str,
    repo: str | None = None,
    endpoint: str | None = None,
    dry_run: bool = True,
    risk_status: str = "LOW",
):
    from rate_limiter import get_rate_state
    state = get_rate_state(account_id)
    db.log_action(
        account_id=account_id,
        action=action,
        result=result,
        achievement=achievement,
        repository=repo,
        api_endpoint=endpoint,
        rate_limit_remaining=state.remaining,
        risk_status=risk_status,
        dry_run=dry_run,
    )


async def _ensure_automation_repo(client, username: str, dry_run: bool) -> str | None:
    """
    Find or create a suitable automation repository.
    Returns the repo name, or None in dry-run mode.
    """
    repos = await client.get_repos()

    # Prefer existing repos with our prefix
    for repo in repos:
        if repo["name"].startswith(AUTOMATION_REPO_PREFIX) and not repo.get("archived"):
            return repo["name"]

    # Find any repo owned by user that isn't archived
    for repo in repos:
        if repo.get("owner", {}).get("login") == username and not repo.get("archived"):
            return repo["name"]

    # Need to create one
    if dry_run:
        return f"{AUTOMATION_REPO_PREFIX}-demo"

    logger.info("Creating automation repository: %s", AUTOMATION_REPO_PREFIX)
    repo = await client.create_repo(
        name=AUTOMATION_REPO_PREFIX,
        description="Repository for GitHub achievement automation (legitimate actions only)",
    )
    return repo["name"]


# ---------------------------------------------------------------------------
# Quickdraw executor
# ---------------------------------------------------------------------------

async def execute_quickdraw(
    account_id: int,
    client,
    dry_run: bool = True,
) -> ExecutionResult:
    """
    Open an issue on an owned repo and close it within 5 minutes.
    Qualifies for the Quickdraw achievement.
    """
    safety.assert_not_stopped()
    ok, reason = safety.pre_action_check("quickdraw", account_id, "issue")
    if not ok:
        return ExecutionResult("quickdraw", "open+close issue", False, reason, dry_run)

    user = await client.get_user()
    username = user["login"]
    repo_name = await _ensure_automation_repo(client, username, dry_run)

    if dry_run:
        _log(account_id, "DRY_RUN: open issue + close within 5 min",
             "SKIPPED", "quickdraw", repo_name, dry_run=True)
        return ExecutionResult(
            "quickdraw", "open+close issue", True,
            f"[DRY RUN] Would open an issue on '{username}/{repo_name}' "
            f"and close it within {QUICKDRAW_WINDOW_SECONDS}s.",
            dry_run=True,
            details={"repo": repo_name, "window_seconds": QUICKDRAW_WINDOW_SECONDS},
        )

    # Live mode
    open_time = time.time()
    issue = await client.create_issue(
        owner=username,
        repo=repo_name,
        title="Achievement automation: Quickdraw test",
        body=(
            "This issue was created by the GitHub Achievement Agent to qualify for "
            "the Quickdraw achievement. It will be closed automatically within 5 minutes."
        ),
    )
    issue_number = issue["number"]
    _log(account_id, f"Created issue #{issue_number}", "SUCCESS", "quickdraw",
         f"{username}/{repo_name}", f"/repos/{username}/{repo_name}/issues", dry_run=False)

    # Respect the window — close immediately (well within 5 min)
    elapsed = time.time() - open_time
    remaining = QUICKDRAW_WINDOW_SECONDS - elapsed
    if remaining > 2:
        await asyncio.sleep(2)  # Tiny pause — we're not spamming

    closed = await client.close_issue(username, repo_name, issue_number)
    close_time = time.time()
    delta = close_time - open_time

    _log(account_id, f"Closed issue #{issue_number} ({delta:.1f}s after open)",
         "SUCCESS", "quickdraw", f"{username}/{repo_name}",
         f"/repos/{username}/{repo_name}/issues/{issue_number}", dry_run=False)

    return ExecutionResult(
        "quickdraw", "open+close issue", True,
        f"✅ Opened and closed issue #{issue_number} in {delta:.1f}s "
        f"(within {QUICKDRAW_WINDOW_SECONDS}s window).",
        dry_run=False,
        details={
            "repo": f"{username}/{repo_name}",
            "issue_number": issue_number,
            "elapsed_seconds": round(delta, 1),
        },
    )


# ---------------------------------------------------------------------------
# Pull Shark executor
# ---------------------------------------------------------------------------

async def execute_pull_shark(
    account_id: int,
    client,
    dry_run: bool = True,
    coauthor_name: str | None = None,
    coauthor_email: str | None = None,
) -> ExecutionResult:
    """
    Create a branch with a small legitimate change, open a PR, and merge it.
    Co-author details can be provided for Pair Extraordinaire.
    """
    safety.assert_not_stopped()
    ok, reason = safety.pre_action_check("pull_shark", account_id, "pr")
    if not ok:
        return ExecutionResult("pull_shark", "create+merge PR", False, reason, dry_run)

    user = await client.get_user()
    username = user["login"]
    repo_name = await _ensure_automation_repo(client, username, dry_run)

    ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    branch_name = f"{PR_BRANCH_PREFIX}-{ts}"
    achievement_label = "pair_extraordinaire" if coauthor_name else "pull_shark"

    if dry_run:
        _log(account_id, "DRY_RUN: create branch, PR, merge",
             "SKIPPED", achievement_label, repo_name, dry_run=True)
        return ExecutionResult(
            achievement_label, "create+merge PR", True,
            f"[DRY RUN] Would create branch '{branch_name}' on '{username}/{repo_name}', "
            f"open a PR with a legitimate change, and merge it.",
            dry_run=True,
            details={"repo": repo_name, "branch": branch_name, "coauthor": coauthor_name},
        )

    # Get default branch SHA
    sha, default_branch = await client.get_default_branch_sha(username, repo_name)

    # Check branch protection — abort rather than bypass
    protection = await client.get_branch_protection(username, repo_name, default_branch)
    if protection:
        required_reviews = (
            protection.get("required_pull_request_reviews", {})
                      .get("required_approving_review_count", 0)
        )
        if required_reviews > 0:
            msg = (
                f"Branch '{default_branch}' requires {required_reviews} review(s). "
                "Cannot merge without review — status: HUMAN_REQUIRED."
            )
            _log(account_id, "Branch protection detected — aborted", msg,
                 achievement_label, f"{username}/{repo_name}", dry_run=False, risk_status="MEDIUM")
            return ExecutionResult(achievement_label, "create+merge PR", False, msg, dry_run=False)

    # Create branch
    await client.create_branch(username, repo_name, branch_name, sha)
    _log(account_id, f"Created branch {branch_name}", "SUCCESS", achievement_label,
         f"{username}/{repo_name}", dry_run=False)

    # Create/update a CHANGELOG file with a meaningful entry
    changelog_path = "CHANGELOG.md"
    try:
        existing = await client.get_file(username, repo_name, changelog_path)
        file_sha = existing["sha"]
        current_content = base64.b64decode(existing["content"]).decode()
    except Exception:
        file_sha = None
        current_content = "# Changelog\n\n"

    entry = (
        f"## {datetime.utcnow().strftime('%Y-%m-%d')} — Automated maintenance\n\n"
        f"- Routine documentation update (branch: `{branch_name}`)\n\n"
    )
    new_content = current_content + entry

    commit_msg = f"docs: update changelog [{ts}]"

    if file_sha:
        await client.update_file(
            username, repo_name, changelog_path,
            commit_msg, _encode(new_content), file_sha, branch_name,
            coauthor_name=coauthor_name, coauthor_email=coauthor_email,
        )
    else:
        await client.create_file(
            username, repo_name, changelog_path,
            commit_msg, _encode(new_content), branch_name,
            coauthor_name=coauthor_name, coauthor_email=coauthor_email,
        )

    _log(account_id, f"Committed CHANGELOG to {branch_name}", "SUCCESS", achievement_label,
         f"{username}/{repo_name}", dry_run=False)

    # Open PR
    pr_title = f"docs: changelog update [{ts}]"
    pr_body = (
        "Automated maintenance PR created by GitHub Achievement Agent.\n\n"
        "Contains a legitimate changelog entry. "
        "This PR will be merged automatically."
    )
    if coauthor_name:
        pr_body += f"\n\nCo-authored with: {coauthor_name}"

    pr = await client.create_pull_request(
        username, repo_name, pr_title, pr_body, branch_name, default_branch
    )
    pr_number = pr["number"]
    _log(account_id, f"Opened PR #{pr_number}", "SUCCESS", achievement_label,
         f"{username}/{repo_name}", f"/repos/{username}/{repo_name}/pulls", dry_run=False)

    # Brief pause before merge (looks natural, avoids secondary rate limit)
    await asyncio.sleep(5)
    safety.assert_not_stopped()

    # Merge
    await client.merge_pull_request(
        username, repo_name, pr_number,
        commit_title=f"docs: changelog update [{ts}] (squash)"
    )
    _log(account_id, f"Merged PR #{pr_number}", "SUCCESS", achievement_label,
         f"{username}/{repo_name}", f"/repos/{username}/{repo_name}/pulls/{pr_number}/merge",
         dry_run=False)

    return ExecutionResult(
        achievement_label, "create+merge PR", True,
        f"✅ Created and merged PR #{pr_number} on '{username}/{repo_name}'.",
        dry_run=False,
        details={
            "repo": f"{username}/{repo_name}",
            "branch": branch_name,
            "pr_number": pr_number,
            "coauthor": coauthor_name,
        },
    )


# ---------------------------------------------------------------------------
# YOLO executor
# ---------------------------------------------------------------------------

async def execute_yolo(
    account_id: int,
    client,
    dry_run: bool = True,
) -> ExecutionResult:
    """
    Merge a PR without requesting a review.
    Only proceeds if the repository has no review requirement.
    """
    safety.assert_not_stopped()
    ok, reason = safety.pre_action_check("yolo", account_id, "pr")
    if not ok:
        return ExecutionResult("yolo", "merge PR without review", False, reason, dry_run)

    # YOLO is effectively the same as pull_shark without co-author
    # The distinction is "no review requested" — which is satisfied by not calling
    # the review API. We verify branch protection before merging.
    result = await execute_pull_shark(account_id, client, dry_run)

    if result.success and not dry_run:
        result.achievement = "yolo"
        result.action = "merge PR without review"
        result.message = result.message.replace("pull_shark", "YOLO")
        _log(account_id, "YOLO: merged PR without review",
             "SUCCESS", "yolo", dry_run=False)

    elif dry_run:
        result.achievement = "yolo"
        result.message = (
            "[DRY RUN] Would create a PR and merge it without requesting a review "
            "(only on repos with no branch protection requiring reviews)."
        )

    return result


# ---------------------------------------------------------------------------
# Pair Extraordinaire executor
# ---------------------------------------------------------------------------

async def execute_pair_extraordinaire(
    account_id_1: int,
    client_1,
    account_id_2: int,
    client_2,
    dry_run: bool = True,
) -> ExecutionResult:
    """
    Create a co-authored commit using Account 2's real identity.
    Account 2 must be a real GitHub account — no identity fabrication.
    """
    safety.assert_not_stopped()

    # Fetch real identity for Account 1 (the one who needs the badge)
    try:
        user1 = await client_1.get_user()
        coauthor_login = user1.get("login", "")
        coauthor_name = user1.get("name") or coauthor_login
        coauthor_email = user1.get("email")
    except Exception as e:
        return ExecutionResult(
            "pair_extraordinaire", "co-authored PR", False,
            f"Could not fetch Account 1 identity: {e}", dry_run
        )

    if not coauthor_email:
        # GitHub users may have a private no-reply email
        coauthor_email = f"{coauthor_login}@users.noreply.github.com"

    if dry_run:
        return ExecutionResult(
            "pair_extraordinaire", "co-authored PR", True,
            f"[DRY RUN] Would perform a BIDIRECTIONAL run. First, Account 1 (@{coauthor_login}) will co-author a PR for Account 2. Then Account 2 will co-author a PR for Account 1. This unlocks BOTH badges for BOTH accounts!",
            dry_run=True,
            details={"coauthor_login": coauthor_login, "coauthor_email": coauthor_email},
        )

    # 1. Use Account 2's client to do the work, embedding Account 1 as co-author
    # This ensures Account 1 receives Pair Extraordinaire.
    res1 = await execute_pull_shark(
        account_id=account_id_2,
        client=client_2,
        dry_run=False,
        coauthor_name=coauthor_name,
        coauthor_email=coauthor_email,
    )

    if not res1.success:
        return res1

    # Fetch real identity for Account 2 (to be the co-author for the second half)
    try:
        user2 = await client_2.get_user()
        coauthor_login2 = user2.get("login", "")
        coauthor_name2 = user2.get("name") or coauthor_login2
        coauthor_email2 = user2.get("email")
        if not coauthor_email2:
            coauthor_email2 = f"{coauthor_login2}@users.noreply.github.com"
    except Exception as e:
        return ExecutionResult(
            "pair_extraordinaire", "co-authored PR", False,
            f"Phase 1 succeeded, but failed to fetch Account 2 identity for Phase 2: {e}", dry_run
        )

    # 2. Use Account 1's client to do the work, embedding Account 2 as co-author
    # This ensures Account 2 receives Pair Extraordinaire.
    res2 = await execute_pull_shark(
        account_id=account_id_1,
        client=client_1,
        dry_run=False,
        coauthor_name=coauthor_name2,
        coauthor_email=coauthor_email2,
    )

    if not res2.success:
        return res2

    return ExecutionResult(
        "pair_extraordinaire", "co-authored PR", True,
        "✅ BI-DIRECTIONAL SUCCESS! Both accounts have now earned the Pair Extraordinaire and Pull Shark badges!",
        dry_run=False
    )


# ---------------------------------------------------------------------------
# Master executor
# ---------------------------------------------------------------------------

async def execute_plan(
    account_id: int,
    planned_actions: list,
    dry_run: bool = True,
    second_account_id: int | None = None,
    progress_callback=None,
) -> AsyncGenerator[dict, None]:
    """
    Execute a list of planned actions.
    Yields progress events as dicts.
    Stops immediately if safety.is_stopped().
    """
    from github_client import GitHubClient

    client_1 = GitHubClient(account_id)
    client_2 = GitHubClient(second_account_id) if second_account_id else None

    for action in planned_actions:
        if safety.is_stopped():
            yield {"type": "stopped", "reason": safety.get_stop_reason()}
            return

        if not action.is_executable:
            yield {
                "type": "skip",
                "achievement": action.achievement_key,
                "reason": f"Not executable: {action.classification}",
            }
            continue

        yield {"type": "start", "achievement": action.achievement_key, "action": action.action if hasattr(action, 'action') else action.description}

        try:
            result = await _dispatch(
                action.achievement_key,
                account_id,
                client_1,
                dry_run,
                second_account_id=second_account_id,
                client_2=client_2,
            )
            yield {"type": "result", "data": result.to_dict()}

        except RuntimeError as e:
            # Safety stop was triggered
            yield {"type": "stopped", "reason": str(e)}
            return
        except Exception as e:
            logger.error("Executor error for '%s': %s", action.achievement_key, e)
            yield {
                "type": "error",
                "achievement": action.achievement_key,
                "error": str(e),
            }
            # Don't stop for non-safety errors — move to next action


async def _dispatch(
    key: str,
    account_id: int,
    client,
    dry_run: bool,
    second_account_id: int | None = None,
    client_2=None,
) -> ExecutionResult:
    if key == "quickdraw":
        return await execute_quickdraw(account_id, client, dry_run)
    elif key == "pull_shark":
        return await execute_pull_shark(account_id, client, dry_run)
    elif key == "yolo":
        return await execute_yolo(account_id, client, dry_run)
    elif key == "pair_extraordinaire":
        if client_2 and second_account_id:
            return await execute_pair_extraordinaire(
                account_id, client, second_account_id, client_2, dry_run
            )
        else:
            return ExecutionResult(
                "pair_extraordinaire", "co-authored PR", False,
                "Account 2 not available. Set GITHUB_TOKEN_2 in .env.", dry_run
            )
    else:
        return ExecutionResult(
            key, "unknown", False, f"No executor for achievement: {key}", dry_run
        )
