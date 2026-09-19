"""
GitHub and web search — the two outward-facing helpers the agents need most.

GitHub uses the REST API with a token from the environment; nothing is written
unless a tool explicitly asks and the approval gate allows it.

Web search uses DuckDuckGo's HTML endpoint, which needs no key and no account.
It is deliberately a *search*, not a crawler: it returns titles, URLs and
snippets, and `web.fetch` is the separate tool that reads a page.
"""
from __future__ import annotations

import html
import os
import re
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import httpx

GITHUB_API = "https://api.github.com"


class ExternalError(Exception):
    pass


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


# ── GitHub ───────────────────────────────────────────────────────────────────

class GitHubService:
    def __init__(self) -> None:
        self.token = _env("GITHUB_TOKEN")
        self.default_repo = _env("GITHUB_DEFAULT_REPO")

    def configured(self) -> bool:
        return bool(self.token)

    def unavailable_reason(self) -> str:
        return "" if self.configured() else "GITHUB_TOKEN not set"

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "JARVIS-CommandCenter/0.1"}

    async def _get(self, path: str, params: dict | None = None):
        if not self.configured():
            raise ExternalError(self.unavailable_reason())
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.get(f"{GITHUB_API}{path}", headers=self._headers(), params=params)
        if r.status_code == 401:
            raise ExternalError("GitHub rejected the token.")
        if r.status_code == 403 and "rate limit" in r.text.lower():
            raise ExternalError("GitHub rate limit reached — try again later.")
        if r.status_code == 404:
            raise ExternalError("GitHub found nothing at that path (private repo, or wrong name).")
        if r.status_code >= 400:
            raise ExternalError(f"GitHub refused the request (HTTP {r.status_code}).")
        return r.json()

    def _repo(self, repo: str) -> str:
        repo = (repo or self.default_repo).strip().removeprefix("https://github.com/").strip("/")
        if "/" not in repo:
            raise ExternalError("Name the repository as owner/repo.")
        return repo

    async def read_file(self, repo: str, path: str, ref: str = "") -> dict:
        import base64
        params = {"ref": ref} if ref else None
        data = await self._get(f"/repos/{self._repo(repo)}/contents/{path.lstrip('/')}", params)
        if isinstance(data, list):
            return {"path": path, "type": "directory",
                    "entries": [{"name": i["name"], "type": i["type"], "size": i.get("size", 0)} for i in data]}
        if data.get("encoding") == "base64":
            try:
                content = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
            except Exception:
                content = "(binary file)"
        else:
            content = data.get("content", "")
        return {"path": data.get("path", path), "type": "file", "size": data.get("size", 0),
                "content": content[:60000], "html_url": data.get("html_url", "")}

    async def list_issues(self, repo: str, state: str = "open", limit: int = 10) -> list[dict]:
        data = await self._get(f"/repos/{self._repo(repo)}/issues",
                               {"state": state, "per_page": min(limit, 30)})
        return [{"number": i["number"], "title": i["title"], "state": i["state"],
                 "author": (i.get("user") or {}).get("login", ""), "url": i["html_url"],
                 "is_pull_request": "pull_request" in i, "updated_at": i["updated_at"],
                 "labels": [l["name"] for l in i.get("labels", [])]} for i in data]

    async def read_issue(self, repo: str, number: int) -> dict:
        i = await self._get(f"/repos/{self._repo(repo)}/issues/{int(number)}")
        comments = await self._get(f"/repos/{self._repo(repo)}/issues/{int(number)}/comments",
                                   {"per_page": 20})
        return {"number": i["number"], "title": i["title"], "state": i["state"],
                "author": (i.get("user") or {}).get("login", ""), "body": (i.get("body") or "")[:8000],
                "url": i["html_url"],
                "comments": [{"author": (c.get("user") or {}).get("login", ""),
                              "body": (c.get("body") or "")[:2000]} for c in comments]}

    async def list_commits(self, repo: str, limit: int = 10, branch: str = "") -> list[dict]:
        params: dict = {"per_page": min(limit, 30)}
        if branch:
            params["sha"] = branch
        data = await self._get(f"/repos/{self._repo(repo)}/commits", params)
        return [{"sha": c["sha"][:8], "message": (c["commit"]["message"] or "").split("\n")[0],
                 "author": (c["commit"].get("author") or {}).get("name", ""),
                 "date": (c["commit"].get("author") or {}).get("date", ""), "url": c["html_url"]}
                for c in data]

    async def repo_overview(self, repo: str) -> dict:
        r = await self._get(f"/repos/{self._repo(repo)}")
        return {"full_name": r["full_name"], "description": r.get("description") or "",
                "default_branch": r.get("default_branch", ""), "private": r.get("private", False),
                "open_issues": r.get("open_issues_count", 0), "stars": r.get("stargazers_count", 0),
                "pushed_at": r.get("pushed_at", ""), "url": r["html_url"],
                "language": r.get("language") or ""}

    async def health(self) -> dict:
        if not self.configured():
            return {"status": "not_configured", "detail": self.unavailable_reason()}
        try:
            me = await self._get("/user")
            return {"status": "healthy", "detail": f"authenticated as {me.get('login', '?')}"}
        except ExternalError as e:
            return {"status": "offline", "detail": str(e)}
        except httpx.HTTPError as e:
            return {"status": "offline", "detail": f"cannot reach GitHub ({e.__class__.__name__})"}


# ── Web search ───────────────────────────────────────────────────────────────

_RESULT_RE = re.compile(
    r'<a[^>]+class="result__a"[^>]+href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>'
    r'(?:.*?class="result__snippet"[^>]*>(?P<snippet>.*?)</a>)?',
    re.S | re.I)


def _clean(raw: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", raw or "")).strip()


def _real_url(href: str) -> str:
    """DuckDuckGo wraps results in /l/?uddg=<encoded>; unwrap it."""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        if target:
            return unquote(target)
    return href


async def web_search(query: str, limit: int = 8, region: str = "") -> list[dict]:
    """Titles, URLs and snippets from DuckDuckGo's HTML endpoint. No key needed."""
    if not query.strip():
        raise ExternalError("The search needs a query.")
    data = {"q": query, "kl": region} if region else {"q": query}
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, headers={
                "User-Agent": "Mozilla/5.0 (compatible; JARVIS-CommandCenter/0.1)",
                "Accept-Language": "de,en;q=0.8"}) as c:
            r = await c.post("https://html.duckduckgo.com/html/", data=data)
    except httpx.HTTPError as e:
        raise ExternalError(f"The search engine could not be reached ({e.__class__.__name__}).")
    if r.status_code >= 400:
        raise ExternalError(f"The search engine answered HTTP {r.status_code}.")
    results = []
    for m in _RESULT_RE.finditer(r.text):
        url = _real_url(m.group("url"))
        title = _clean(m.group("title"))
        if not url or not title:
            continue
        results.append({"title": title, "url": url, "snippet": _clean(m.group("snippet") or "")[:400]})
        if len(results) >= max(1, min(limit, 20)):
            break
    if not results and "no results" not in r.text.lower():
        raise ExternalError("The search returned nothing I could read — the engine may have changed its page.")
    return results
