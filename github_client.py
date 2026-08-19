"""
github_client.py — Authenticated GitHub REST API wrapper.

All requests:
  1. Check rate-limiter before sending
  2. Read X-RateLimit-* headers after response
  3. Run safety analysis on every response
  4. NEVER log tokens or Authorization headers
"""

import asyncio
import logging
from typing import Any

import httpx

import rate_limiter
import safety
from config import get_token

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"
DEFAULT_TIMEOUT = 30.0


# ---------------------------------------------------------------------------
# Client factory
# ---------------------------------------------------------------------------

def _make_headers(account_id: int) -> dict[str, str]:
    token = get_token(account_id)
    if not token:
        raise ValueError(f"No token configured for account {account_id}")
    return {
        "Authorization": f"token {token}",   # ← never logged
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "GitHub-Achievement-Agent/1.0",
    }


class GitHubClient:
    """
    Async GitHub API client for a single account.
    Tokens are held in memory only and never serialised.
    """

    def __init__(self, account_id: int):
        self.account_id = account_id
        self._headers = _make_headers(account_id)

    async def _request(
        self,
        method: str,
        path: str,
        json_body: dict | None = None,
        params: dict | None = None,
        is_mutation: bool = False,
    ) -> dict | list | None:
        """
        Execute a single request. Handles rate-limit headers and abuse signals.
        Never logs the Authorization header.
        """
        safety.assert_not_stopped()

        url = f"{GITHUB_API_BASE}{path}"
        safe_headers = {k: v for k, v in self._headers.items() if k.lower() != "authorization"}
        logger.debug("→ %s %s headers=%s", method, url, safe_headers)

        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            response = await client.request(
                method,
                url,
                headers=self._headers,
                json=json_body,
                params=params,
            )

        # Update rate-limit state
        rate_limiter.update_from_headers(self.account_id, dict(response.headers))

        # Parse body safely
        try:
            body = response.json()
        except Exception:
            body = response.text

        # Log endpoint + status (no tokens)
        logger.info("← %s %s [%d]", method, path, response.status_code)

        # Safety analysis
        stop_reason = safety.analyze_response(response.status_code, body, self.account_id)
        if stop_reason:
            raise RuntimeError(f"Safety stop: {stop_reason}")

        if response.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"GitHub API error {response.status_code}: {body}",
                request=response.request,
                response=response,
            )

        return body

    # ------------------------------------------------------------------
    # Read methods
    # ------------------------------------------------------------------

    async def get_user(self) -> dict:
        return await self._request("GET", "/user")

    async def get_repos(self, per_page: int = 100) -> list[dict]:
        """Fetch all repos owned by the authenticated user."""
        repos = []
        page = 1
        while True:
            safety.assert_not_stopped()
            page_data = await self._request(
                "GET", "/user/repos",
                params={"type": "owner", "per_page": per_page, "page": page, "sort": "updated"}
            )
            if not page_data:
                break
            repos.extend(page_data)
            if len(page_data) < per_page:
                break
            page += 1
        return repos

    async def get_user_events(self, username: str, per_page: int = 100) -> list[dict]:
        """Fetch recent public events for a user."""
        return await self._request(
            "GET", f"/users/{username}/events",
            params={"per_page": per_page}
        ) or []

    async def get_repo_pulls(self, owner: str, repo: str, state: str = "all") -> list[dict]:
        return await self._request(
            "GET", f"/repos/{owner}/{repo}/pulls",
            params={"state": state, "per_page": 100}
        ) or []

    async def get_repo_commits(self, owner: str, repo: str, per_page: int = 50) -> list[dict]:
        return await self._request(
            "GET", f"/repos/{owner}/{repo}/commits",
            params={"per_page": per_page}
        ) or []

    async def get_rate_limit(self) -> dict:
        return await self._request("GET", "/rate_limit")

    async def get_repo(self, owner: str, repo: str) -> dict:
        return await self._request("GET", f"/repos/{owner}/{repo}")

    # ------------------------------------------------------------------
    # Write (mutation) methods — all gated by safety checks
    # ------------------------------------------------------------------

    async def create_repo(self, name: str, description: str = "") -> dict:
        rate_limiter.check_mutation_budget(self.account_id)
        result = await self._request("POST", "/user/repos", json_body={
            "name": name,
            "description": description,
            "private": False,
            "auto_init": True,
            "has_issues": True,
        })
        rate_limiter._rate_states.get(self.account_id)  # refresh
        import database as db
        db.increment_mutations(self.account_id)
        return result

    async def create_issue(self, owner: str, repo: str, title: str, body: str) -> dict:
        rate_limiter.check_mutation_budget(self.account_id)
        rate_limiter.check_issue_budget(self.account_id)
        result = await self._request(
            "POST", f"/repos/{owner}/{repo}/issues",
            json_body={"title": title, "body": body}
        )
        import database as db
        db.increment_mutations(self.account_id)
        db.increment_issues(self.account_id)
        return result

    async def close_issue(self, owner: str, repo: str, issue_number: int) -> dict:
        rate_limiter.check_mutation_budget(self.account_id)
        result = await self._request(
            "PATCH", f"/repos/{owner}/{repo}/issues/{issue_number}",
            json_body={"state": "closed"}
        )
        import database as db
        db.increment_mutations(self.account_id)
        return result

    async def create_branch(self, owner: str, repo: str, branch: str, sha: str) -> dict:
        rate_limiter.check_mutation_budget(self.account_id)
        result = await self._request(
            "POST", f"/repos/{owner}/{repo}/git/refs",
            json_body={"ref": f"refs/heads/{branch}", "sha": sha}
        )
        import database as db
        db.increment_mutations(self.account_id)
        return result

    async def get_file(self, owner: str, repo: str, path: str) -> dict:
        return await self._request("GET", f"/repos/{owner}/{repo}/contents/{path}")

    async def update_file(
        self, owner: str, repo: str, path: str,
        message: str, content_b64: str, sha: str, branch: str,
        coauthor_name: str | None = None, coauthor_email: str | None = None,
    ) -> dict:
        rate_limiter.check_mutation_budget(self.account_id)
        commit_message = message
        if coauthor_name and coauthor_email:
            commit_message += f"\n\nCo-authored-by: {coauthor_name} <{coauthor_email}>"
        result = await self._request(
            "PUT", f"/repos/{owner}/{repo}/contents/{path}",
            json_body={
                "message": commit_message,
                "content": content_b64,
                "sha": sha,
                "branch": branch,
            }
        )
        import database as db
        db.increment_mutations(self.account_id)
        return result

    async def create_file(
        self, owner: str, repo: str, path: str,
        message: str, content_b64: str, branch: str,
        coauthor_name: str | None = None, coauthor_email: str | None = None,
    ) -> dict:
        rate_limiter.check_mutation_budget(self.account_id)
        commit_message = message
        if coauthor_name and coauthor_email:
            commit_message += f"\n\nCo-authored-by: {coauthor_name} <{coauthor_email}>"
        result = await self._request(
            "PUT", f"/repos/{owner}/{repo}/contents/{path}",
            json_body={
                "message": commit_message,
                "content": content_b64,
                "branch": branch,
            }
        )
        import database as db
        db.increment_mutations(self.account_id)
        return result

    async def create_pull_request(
        self, owner: str, repo: str, title: str, body: str, head: str, base: str
    ) -> dict:
        rate_limiter.check_mutation_budget(self.account_id)
        rate_limiter.check_pr_budget(self.account_id)
        result = await self._request(
            "POST", f"/repos/{owner}/{repo}/pulls",
            json_body={"title": title, "body": body, "head": head, "base": base}
        )
        import database as db
        db.increment_mutations(self.account_id)
        db.increment_prs(self.account_id)
        return result

    async def merge_pull_request(
        self, owner: str, repo: str, pr_number: int, commit_title: str
    ) -> dict:
        rate_limiter.check_mutation_budget(self.account_id)
        result = await self._request(
            "PUT", f"/repos/{owner}/{repo}/pulls/{pr_number}/merge",
            json_body={"commit_title": commit_title, "merge_method": "merge"}
        )
        import database as db
        db.increment_mutations(self.account_id)
        return result

    async def get_default_branch_sha(self, owner: str, repo: str) -> str:
        """Get the SHA of the tip of the default branch."""
        repo_info = await self.get_repo(owner, repo)
        default_branch = repo_info.get("default_branch", "main")
        ref = await self._request("GET", f"/repos/{owner}/{repo}/git/ref/heads/{default_branch}")
        return ref["object"]["sha"], default_branch

    async def get_branch_protection(self, owner: str, repo: str, branch: str) -> dict | None:
        """Returns branch protection info, or None if not protected/accessible."""
        try:
            return await self._request("GET", f"/repos/{owner}/{repo}/branches/{branch}/protection")
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (403, 404):
                return None
            raise

    async def add_collaborator(self, owner: str, repo: str, username: str, permission: str = "push") -> dict:
        rate_limiter.check_mutation_budget(self.account_id)
        result = await self._request(
            "PUT", f"/repos/{owner}/{repo}/collaborators/{username}",
            json_body={"permission": permission}
        )
        import database as db
        db.increment_mutations(self.account_id)
        return result

    async def get_user_by_token(self) -> dict:
        """Get user info for the second account (used for co-author metadata)."""
        return await self._request("GET", "/user")
