"""Semantic Scholar MCP server (Streamable HTTP) for the local stack (D-016).

Written here because no published server met the API's terms for keyed use: every request
carries the key and goes through one limiter below 1 request/s, 429s back off, and paper
details use the batch endpoint with only the fields that are needed.

- One process, one asyncio limiter: all tool calls, parallel or not, and the weekly digest
  share it. Requests start at least S2_MIN_INTERVAL seconds apart (default 1.1).
- 429 and 5xx: retry with exponential backoff (honouring Retry-After), each retry through the
  limiter again, for at most S2_RETRY_BUDGET seconds (default 60) per tool call. Then the call
  fails with "Semantic Scholar unavailable, try OpenAlex", so the model moves on.
- The key comes from S2_API_KEY and is sent as x-api-key. It is never logged: httpx's request
  logging is off and errors report only the status and the path.

Data: Semantic Scholar Academic Graph API (https://www.semanticscholar.org/product/api).
"""

import asyncio
import logging
import os
import random
import time

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings

API = "https://api.semanticscholar.org/graph/v1"
MIN_INTERVAL = float(os.environ.get("S2_MIN_INTERVAL", "1.1"))
RETRY_BUDGET = float(os.environ.get("S2_RETRY_BUDGET", "60"))
UNAVAILABLE = "Semantic Scholar unavailable, try OpenAlex"
LIST_FIELDS = "title,year,venue,authors,externalIds,citationCount"
DETAIL_FIELDS = (
    LIST_FIELDS + ",abstract,tldr,publicationDate,referenceCount,openAccessPdf"
)
EDGE_FIELDS = "title,year,venue,externalIds,citationCount"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
for noisy in ("httpx", "httpcore"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
log = logging.getLogger("s2")


class Limiter:
    """Hands out start times at least `interval` seconds apart, in call order."""

    def __init__(self, interval: float):
        self.interval = interval
        self.lock = asyncio.Lock()
        self.next_start = 0.0

    async def wait(self) -> None:
        async with self.lock:
            now = time.monotonic()
            delay = self.next_start - now
            self.next_start = max(now, self.next_start) + self.interval
        if delay > 0:
            await asyncio.sleep(delay)


limiter = Limiter(MIN_INTERVAL)
_client: httpx.AsyncClient | None = None


def client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        headers = {"User-Agent": "local-llm-s2-mcp"}
        key = os.environ.get("S2_API_KEY", "").strip()
        if key:
            headers["x-api-key"] = key
        _client = httpx.AsyncClient(base_url=API, headers=headers, timeout=30)
    return _client


async def request(method: str, path: str, **kwargs) -> dict:
    deadline = time.monotonic() + RETRY_BUDGET
    attempt = 0
    while True:
        await limiter.wait()
        log.info("%s %s", method, path)
        try:
            resp = await client().request(method, path, **kwargs)
        except httpx.TransportError as e:
            status, retry_after = None, None
            reason = type(e).__name__
        else:
            if resp.status_code < 400:
                return resp.json()
            status, reason = resp.status_code, resp.reason_phrase
            retry_after = resp.headers.get("retry-after")
            if status == 404:
                raise ValueError(f"not found: {path}")
            if status not in (429, 500, 502, 503, 504):
                raise RuntimeError(
                    f"Semantic Scholar answered {status} {reason} for {path}"
                )
        # the next attempt also waits for its limiter slot, so leave room for that
        left = deadline - time.monotonic() - limiter.interval
        if retry_after and retry_after.isdigit():
            backoff = float(retry_after)
        else:
            # shorten only our own backoff, so the last retry still fits in the budget
            backoff = min(2**attempt + random.uniform(0, 0.5), left)
        if backoff > left or left < 1:
            log.warning("%s %s: %s %s, giving up", method, path, status, reason)
            raise ToolError(UNAVAILABLE)
        attempt += 1
        log.warning(
            "%s %s: %s %s, retry %d in %.1f s",
            method,
            path,
            status,
            reason,
            attempt,
            backoff,
        )
        await asyncio.sleep(backoff)


def ident(p: dict) -> str:
    ext = p.get("externalIds") or {}
    for key, prefix in (
        ("DOI", "https://doi.org/"),
        ("ArXiv", "https://arxiv.org/abs/"),
    ):
        if ext.get(key):
            return prefix + ext[key]
    if ext.get("PubMed"):
        return f"https://pubmed.ncbi.nlm.nih.gov/{ext['PubMed']}/"
    return f"https://www.semanticscholar.org/paper/{p.get('paperId')}"


def line(p: dict) -> str:
    authors = [a.get("name", "") for a in p.get("authors") or []]
    who = ", ".join(authors[:3]) + (" et al" if len(authors) > 3 else "")
    venue = p.get("venue") or "no venue"
    parts = [f"{p.get('title')} ({p.get('year') or 'n.d.'})", who, venue]
    return (
        "- " + ". ".join(x for x in parts if x) + ". "
        f"Cited by {p.get('citationCount', '?')}. {ident(p)} [S2 {p.get('paperId')}]"
    )


security = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=[
        "semantic-scholar",
        "semantic-scholar:8000",
        "127.0.0.1:*",
        "localhost:*",
    ],
    allowed_origins=[],
)
mcp = FastMCP(
    "semantic-scholar",
    instructions="Semantic Scholar: papers across all fields, including CS conference papers, "
    "with citation counts and citation graphs. Requests are rate limited; ask for what you need.",
    host="0.0.0.0",
    port=8000,
    transport_security=security,
)


@mcp.tool()
async def search_papers(
    query: str,
    limit: int = 10,
    year: str = "",
    venue: str = "",
    fields_of_study: str = "",
) -> str:
    """Search papers by relevance.

    :param query: plain-text search words
    :param limit: number of results, 1 to 50
    :param year: optional year or range, e.g. "2024", "2022-2025", "2023-"
    :param venue: optional comma-separated venues, e.g. "CVPR,ICCV,ECCV"
    :param fields_of_study: optional, e.g. "Computer Science" or "Medicine"
    """
    params = {"query": query, "limit": max(1, min(limit, 50)), "fields": LIST_FIELDS}
    if year:
        params["year"] = year
    if venue:
        params["venue"] = venue
    if fields_of_study:
        params["fieldsOfStudy"] = fields_of_study
    data = await request("GET", "/paper/search", params=params)
    papers = data.get("data") or []
    head = f"{data.get('total', 0)} results for {query!r}; showing {len(papers)}:"
    return (
        "\n".join([head, *map(line, papers)])
        if papers
        else f"No results for {query!r}."
    )


@mcp.tool()
async def get_papers(ids: list[str]) -> str:
    """Details (abstract, TL;DR, open-access PDF) for up to 100 papers in one request.

    :param ids: paper ids: S2 ids, or prefixed ones such as "DOI:10.1/xyz", "ARXIV:2404.08471",
        "PMID:123456", "CorpusId:123"
    """
    ids = ids[:100]
    data = await request(
        "POST", "/paper/batch", params={"fields": DETAIL_FIELDS}, json={"ids": ids}
    )
    out = []
    for asked, p in zip(ids, data):
        if p is None:
            out.append(f"## {asked}\nNot found.")
            continue
        tldr = (p.get("tldr") or {}).get("text")
        pdf = (p.get("openAccessPdf") or {}).get("url")
        out.append(
            "\n".join(
                filter(
                    None,
                    [
                        f"## {p.get('title')}",
                        line(p)[2:],
                        f"Published: {p.get('publicationDate') or p.get('year')}. References: {p.get('referenceCount')}.",
                        f"TL;DR: {tldr}" if tldr else None,
                        f"Abstract: {p.get('abstract')}"
                        if p.get("abstract")
                        else "No abstract available.",
                        f"Open-access PDF: {pdf}" if pdf else None,
                    ],
                )
            )
        )
    return "\n\n".join(out)


@mcp.tool()
async def citation_graph(
    paper_id: str, direction: str = "citations", limit: int = 100, offset: int = 0
) -> str:
    """Papers that cite a paper ("citations") or that it cites ("references").

    Citations come newest first; page through older ones with offset (offset + limit <= 9999).

    :param paper_id: an id as in get_papers, e.g. "ARXIV:2404.08471" or "DOI:..."
    :param direction: "citations" or "references"
    :param limit: 1 to 1000
    :param offset: index of the first result, for paging
    """
    if direction not in ("citations", "references"):
        raise ValueError('direction must be "citations" or "references"')
    params = {
        "fields": EDGE_FIELDS,
        "limit": max(1, min(limit, 1000)),
        "offset": max(0, offset),
    }
    data = await request("GET", f"/paper/{paper_id}/{direction}", params=params)
    key = "citingPaper" if direction == "citations" else "citedPaper"
    papers = [e[key] for e in data.get("data") or [] if e.get(key, {}).get("title")]
    if not papers:
        return f"No {direction} found for {paper_id} at offset {offset}."
    more = (
        f"; next page: offset={data['next']}" if data.get("next") else "; no more pages"
    )
    head = f"{len(papers)} {direction} of {paper_id} from offset {offset}{more}:"
    return "\n".join([head, *map(line, papers)])


if __name__ == "__main__":
    log.info(
        "Semantic Scholar MCP: key %s, min interval %.2f s, retry budget %.0f s",
        "set" if os.environ.get("S2_API_KEY") else "not set",
        MIN_INTERVAL,
        RETRY_BUDGET,
    )
    mcp.run(transport="streamable-http")
