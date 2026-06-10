"""Scout v1 — find sponsorship leads from public evidence.

Pipeline (run by GitHub Actions weekly, or locally: `python jobs/scout_refresh.py`):
  1. Load enabled seeds from scout_seeds (bootstrap from rival_fests.yaml if empty).
  2. For each seed: robots.txt-aware, politeness-delayed fetch of the page plus
     up to 3 same-domain sponsor/partner links; LLM-extract brand names.
  3. Google News RSS search (free, keyless) for fest-sponsorship coverage.
  4. ANTI-HALLUCINATION GATE: an extracted brand is written ONLY if its name
     appears verbatim (case-insensitive) in the fetched source text.
  5. Idempotent writes: brands get-or-create on normalized_name; evidence
     upserts on (brand_id, source_url); leads are insert-if-absent — on
     existing leads Scout updates ONLY evidence_score, never status/owner.
  6. Run summary written to scout_runs (shown on the Admin page).

Failure policy: a broken site logs and skips; invalid LLM output retries once
then skips; all providers down => checkpoint, mark run 'partial', exit 0.
"""
from __future__ import annotations

import io
import logging
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
import yaml
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.llm import LLMUnavailableError, extract_json  # noqa: E402
from core.scoring import (  # noqa: E402
    compute_evidence_score,
    normalize_brand_name,
    priority_from_score,
)

logger = logging.getLogger("scout")

USER_AGENT = "SponsorOS-Scout/1.0 (ACM SIGAI MUJ student chapter; respects robots.txt)"
POLITENESS_DELAY_SECONDS = 3.0   # minimum gap between requests to the same host
FEST_TARGET = os.environ.get("SCOUT_FEST_TARGET", "MUJ Fest 2026-27")
MAX_SPONSOR_LINKS_PER_SEED = 3
PAGE_TEXT_CAP = 12_000
NEWS_ARTICLES_PER_QUERY = 8
SEEDS_YAML = ROOT / "jobs" / "seeds" / "rival_fests.yaml"

# (query, is_region_match) — regional coverage is the higher-affinity signal.
NEWS_QUERIES: tuple[tuple[str, bool], ...] = (
    ("college fest sponsors Jaipur", True),
    ("university fest sponsorship Rajasthan", True),
    ("college techfest title sponsor India", False),
)


# ---------------------------------------------------------------------------
# LLM output schemas (strict; validated by core.llm via pydantic)
# ---------------------------------------------------------------------------
class ExtractedBrand(BaseModel):
    brand_name: str = Field(min_length=2, max_length=80)
    snippet: str = Field(min_length=5, max_length=300)
    confidence: float = Field(ge=0, le=1)


class PageExtraction(BaseModel):
    brands: list[ExtractedBrand] = Field(default_factory=list, max_length=40)


class NewsBrand(BaseModel):
    brand_name: str = Field(min_length=2, max_length=80)
    article_index: int = Field(ge=0)
    confidence: float = Field(ge=0, le=1)


class NewsExtraction(BaseModel):
    brands: list[NewsBrand] = Field(default_factory=list, max_length=25)


# ---------------------------------------------------------------------------
# Pure helpers (covered by tests/test_scout_logic.py)
# ---------------------------------------------------------------------------
def normalize_ws(text: str) -> str:
    """Collapse all whitespace runs to single spaces."""
    return re.sub(r"\s+", " ", text).strip()


def brand_on_page(brand_name: str, page_text: str) -> bool:
    """Anti-hallucination gate: the brand string must appear verbatim
    (case-insensitive, whitespace-normalized) in the fetched source text."""
    return normalize_ws(brand_name).lower() in normalize_ws(page_text).lower()


def existing_lead_update(score: float) -> dict[str, Any]:
    """The ONLY payload Scout may apply to an EXISTING lead.

    Deliberately never contains status, owner_id, or priority: a re-run must
    not trample workflow state a junior set by hand (e.g. 'contacted').
    """
    return {"evidence_score": score}


def new_lead_row(brand_id: int, score: float) -> dict[str, Any]:
    """Full row for a lead Scout creates for the first time."""
    return {
        "brand_id": brand_id,
        "fest_target": FEST_TARGET,
        "evidence_score": score,
        "status": "new",
        "priority": priority_from_score(score),
        "is_demo": False,
    }


# ---------------------------------------------------------------------------
# Polite fetching (robots.txt + per-host delay)
# ---------------------------------------------------------------------------
class PoliteFetcher:
    """httpx wrapper that respects robots.txt and rate-limits per host."""

    def __init__(self) -> None:
        self._client = httpx.Client(
            headers={"User-Agent": USER_AGENT}, timeout=20, follow_redirects=True
        )
        self._robots: dict[str, RobotFileParser | None] = {}
        self._last_hit: dict[str, float] = {}

    def _allowed(self, url: str) -> bool:
        host = urlparse(url).netloc
        if host not in self._robots:
            parser = RobotFileParser()
            try:
                response = self._client.get(f"https://{host}/robots.txt")
                parser.parse(response.text.splitlines() if response.status_code == 200 else [])
            except Exception:  # noqa: BLE001 — unreachable robots => allow, log
                logger.info("robots.txt unreachable for %s; proceeding politely.", host)
                parser = None
            self._robots[host] = parser
        parser = self._robots[host]
        return True if parser is None else parser.can_fetch(USER_AGENT, url)

    def _wait_politely(self, url: str) -> None:
        host = urlparse(url).netloc
        elapsed = time.monotonic() - self._last_hit.get(host, 0.0)
        if elapsed < POLITENESS_DELAY_SECONDS:
            time.sleep(POLITENESS_DELAY_SECONDS - elapsed)
        self._last_hit[host] = time.monotonic()

    def get(self, url: str) -> str | None:
        """Fetch a URL's body, or None (logged) on any failure/disallow."""
        try:
            if not self._allowed(url):
                logger.warning("robots.txt disallows %s; skipping.", url)
                return None
            self._wait_politely(url)
            response = self._client.get(url)
            response.raise_for_status()
            return response.text
        except Exception as exc:  # noqa: BLE001 — broken site must never crash the run
            logger.warning("Fetch failed for %s: %s", url, exc)
            return None


def page_to_text(html: str) -> str:
    """Visible text of a page, whitespace-collapsed and capped."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return normalize_ws(soup.get_text(" "))[:PAGE_TEXT_CAP]


def find_sponsor_links(html: str, base_url: str) -> list[str]:
    """Same-domain links whose href/text mention sponsors or partners."""
    soup = BeautifulSoup(html, "html.parser")
    base_host = urlparse(base_url).netloc
    found: list[str] = []
    for anchor in soup.find_all("a", href=True):
        blob = (anchor["href"] + " " + anchor.get_text(" ")).lower()
        if "sponsor" in blob or "partner" in blob:
            absolute = urljoin(base_url, anchor["href"]).split("#")[0]
            if urlparse(absolute).netloc == base_host and absolute not in found and absolute != base_url:
                found.append(absolute)
    return found[:MAX_SPONSOR_LINKS_PER_SEED]


def fetch_google_news(fetcher: PoliteFetcher, query: str) -> list[dict[str, str]]:
    """Top articles from Google News RSS (free, keyless) for a query."""
    url = ("https://news.google.com/rss/search?q="
           + httpx.QueryParams({"q": query})["q"].replace(" ", "+")
           + "&hl=en-IN&gl=IN&ceid=IN:en")
    body = fetcher.get(url)
    if not body:
        return []
    articles: list[dict[str, str]] = []
    try:
        for item in ET.fromstring(body).iter("item"):
            title = normalize_ws(item.findtext("title") or "")
            link = (item.findtext("link") or "").strip()
            description = BeautifulSoup(item.findtext("description") or "", "html.parser").get_text(" ")
            if title and link:
                articles.append(
                    {"title": title, "url": link, "text": normalize_ws(f"{title} {description}")[:600]}
                )
            if len(articles) >= NEWS_ARTICLES_PER_QUERY:
                break
    except ET.ParseError as exc:
        logger.warning("News RSS parse failed for %r: %s", query, exc)
    return articles


# ---------------------------------------------------------------------------
# Database writes (service-role client; RLS bypassed by design in jobs/)
# ---------------------------------------------------------------------------
def get_service_client():  # noqa: ANN201 — supabase Client
    load_dotenv()
    from supabase import create_client

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        logger.error("SUPABASE_URL / SUPABASE_SERVICE_KEY missing (see .env.example).")
        sys.exit(1)
    return create_client(url, key)


def get_or_create_brand(client: Any, display_name: str, normalized: str) -> tuple[int, bool]:
    """Return (brand_id, created). Never overwrites an existing brand's fields."""
    existing = client.table("brands").select("id").eq("normalized_name", normalized).execute().data
    if existing:
        return existing[0]["id"], False
    row = (
        client.table("brands")
        .insert({"name": display_name.strip(), "normalized_name": normalized, "is_demo": False})
        .execute()
        .data
    )
    return row[0]["id"], True


def upsert_evidence(client: Any, brand_id: int, source_url: str, source_type: str,
                    snippet: str, confidence: float, region_match: bool) -> None:
    client.table("evidence").upsert(
        {
            "brand_id": brand_id,
            "source_url": source_url[:2000],
            "source_type": source_type,
            "snippet": snippet[:300],
            "confidence": round(confidence, 2),
            "region_match": region_match,
            "detected_at": datetime.now(timezone.utc).isoformat(),
        },
        on_conflict="brand_id,source_url",
    ).execute()


def rescore_lead(client: Any, brand_id: int) -> bool:
    """Recompute the brand's Evidence Score; create or score-update its lead."""
    evidence = client.table("evidence").select("*").eq("brand_id", brand_id).execute().data or []
    score = compute_evidence_score(evidence)
    existing = (
        client.table("leads").select("id")
        .eq("brand_id", brand_id).eq("fest_target", FEST_TARGET)
        .execute().data
    )
    if existing:
        client.table("leads").update(existing_lead_update(score)).eq("id", existing[0]["id"]).execute()
        return False
    client.table("leads").insert(new_lead_row(brand_id, score)).execute()
    return True


# ---------------------------------------------------------------------------
# Seeds
# ---------------------------------------------------------------------------
def load_seeds(client: Any) -> list[dict[str, Any]]:
    """Enabled seeds from scout_seeds; bootstrap the table from YAML if empty."""
    rows = client.table("scout_seeds").select("*").execute().data or []
    if not rows and SEEDS_YAML.exists():
        config = yaml.safe_load(SEEDS_YAML.read_text(encoding="utf-8")) or {}
        for fest in config.get("fests") or []:
            client.table("scout_seeds").upsert(
                {
                    "name": fest["name"],
                    "url": fest["url"],
                    "region_match": bool(fest.get("region_match", False)),
                    "enabled": bool(fest.get("enabled", True)),
                    "notes": fest.get("notes"),
                },
                on_conflict="url",
            ).execute()
        rows = client.table("scout_seeds").select("*").execute().data or []
        logger.info("Bootstrapped %d seeds from rival_fests.yaml.", len(rows))
    return [row for row in rows if row.get("enabled")]


# ---------------------------------------------------------------------------
# Extraction passes
# ---------------------------------------------------------------------------
@dataclass
class RunStats:
    pages_ok: int = 0
    pages_failed: int = 0
    news_queries: int = 0
    brands_new: int = 0
    evidence_written: int = 0
    leads_new: int = 0
    leads_rescored: int = 0
    llm_skips: int = 0
    rejected_not_on_page: int = 0
    touched_brand_ids: set[int] = field(default_factory=set)

    def as_dict(self) -> dict[str, int]:
        return {key: value for key, value in self.__dict__.items() if isinstance(value, int)}


PAGE_PROMPT = """From this college-fest webpage text, extract commercial brands that appear to be SPONSORS or PARTNERS of the fest.
Rules: exclude the fest itself, the host college/university, student clubs, and government bodies. For each brand give a verbatim snippet (a short quote from the text where it appears) and a confidence 0-1.
Schema: {{"brands": [{{"brand_name": str, "snippet": str, "confidence": float}}]}}
Fest: {fest_name}
Page text:
{page_text}"""

NEWS_PROMPT = """These are news search results about college fest sponsorships in India. Extract commercial brands mentioned as sponsoring or partnering with a college fest. Use article_index to say which numbered article mentions the brand.
Schema: {{"brands": [{{"brand_name": str, "article_index": int, "confidence": float}}]}}
Articles:
{articles}"""


def process_seed_page(client: Any, stats: RunStats, seed: dict[str, Any],
                      url: str, page_text: str) -> None:
    """LLM-extract brands from one fetched page and persist verified evidence."""
    result = extract_json(PAGE_PROMPT.format(fest_name=seed["name"], page_text=page_text), PageExtraction)
    if result is None:
        stats.llm_skips += 1
        return
    fest_norm = normalize_brand_name(seed["name"])
    for brand in result.brands:
        normalized = normalize_brand_name(brand.brand_name)
        if len(normalized) < 2 or normalized == fest_norm:
            continue
        if not brand_on_page(brand.brand_name, page_text):
            stats.rejected_not_on_page += 1
            logger.info("Rejected %r — not found verbatim on %s", brand.brand_name, url)
            continue
        brand_id, created = get_or_create_brand(client, brand.brand_name, normalized)
        stats.brands_new += int(created)
        upsert_evidence(client, brand_id, url, "rival_fest_site",
                        brand.snippet, brand.confidence, bool(seed.get("region_match")))
        stats.evidence_written += 1
        stats.touched_brand_ids.add(brand_id)


def process_news_query(client: Any, stats: RunStats, fetcher: PoliteFetcher,
                       query: str, region_match: bool) -> None:
    articles = fetch_google_news(fetcher, query)
    stats.news_queries += 1
    if not articles:
        return
    numbered = "\n".join(f"[{index}] {article['text']}" for index, article in enumerate(articles))
    result = extract_json(NEWS_PROMPT.format(articles=numbered), NewsExtraction)
    if result is None:
        stats.llm_skips += 1
        return
    for brand in result.brands:
        if brand.article_index >= len(articles):
            continue
        article = articles[brand.article_index]
        normalized = normalize_brand_name(brand.brand_name)
        if len(normalized) < 2 or not brand_on_page(brand.brand_name, article["text"]):
            stats.rejected_not_on_page += 1
            continue
        brand_id, created = get_or_create_brand(client, brand.brand_name, normalized)
        stats.brands_new += int(created)
        upsert_evidence(client, brand_id, article["url"], "news",
                        article["title"], brand.confidence, region_match)
        stats.evidence_written += 1
        stats.touched_brand_ids.add(brand_id)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def main() -> int:
    log_buffer = io.StringIO()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout), logging.StreamHandler(log_buffer)],
    )
    client = get_service_client()
    run_id = client.table("scout_runs").insert({"status": "running"}).execute().data[0]["id"]
    stats = RunStats()
    status = "success"

    try:
        fetcher = PoliteFetcher()
        seeds = load_seeds(client)
        logger.info("Scout starting: %d enabled seeds, fest_target=%r", len(seeds), FEST_TARGET)

        for seed in seeds:
            html = fetcher.get(seed["url"])
            if html is None:
                stats.pages_failed += 1
                continue
            stats.pages_ok += 1
            process_seed_page(client, stats, seed, seed["url"], page_to_text(html))
            for link in find_sponsor_links(html, seed["url"]):
                sub_html = fetcher.get(link)
                if sub_html is None:
                    stats.pages_failed += 1
                    continue
                stats.pages_ok += 1
                process_seed_page(client, stats, seed, link, page_to_text(sub_html))

        for query, region in NEWS_QUERIES:
            process_news_query(client, stats, fetcher, query, region)

        for brand_id in stats.touched_brand_ids:
            if rescore_lead(client, brand_id):
                stats.leads_new += 1
            else:
                stats.leads_rescored += 1

        if stats.pages_failed or stats.llm_skips:
            status = "partial"
    except LLMUnavailableError as exc:
        logger.error("All LLM providers unavailable — checkpointing: %s", exc)
        status = "partial"
    except Exception:  # noqa: BLE001 — record, mark failed, surface in Actions
        logger.exception("Scout run failed unexpectedly.")
        status = "failed"

    client.table("scout_runs").update(
        {
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "stats": stats.as_dict(),
            "log": log_buffer.getvalue()[-8000:],
        }
    ).eq("id", run_id).execute()
    logger.info("Scout finished: status=%s stats=%s", status, stats.as_dict())
    return 1 if status == "failed" else 0


if __name__ == "__main__":
    sys.exit(main())
