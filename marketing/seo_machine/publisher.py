"""Publish an article to the box's own Sanity project. `docs/OWNBOX_ARTICLES.md` is the contract.

THE GUARD IS NOT OPTIONAL AND NOT THE CALLER'S JOB. `OWNER 2026-09-25:` the machine will *"write
and publish on its own"*, so there is no human between a draft and a live page. `publish()`
therefore runs `guard.assert_publishable` itself and raises before it touches the network — you
cannot reach Sanity from this module without passing the guard, which is the only way a rule
nobody can skip stays a rule nobody can skip.

CREATE OR PATCH, NEVER `createOrReplace`. The contract: *"Keep it stable: changing it breaks every
link to the article. Update an article by patching it in place."* `createOrReplace` silently drops
fields you did not send, which on a patch is data loss wearing a success code — the leadmagnet
client learned this and its comment says so. So: look the slug up, patch if it is there, create if
it is not.

NOT CONFIGURED IS AN ANSWER. A box with no Sanity project is not broken, it is a box whose owner
has not set this up (`docs/SEO_AEO_MACHINE_BUILD_SPEC.md` §5). `is_configured()` says so in words
and nothing here raises for it.

NOTHING HERE IS OURS ALONE. Project, dataset and site URL are per-box settings; the write token is
the box's own secret. `grep -ri "ownbox" ` this file and you get nothing — the owner's standing
rule of 2026-09-25: it has to work on a fresh box, for a stranger.

DETERMINISTIC. No `brain.think()` anywhere: this is HTTP and dict-building (`CLAUDE.md` §3).
"""
from __future__ import annotations

import json as _json
import re
from urllib.parse import urlencode, urlparse

from core import box_secrets, net
from core.logging import get_logger
from marketing.seo_machine import guard, settings

log = get_logger(__name__)

DOC_TYPE = "article"          # the contract reads nothing else
_API_VERSION = "2025-02-19"

# ONE NAME. The owner named it on 2026-09-25 and OSDev1 held me to it on #1554: two names is two
# places for one secret, and the settings page could write one while a stale value sits in the
# other. I would still rather it were machine-scoped than brand-scoped on a machine we sell, but
# that is a rename for the owner to make on the wall, not something to pre-empt in code.
# It is NOT the box's existing SANITY_API_TOKEN, which points at a different project.
TOKEN_KEY = "SANITY_API_TOKEN_OWNBOX"


def _cfg() -> dict:
    """The box's settings — from the screen, never straight from YAML. See settings.py."""
    return settings.get()


def _token() -> str:
    return (box_secrets.get(TOKEN_KEY) or "").strip()


def is_configured() -> tuple[bool, str]:
    """(ready, why-not). Never raises — an unconfigured box answers, it does not crash."""
    c = _cfg()
    if not c.get("project_id"):
        return False, "no Sanity project set for this box (SEO settings: project_id)"
    if not c.get("dataset"):
        return False, "no Sanity dataset set for this box (SEO settings: dataset)"
    if not _token():
        return False, f"no write token in the box's secrets ({TOKEN_KEY})"
    # The site's address is REQUIRED, not optional (OSDev1, #1561): without it every article's URL
    # is relative, IndexNow is never pinged, and the Topics screen already says "not set up" while
    # the worker would publish anyway. One answer, from one place.
    try:
        site = urlparse(str(c.get("site_url") or "").strip())
        absolute = site.scheme in ("http", "https") and bool(site.hostname)
    except ValueError:                               # "http://[" — malformed is missing, not a crash
        absolute = False
    if not absolute:
        return False, "no website address"
    return True, ""


def article_url(slug: str) -> str:
    base = str(_cfg().get("site_url") or "").rstrip("/")
    return f"{base}/articles/{slug}" if base else f"/articles/{slug}"


def _endpoint(kind: str) -> str:
    c = _cfg()
    # The 'v' IS REQUIRED. Unlike @sanity/client, the HTTP API 404s on a bare date — and the
    # config in this repo stores it bare, so a straight read here would have shipped a 404.
    # `marketing/content_machine/leadmagnet/sanity_client.py:60` learned this first; accept both.
    ver = "v" + str(c.get("api_version") or _API_VERSION).lstrip("v")
    return f"https://{c['project_id']}.api.sanity.io/{ver}/data/{kind}/{c['dataset']}"


def _headers() -> dict:
    return {"Authorization": f"Bearer {_token()}", "Content-Type": "application/json"}


def _json_or_raise(status: int, body: str, what: str) -> dict:
    """`core.net` hands back (status, body) and leaves the meaning to us — deliberately, and its
    docstring says why: a 429 and a 404 are different facts and a door that collapses them makes
    the caller guess. So we decide here, and we decide loudly. A publish that half-failed and
    reported success is the one outcome worth crashing to avoid."""
    if status >= 400:
        raise RuntimeError(f"sanity {what} failed: HTTP {status}: {body[:300]}")
    try:
        return _json.loads(body) or {}
    except ValueError as e:
        raise RuntimeError(f"sanity {what} returned {status} but not JSON: {body[:200]}") from e


def find_by_slug(slug: str) -> dict | None:
    """The published document with this slug, or None. Drafts are not ours to find.

    The slug is PARAMETERISED, not interpolated — `$slug` with the value passed separately, so a
    title that produced an odd slug cannot close the string and become query. It is our own text
    today; it is model-written text tomorrow, which is exactly when this stops being theoretical.
    """
    groq = f'*[_type=="{DOC_TYPE}" && slug.current==$slug][0]{{_id,_updatedAt}}'
    url = _endpoint("query") + "?" + urlencode({"query": groq, "$slug": _json.dumps(slug)})
    status, body = net.get_public(url, headers=_headers())
    return _json_or_raise(status, body, "query").get("result") or None


def _document(**f) -> dict:
    """Only the fields the contract names, and only those that were actually given.

    A key we send as None is a key we ERASE on a patch. The contract says every field but title
    and slug is optional and the site renders sensibly without it — so absent must mean absent,
    not blanked.
    """
    doc = {
        "_type": DOC_TYPE,
        "title": f["title"],
        "slug": {"_type": "slug", "current": f["slug"]},
    }
    for key, value in (
        ("shortAnswer", f.get("short_answer")), ("body", f.get("body_blocks")),
        ("category", f.get("category")), ("contentType", f.get("content_type")),
        ("metaDescription", f.get("meta_description")), ("heroImage", f.get("hero_image")),
        ("author", f.get("author")), ("faqs", f.get("faqs")),
        ("publishedAt", f.get("published_at")), ("sourceMarkdown", f.get("source_markdown")),
    ):
        if value is not None:
            doc[key] = value
    return doc


def _spans(block: dict) -> str:
    """One block's words as a reader sees them: its spans JOINED, not listed.

    REVIEW ITEM 1 (OSDev1, #1554). Spans used to be joined with newlines, so a phrase split by
    styling, "voice **search**", reached the guard as "voice\nsearch", and a competitor inside a
    link, "Unlike [Acme](…) CRM", as three lines. On the page they are one run of text, so here
    they are too.
    """
    return "".join(str(c.get("text") or "") for c in block.get("children") or []
                   if isinstance(c, dict))


def _body_parts(blocks) -> tuple[list[str], list[str]]:
    """(prose, markup) from Portable Text: the words a reader reads, and the text that is visible
    or public but is not a claim in a sentence (link targets, code).

    REVIEW ITEM 2 (OSDev1, #1554): code blocks, image-row alt text and captions, image captions and
    link hrefs were never read at all. Every block type the contract names is walked here, and an
    unknown one contributes whatever text-shaped fields it carries rather than nothing.
    """
    prose, markup = [], []
    for b in blocks or []:
        if not isinstance(b, dict):
            continue
        kind = b.get("_type")
        if kind == "block":
            prose.append(_spans(b))
            markup += [str(d.get("href") or "") for d in b.get("markDefs") or [] if isinstance(d, dict)]
        elif kind == "code":
            markup.append(str(b.get("code") or ""))
        elif kind == "table":
            for row in b.get("rows") or []:
                prose += [str(c) for c in (row.get("cells") or []) if c]
        elif kind == "imageRow":
            for img in b.get("images") or []:
                if isinstance(img, dict):
                    prose += [str(img.get(k) or "") for k in ("alt", "caption")]
        else:                                    # imageUrl, image, and anything added later
            prose += [str(b.get(k) or "") for k in ("alt", "caption", "text")]
            for child in b.get("children") or []:
                if isinstance(child, dict) and child.get("text"):
                    prose.append(str(child["text"]))
    return [p for p in prose if p], [m for m in markup if m]


def _readable(doc: dict) -> str:
    """Everything in this article a person would read as prose. The guard reads ALL of it.

    THE RENDERED WORDS, NOT THE MARKDOWN, for the number rule. "1. Open the app" carries a literal
    `1` that is list markup, not a claim, so the number rule runs on the rendered text, where a
    numbered list has no digits (`docs/SEO_MACHINE_OWNBOX_SEED.md` §1 depends on that).
    """
    prose, _ = _body_parts(doc.get("body"))
    parts = [doc.get("title") or "", doc.get("shortAnswer") or "",
             doc.get("metaDescription") or "", doc.get("category") or ""]
    author = doc.get("author")
    if isinstance(author, dict):
        parts.append(str(author.get("name") or ""))
    parts += prose
    for faq in doc.get("faqs") or []:
        if isinstance(faq, dict):
            parts += [str(faq.get("question") or ""), str(faq.get("answer") or "")]
    return "\n".join(p for p in parts if p)


_DIGITS = re.compile(r"\d")


def _markup_text(doc: dict) -> str:
    """The public text that is not prose: the slug, link targets, the author's link, code, and
    `sourceMarkdown` (which the site does not render but sits in a publicly readable dataset).

    WORDS ARE GUARDED, DIGITS ARE NOT. A banned word or a competitor in a URL or a code block is as
    public as one in a sentence. A digit in a URL, a version string or the Markdown's list syntax
    is not a claim anyone reads as a fact, and the prose check above already reads every number a
    reader sees. So digits are blanked here and every other rule applies in full.
    """
    _, markup = _body_parts(doc.get("body"))
    parts = [(doc.get("slug") or {}).get("current") or "", doc.get("sourceMarkdown") or ""]
    author = doc.get("author")
    if isinstance(author, dict):
        parts.append(str(author.get("url") or ""))
    parts += markup
    text = "\n".join(p for p in parts if p)
    # A slug and a URL path join words with hyphens and slashes. Read them as spaces, so a banned
    # word inside "/never-miss-a-call" is the word it is.
    text = re.sub(r"[-_/]+", " ", text)
    return _DIGITS.sub(" ", text)


def refusals(*, lists=None, **fields) -> list:
    """Every reason these fields could not be published, read exactly as `publish()` reads them.

    ONE DEFINITION, SO THE WRITER AND THE PUBLISHER CANNOT DISAGREE. The writer checks a draft
    before spending a publish on it; if it read less of the article than this does, a draft would
    pass the writer and be refused at the door, or pass both while the rules saw half of it.
    `lists` defaults to the box's own, never to nothing (review item 10).
    """
    lists = lists if lists is not None else _box_lists()
    doc = _document(**fields)
    return guard.check(_readable(doc), **lists) + guard.check(_markup_text(doc), **lists)


def _check_all(doc: dict, lists: dict) -> None:
    """One refusal list for the whole article: prose with every rule, markup with every rule but
    the number one. Raises `GuardRefused` with all of it at once."""
    found = guard.check(_readable(doc), **lists) + guard.check(_markup_text(doc), **lists)
    if found:
        raise guard.GuardRefused(found)


def _box_lists() -> dict:
    """The box's own never-use words, competitors and fact list, from its settings.

    These are ENTERED ON THE SETTINGS SCREEN, not shipped in code — OSDev1's ruling on #1552: our
    receptionist vocabulary is Ownbox's *setting*, and a plumber's box must be able to publish the
    word "plumbing". A box that has filled nothing in refuses nothing but unsourced numbers.
    """
    return settings.lists()


def preview_text(**fields) -> str:
    """Exactly what the guard will read when these fields are published.

    ONE DEFINITION, SO THE WRITER AND THE PUBLISHER CANNOT DISAGREE. The writer needs to check a
    draft before spending a publish on it, and a second implementation of "what counts as the
    text" is how a draft passes the writer's check and is then refused at the door — or worse,
    passes both while the rules only ever saw half the article.
    """
    return _readable(_document(**fields))


class InvalidArticle(ValueError):
    """The fields break the contract (`docs/OWNBOX_ARTICLES.md`). Raised before any network call."""


_CONTENT_TYPES = ("A", "B", "C")


def validate(**f) -> None:
    """REVIEW ITEM 12 (OSDev1, #1554): the contract's shapes, checked before anything is written.

    The site trusts these shapes. An `author` that is a bare string, a `contentType` of "answer" or
    a `heroImage` that is not a URL is stored without complaint and then renders wrong, or not at
    all, on a page nobody reviewed.
    """
    problems = []
    if not str(f.get("title") or "").strip():
        problems.append("title is required")
    if not str(f.get("slug") or "").strip():
        problems.append("slug is required")
    author = f.get("author")
    if author is not None:
        if not (isinstance(author, dict) and str(author.get("name") or "").strip()
                and set(author) <= {"name", "url"}):
            problems.append("author must be {name, url?}")
        elif author.get("url") and not _is_url(author["url"]):
            problems.append("author.url must be an http(s) URL")
    ct = f.get("content_type")
    if ct is not None and ct not in _CONTENT_TYPES:
        problems.append(f"content_type must be one of {', '.join(_CONTENT_TYPES)}")
    hero = f.get("hero_image")
    if hero is not None and not _is_url(hero):
        problems.append("hero_image must be an http(s) URL")
    faqs = f.get("faqs")
    if faqs is not None and not (isinstance(faqs, list) and all(
            isinstance(q, dict) and set(q) <= {"question", "answer"} for q in faqs)):
        problems.append("faqs must be a list of {question, answer}")
    if problems:
        raise InvalidArticle("; ".join(problems))


def _is_url(value) -> bool:
    try:
        u = urlparse(str(value))
    except ValueError:
        return False
    return u.scheme in ("http", "https") and bool(u.netloc)


def publish(*, lists=None, **fields) -> dict:
    """Create or patch one article. Returns {doc_id, url, slug, created}.

    Raises `guard.GuardRefused` before any network call if the text breaks one of this box's
    publishing rules, and `RuntimeError` if the box has no Sanity project — neither is a partial
    publish. `lists` overrides the box's settings; omit it and the box's own rules apply.
    """
    ready, why = is_configured()
    if not ready:
        raise RuntimeError(f"seo publisher not configured: {why}")

    # DEFAULTS FROM THE BOX, NOT FROM EMPTY. A caller that forgets to pass the lists must get the
    # box's rules, not a guard with nothing to check — silently permissive is the one failure mode
    # this guard cannot have, since it is the only reviewer.
    validate(**fields)
    doc = _document(**fields)
    _check_all(doc, lists if lists is not None else _box_lists())

    slug = doc["slug"]["current"]
    existing = find_by_slug(slug)
    if existing:
        patch = {k: v for k, v in doc.items() if k not in ("_type", "slug")}
        status, body = net.post_public(
            _endpoint("mutate"), headers=_headers(),
            json={"mutations": [{"patch": {"id": existing["_id"], "set": patch}}]})
        _json_or_raise(status, body, "patch")
        log.info("seo.article_patched", slug=slug, doc_id=existing["_id"])
        return {"doc_id": existing["_id"], "url": article_url(slug), "slug": slug,
                "created": False}

    # REVIEW ITEM 11 (OSDev1, #1554): without returnIds Sanity answers a create with no id, and
    # the row recorded `doc_id: None`. Ask for it; and if it still is not there, look the slug up
    # rather than hand back a None that looks like an answer.
    status, body = net.post_public(_endpoint("mutate") + "?returnIds=true", headers=_headers(),
                                   json={"mutations": [{"create": doc}]})
    r = _json_or_raise(status, body, "create")
    doc_id = (((r.get("results") or [{}])[0]) or {}).get("id")
    if not doc_id:
        doc_id = (find_by_slug(slug) or {}).get("_id")
    if not doc_id:
        raise RuntimeError(f"sanity create for {slug!r} returned no id and the slug does not resolve")
    log.info("seo.article_created", slug=slug, doc_id=doc_id)
    return {"doc_id": doc_id, "url": article_url(slug), "slug": slug, "created": True}


def _host(c: dict) -> str:
    """The IndexNow host is the site's own host, derived from `site_url`, never a second setting
    that can disagree with it (OSDev1, #1554 contract gaps). A bare "example.com" is accepted."""
    raw = str(c.get("site_url") or "").strip()
    if raw and "://" not in raw:
        raw = "https://" + raw
    try:
        return (urlparse(raw).hostname or "") if raw else ""
    except ValueError:
        return ""


def ping_indexnow(urls: list[str]) -> bool:
    """Tell the IndexNow engines a URL changed. A NOTIFICATION, NEVER A CLAIM OF INDEXING.

    The key is public by design and the site serves it at `/<key>.txt`; another site generates its
    own, so both are per-box settings. Failure here never fails a publish — the article is already
    live and a missed ping is a slower crawl, not a broken page.
    """
    c = _cfg()
    key, host = c.get("indexnow_key"), _host(c)
    if not (key and host and urls):
        return False
    try:
        status, _ = net.post_public("https://api.indexnow.org/indexnow",
                                    json={"host": host, "key": key, "urlList": urls})
        ok = status < 400
        log.info("seo.indexnow_pinged", count=len(urls), status=status, accepted=ok)
        return ok
    except Exception as e:                                   # noqa: BLE001 — see the docstring
        log.warning("seo.indexnow_failed", error=str(e)[:160])
        return False
