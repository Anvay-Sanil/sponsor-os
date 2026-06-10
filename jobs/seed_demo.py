"""Seed demo data so the UI is never blank, plus bootstrap invite codes.

Run locally ONCE after applying supabase/schema.sql:

    python jobs/seed_demo.py

Uses the SERVICE-ROLE key from .env (bypasses RLS) — this script is the
chicken-and-egg solver: it prints the first admin invite code to the console.

Idempotent: brands/leads/evidence/tiers upsert on their unique keys; invite
codes are only created for a role if no unused, unexpired ACM- code exists.

Everything seeded here is marked is_demo=true. Purge before production:
    delete from leads where is_demo; delete from brands where is_demo;
"""
from __future__ import annotations

import logging
import os
import re
import secrets
import string
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

from dotenv import load_dotenv
from supabase import Client, create_client

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("seed_demo")

ROLES = ("admin", "sponsorship", "analyst", "viewer")
FEST = "Demo Fest 2026"

# name, industry, website, evidence_score, status, priority
DEMO_BRANDS: list[tuple[str, str, str, float, str, str]] = [
    ("boAt", "Consumer Electronics", "https://www.boat-lifestyle.com", 92, "new", "high"),
    ("Red Bull", "Beverages", "https://www.redbull.com", 88, "contacted", "high"),
    ("Unstop", "EdTech / Community", "https://unstop.com", 84, "replied", "high"),
    ("Coding Ninjas", "EdTech", "https://www.codingninjas.com", 76, "new", "medium"),
    ("Devfolio", "Developer Platform", "https://devfolio.co", 71, "meeting", "medium"),
    ("GeeksforGeeks", "EdTech", "https://www.geeksforgeeks.org", 65, "new", "medium"),
    ("Zebronics", "Consumer Electronics", "https://zebronics.com", 58, "contacted", "medium"),
    ("Swiggy", "Food Delivery", "https://www.swiggy.com", 51, "ghosted", "low"),
    ("Chai Point", "Food & Beverage", "https://chaipoint.com", 44, "new", "low"),
    ("Polygon", "Web3", "https://polygon.technology", 37, "rejected", "low"),
]

DEMO_TIERS: list[dict[str, Any]] = [
    {"name": "Title", "base_price": 75000,
     "components_json": {"stage_logo": True, "reels": 4, "booth": True, "banner": 6, "shoutouts": 8}},
    {"name": "Gold", "base_price": 50000,
     "components_json": {"stage_logo": True, "reels": 2, "booth": True, "banner": 3, "shoutouts": 4}},
    {"name": "Silver", "base_price": 25000,
     "components_json": {"stage_logo": False, "reels": 1, "booth": True, "banner": 2, "shoutouts": 2}},
    {"name": "Community", "base_price": 10000,
     "components_json": {"stage_logo": False, "reels": 1, "booth": False, "banner": 1, "shoutouts": 1}},
]


def normalize_brand_name(name: str) -> str:
    """Lowercase, alphanumeric-only key used for dedup (mirrors Phase 2 Scout)."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def get_service_client() -> Client:
    """Service-role Supabase client. Exits with a clear message if not configured."""
    load_dotenv()
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        logger.error("Set SUPABASE_URL and SUPABASE_SERVICE_KEY in .env first (see .env.example).")
        sys.exit(1)
    return create_client(url, key)


def seed_brands_and_leads(client: Client) -> int:
    """Upsert demo brands, two evidence rows each, and one lead per brand."""
    seeded = 0
    for name, industry, website, score, status, priority in DEMO_BRANDS:
        slug = normalize_brand_name(name)
        brand_rows = (
            client.table("brands")
            .upsert(
                {
                    "name": name,
                    "normalized_name": slug,
                    "website": website,
                    "industry": industry,
                    "region": "India",
                    "is_demo": True,
                },
                on_conflict="normalized_name",
            )
            .execute()
            .data
        )
        brand_id = brand_rows[0]["id"]

        client.table("evidence").upsert(
            [
                {
                    "brand_id": brand_id,
                    "source_url": f"https://example.com/demo/{slug}/rival-fest-sponsors",
                    "source_type": "rival_fest_site",
                    "snippet": f"[DEMO DATA] {name} listed as a sponsor on a rival fest's website.",
                    "confidence": 0.9,
                },
                {
                    "brand_id": brand_id,
                    "source_url": f"https://example.com/demo/{slug}/news-coverage",
                    "source_type": "news",
                    "snippet": f"[DEMO DATA] News article mentions {name} backing student tech events.",
                    "confidence": 0.7,
                },
            ],
            on_conflict="brand_id,source_url",
        ).execute()

        client.table("leads").upsert(
            {
                "brand_id": brand_id,
                "fest_target": FEST,
                "evidence_score": score,
                "status": status,
                "priority": priority,
                "is_demo": True,
            },
            on_conflict="brand_id,fest_target",
        ).execute()
        seeded += 1
    return seeded


def seed_tiers(client: Client) -> int:
    """Upsert the four starter sponsorship tiers."""
    client.table("tiers").upsert(DEMO_TIERS, on_conflict="name").execute()
    return len(DEMO_TIERS)


def ensure_invite_codes(client: Client) -> dict[str, str]:
    """Return one usable invite code per role, creating codes only when missing."""
    now = datetime.now(timezone.utc)
    existing = client.table("invite_codes").select("*").like("code", "ACM-%").execute().data or []
    codes: dict[str, str] = {}
    for row in existing:
        expires = row.get("expires_at")
        alive = expires is None or datetime.fromisoformat(expires.replace("Z", "+00:00")) > now
        if row["uses"] < row["max_uses"] and alive and row["role"] not in codes:
            codes[row["role"]] = row["code"]

    alphabet = string.ascii_uppercase + string.digits
    for role in ROLES:
        if role in codes:
            continue
        code = "ACM-" + "".join(secrets.choice(alphabet) for _ in range(6))
        client.table("invite_codes").insert(
            {
                "code": code,
                "role": role,
                "max_uses": 1 if role == "admin" else 5,
                "expires_at": (now + timedelta(days=30)).isoformat(),
            }
        ).execute()
        codes[role] = code
    return codes


def main() -> None:
    """Seed everything and print the invite codes."""
    client = get_service_client()
    brand_count = seed_brands_and_leads(client)
    tier_count = seed_tiers(client)
    codes = ensure_invite_codes(client)

    logger.info("Seeded %d demo brands/leads and %d tiers (all is_demo=true).", brand_count, tier_count)
    logger.info("Invite codes (valid 30 days — share carefully):")
    for role in ROLES:
        logger.info("  %-12s %s", role, codes[role])
    logger.info("Sign up at the app's Home page with the admin code first.")


if __name__ == "__main__":
    main()
