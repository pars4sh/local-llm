"""OpenAlex MCP server (Streamable HTTP) for the local stack (D-016).

Written here because neither published server fit the key's terms: cyanheads' sends the key as
an api_key query parameter, and the official one runs only as a Cloudflare Worker behind OAuth.

- The key comes from OPENALEX_API_KEY and is sent as "Authorization: Bearer", never in a URL.
  It is never logged.
- Cheap calls first: a single work is a free singleton; several are one list call with up to
  100 ids joined by |; citations use the cites:/cited_by: filters (list calls); searches ask
  for few results and only the fields shown (select=). Rerank doubles a search's cost, so it
  is on only for best-match lookups. Entities are filtered by id, never by name.
- Requests start at least 1/OPENALEX_MAX_RPS apart (the free key allows 30/s).
- 429 and 5xx, timeouts: up to 5 tries with 1, 2, 4, 8 s waits, 30 s per request. A 429 with
  "not_enough_credits" is never retried: the call fails with "OpenAlex daily budget used up,
  resets at midnight UTC".
- Each request logs its credits and X-RateLimit-Remaining.

Data: OpenAlex API (https://help.openalex.org/api/), CC0.
"""

import asyncio
import logging
import os
import re
import time

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings

API = "https://api.openalex.org"
MAX_RPS = float(os.environ.get("OPENALEX_MAX_RPS", "25"))
CORPUS = os.environ.get("OPENALEX_CORPUS", "")
TRIES = 5
TIMEOUT = 30
BUDGET_USED_UP = "OpenAlex daily budget used up, resets at midnight UTC"
UNAVAILABLE = "OpenAlex unavailable, try Semantic Scholar"
LIST_SELECT = (
    "id,doi,title,publication_year,primary_location,cited_by_count,authorships,ids,"
    "abstract_inverted_index"
)
DETAIL_SELECT = (
    LIST_SELECT + ",publication_date,type,open_access,referenced_works_count"
)
# Records that aren't papers: figure and movie files, supplements, datasets, front matter, reviews
# of reviews. Left out of searches and citation lists (a filter costs nothing extra).
NOT_PAPERS = [
    f"type:!{t}"
    for t in ("other", "supplementary-materials", "dataset", "paratext", "peer-review")
]
SORTS = {
    "relevance": "relevance_score:desc",
    "citations": "cited_by_count:desc",
    "newest": "publication_date:desc",
}
ENTITIES = {
    "author": ("authors", "authorships.author.id"),
    "institution": ("institutions", "authorships.institutions.id"),
    "source": ("sources", "primary_location.source.id"),
    "topic": ("topics", "topics.id"),
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
for noisy in ("httpx", "httpcore"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
log = logging.getLogger("openalex")


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


limiter = Limiter(1 / MAX_RPS)
_client: httpx.AsyncClient | None = None


def client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        headers = {"User-Agent": "local-llm-openalex-mcp", "Accept": "application/json"}
        key = os.environ.get("OPENALEX_API_KEY", "").strip()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        _client = httpx.AsyncClient(base_url=API, headers=headers, timeout=TIMEOUT)
    return _client


async def get(path: str, params: dict | None = None) -> dict:
    params = dict(params or {})
    if CORPUS and path in ("/works",):
        params["corpus"] = CORPUS
    for attempt in range(TRIES):
        await limiter.wait()
        try:
            resp = await client().get(path, params=params)
        except httpx.TransportError as e:
            status, why = None, type(e).__name__
        else:
            h = resp.headers
            log.info(
                "GET %s %s: %s, credits %s, remaining %s",
                path,
                ",".join(sorted(params)),
                resp.status_code,
                h.get("x-ratelimit-credits-used", "?"),
                h.get("x-ratelimit-remaining", "?"),
            )
            if resp.status_code < 400:
                return resp.json()
            status, why = resp.status_code, resp.reason_phrase
            try:
                body = resp.json()
            except ValueError:
                body = {}
            if status == 429 and body.get("error") == "not_enough_credits":
                log.warning("daily budget used up: %s", str(body)[:300])
                raise ToolError(BUDGET_USED_UP)
            if status == 404:
                raise ToolError(f"Not found in OpenAlex: {path}")
            if status != 429 and status < 500:
                msg = body.get("message") or body.get("error") or why
                raise ToolError(f"OpenAlex rejected the request: {str(msg)[:400]}")
        if attempt == TRIES - 1:
            break
        log.warning(
            "GET %s: %s %s, retry %d in %d s",
            path,
            status,
            why,
            attempt + 1,
            2**attempt,
        )
        await asyncio.sleep(2**attempt)
    raise ToolError(UNAVAILABLE)


def short_id(url: str | None) -> str:
    return (url or "").rsplit("/", 1)[-1]


def work_ref(raw: str) -> str:
    """A work id as OpenAlex takes it: W..., doi:..., pmid:... (arXiv ids via their DOI)."""
    s = raw.strip()
    low = s.lower()
    for prefix in ("https://openalex.org/", "openalex:"):
        if low.startswith(prefix):
            return s[len(prefix) :]
    if low.startswith(("https://doi.org/", "doi:")):
        return "doi:" + s.split("doi.org/", 1)[-1].split(":", 1)[-1]
    if low.startswith("10."):
        return "doi:" + s
    if low.startswith("arxiv:"):
        return "doi:10.48550/arXiv." + s.split(":", 1)[1]
    if low.startswith("pmid:") or low.isdigit():
        return "pmid:" + s.split(":", 1)[-1]
    return s


def link(w: dict) -> str:
    if w.get("doi"):
        return w["doi"]
    pmid = (w.get("ids") or {}).get("pmid")
    return pmid or w.get("id", "")


def line(w: dict) -> str:
    names = [
        (a.get("author") or {}).get("display_name", "")
        for a in w.get("authorships") or []
    ]
    who = ", ".join(names[:3]) + (" et al" if len(names) > 3 else "")
    source = ((w.get("primary_location") or {}).get("source") or {}).get("display_name")
    parts = [f"{w.get('title')} ({w.get('publication_year') or 'n.d.'})", who, source]
    return (
        "- " + ". ".join(x for x in parts if x) + ". "
        f"Cited by {w.get('cited_by_count', '?')}. {link(w)} [OpenAlex {short_id(w.get('id'))}]"
    )


def first_sentences(text: str | None) -> str:
    """The first two sentences, at most about 450 characters, marked " …" when the text goes
    on; "not available" when there is none (D-023)."""
    t = " ".join((text or "").split())
    if not t:
        return "not available"
    out = " ".join(re.split(r"(?<=[.!?])\s+(?=[A-Z0-9(\[])", t)[:2])
    if len(out) > 450:
        out = out[:450].rsplit(" ", 1)[0]
    return f"{out} …" if len(out) < len(t) else out


def listing(head: str, data: dict) -> str:
    works = data.get("results") or []
    if not works:
        return f"{head}: nothing found."
    total = (data.get("meta") or {}).get("count")
    rows = [
        f"{line(w)}\n  abstract: {first_sentences(abstract(w.get('abstract_inverted_index')))}"
        for w in works
    ]
    return "\n".join([f"{head}: {total} in total; showing {len(works)}:", *rows])


def date_filters(from_date: str, to_date: str) -> list[str]:
    out = []
    if from_date:
        out.append(f"from_publication_date:{from_date}")
    if to_date:
        out.append(f"to_publication_date:{to_date}")
    return out


def abstract(inverted: dict | None) -> str | None:
    if not inverted:
        return None
    pos = {i: word for word, idx in inverted.items() for i in idx}
    return " ".join(pos[i] for i in sorted(pos))


security = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=["openalex", "openalex:8000", "127.0.0.1:*", "localhost:*"],
    allowed_origins=[],
)
mcp = FastMCP(
    "openalex",
    instructions="OpenAlex: scholarly works in all fields, citation counts and citation graphs. "
    "Each search spends daily budget; looking up known works by id is cheap.",
    host="0.0.0.0",
    port=8000,
    transport_security=security,
)


@mcp.tool()
async def search_works(
    query: str = "",
    sort: str = "relevance",
    limit: int = 10,
    from_date: str = "",
    to_date: str = "",
    filter_ids: list[str] | None = None,
    best_match: bool = False,
) -> str:
    """Search works in titles, abstracts and keywords. Returns titles, first authors, venues,
    citation counts, DOIs and OpenAlex ids; get_works gives abstracts.

    :param query: search words; AND, OR, NOT (uppercase) and "quoted phrases" work. May be
        empty when filter_ids is given (e.g. all works of an author, newest first).
    :param sort: "relevance", "citations" (most cited first) or "newest"
    :param limit: number of results, 1 to 25
    :param from_date: optional earliest publication date, YYYY-MM-DD
    :param to_date: optional latest publication date, YYYY-MM-DD
    :param filter_ids: optional OpenAlex ids from find_entity to restrict to: authors (A...),
        institutions (I...), sources/journals (S...) or topics (T...). Never names.
    :param best_match: true only to find one specific paper or the few closest matches:
        reranks the top results by how well they answer the query, at twice the cost.
    """
    if sort not in SORTS:
        raise ToolError('sort must be "relevance", "citations" or "newest"')
    if not query and not filter_ids:
        raise ToolError("give a query, filter_ids, or both")
    filters = [*NOT_PAPERS, *date_filters(from_date, to_date)]
    by_kind: dict[str, list[str]] = {}
    for raw in filter_ids or []:
        i = short_id(raw).upper()
        kind = {"A": "author", "I": "institution", "S": "source", "T": "topic"}.get(
            i[:1]
        )
        if not kind:
            raise ToolError(f"{raw} is not an author, institution, source or topic id")
        by_kind.setdefault(kind, []).append(i)
    filters += [f"{ENTITIES[k][1]}:{'|'.join(v)}" for k, v in by_kind.items()]
    params = {
        "filter": ",".join(filters),
        "select": LIST_SELECT,
        "per_page": max(1, min(limit, 25)),
        "sort": SORTS[sort] if query or sort != "relevance" else SORTS["newest"],
    }
    if query:
        params["search.title_abstract_keywords"] = query
    if best_match and query and sort == "relevance":
        params["rerank"] = "true"
    data = await get("/works", params)
    return listing(f"Works for {query or 'the given ids'!r} by {sort}", data)


@mcp.tool()
async def get_works(ids: list[str]) -> str:
    """Details (abstract, type, open access, reference count) for up to 100 known works.

    :param ids: OpenAlex ids (W...), DOIs (10.x/... or https://doi.org/...), "pmid:123" or
        "arXiv:2404.08471"
    """
    refs = [work_ref(i) for i in ids[:100]]
    if len(refs) == 1:
        works = [await get(f"/works/{refs[0]}", {"select": DETAIL_SELECT})]
    else:
        # One list call per id kind, ids joined by | (a filter takes up to 100 values).
        groups: dict[str, list[str]] = {}
        for r in refs:
            kind, _, val = r.partition(":") if ":" in r else ("openalex", "", r)
            groups.setdefault(kind, []).append(val)
        works = []
        for kind, vals in groups.items():
            data = await get(
                "/works",
                {
                    "filter": f"{'ids.openalex' if kind == 'openalex' else kind}:{'|'.join(vals)}",
                    "select": DETAIL_SELECT,
                    "per_page": len(vals),
                },
            )
            works += data.get("results") or []
    out = []
    for w in works:
        oa = (w.get("open_access") or {}).get("oa_url")
        text = abstract(w.get("abstract_inverted_index"))
        out.append(
            "\n".join(
                filter(
                    None,
                    [
                        f"## {w.get('title')}",
                        line(w)[2:],
                        f"Published: {w.get('publication_date')}. Type: {w.get('type')}. "
                        f"References: {w.get('referenced_works_count')}.",
                        f"Abstract: {text}" if text else "No abstract available.",
                        f"Open access: {oa}" if oa else None,
                    ],
                )
            )
        )
    missing = len(refs) - len(works)
    if missing:
        out.append(f"{missing} of the ids were not found.")
    return "\n\n".join(out) or "None of the ids were found."


@mcp.tool()
async def citation_graph(
    work_id: str,
    direction: str = "citations",
    sort: str = "citations",
    limit: int = 25,
    page: int = 1,
    from_date: str = "",
) -> str:
    """Works that cite a work ("citations") or that it cites ("references").

    :param work_id: an id as in get_works
    :param direction: "citations" or "references"
    :param sort: "citations" (most cited first) or "newest"
    :param limit: 1 to 100
    :param page: page number, for more results
    :param from_date: optional earliest publication date, YYYY-MM-DD
    """
    if direction not in ("citations", "references"):
        raise ToolError('direction must be "citations" or "references"')
    if sort not in ("citations", "newest"):
        raise ToolError('sort must be "citations" or "newest"')
    ref = work_ref(work_id)
    if not ref.upper().startswith("W"):
        w = await get(f"/works/{ref}", {"select": "id"})
        ref = short_id(w.get("id"))
    edge = "cites" if direction == "citations" else "cited_by"
    params = {
        "filter": ",".join(
            [f"{edge}:{ref}", *NOT_PAPERS, *date_filters(from_date, "")]
        ),
        "select": LIST_SELECT,
        "per_page": max(1, min(limit, 100)),
        "page": max(1, page),
        "sort": SORTS[sort],
    }
    data = await get("/works", params)
    return listing(f"{direction.capitalize()} of {ref}, page {page}, by {sort}", data)


@mcp.tool()
async def find_entity(name: str, entity_type: str = "author") -> str:
    """Look up OpenAlex ids for an author, institution, source (journal, conference) or
    topic by name, to pass to search_works as filter_ids.

    :param name: the name to look up
    :param entity_type: "author", "institution", "source" or "topic"
    """
    if entity_type not in ENTITIES:
        raise ToolError(
            'entity_type must be "author", "institution", "source" or "topic"'
        )
    data = await get(
        f"/{ENTITIES[entity_type][0]}",
        {
            "search": name,
            "per_page": 5,
            "select": "id,display_name,works_count,cited_by_count"
            + (",last_known_institutions" if entity_type == "author" else ""),
        },
    )
    rows = []
    for e in data.get("results") or []:
        inst = ", ".join(
            i.get("display_name", "") for i in e.get("last_known_institutions") or []
        )
        rows.append(
            f"- {short_id(e.get('id'))}: {e.get('display_name')}"
            + (f" ({inst})" if inst else "")
            + f". {e.get('works_count')} works, cited {e.get('cited_by_count')} times."
        )
    return "\n".join(rows) or f"No {entity_type} found for {name!r}."


if __name__ == "__main__":
    log.info(
        "OpenAlex MCP: key %s, max %.0f requests/s, corpus %s",
        "set" if os.environ.get("OPENALEX_API_KEY", "").strip() else "not set",
        MAX_RPS,
        CORPUS or "default",
    )
    mcp.run(transport="streamable-http")
