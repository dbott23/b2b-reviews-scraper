"""Trustpilot scraper — uses plain HTTP to extract server-rendered JSON-LD data."""

import asyncio
import json
import re
from datetime import datetime

import httpx
from bs4 import BeautifulSoup

SORT_MAP = {
    "recent": "recency",
    "helpful": "relevance",
    "highest": "5_stars_first",
    "lowest": "1_stars_first",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
}


def _derive_slug(company: str) -> str:
    """Derive a likely Trustpilot slug from the company name.

    Trustpilot slugs match the company's primary domain, e.g.
    'Asana' → 'asana.com', 'monday.com' → 'monday.com'.
    """
    slug = company.lower().strip()
    if "." in slug:
        return slug
    slug = re.sub(r"[^a-z0-9-]", "", slug)
    return f"{slug}.com"


def _parse_reviews(html: str, company: str, product_url: str) -> list[dict]:
    """Extract reviews from JSON-LD embedded in the Trustpilot page."""
    soup = BeautifulSoup(html, "html.parser")
    records = []

    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        reviews = data.get("review") or []
        if not reviews and data.get("@type") == "Review":
            reviews = [data]
        for r in reviews:
            if not isinstance(r, dict):
                continue
            try:
                rating = float(r.get("reviewRating", {}).get("ratingValue", 0) or 0)
            except (TypeError, ValueError):
                rating = None

            author = r.get("author", {})
            date_str = r.get("datePublished", "")
            try:
                date = datetime.fromisoformat(date_str.replace("Z", "+00:00")).date().isoformat()
            except Exception:
                date = date_str or None

            records.append({
                "company": company,
                "platform": "trustpilot",
                "reviewer_name": author.get("name"),
                "reviewer_title": None,
                "reviewer_company_size": None,
                "rating": rating if rating else None,
                "title": r.get("name"),
                "body": r.get("reviewBody"),
                "pros": None,
                "cons": None,
                "date": date,
                "verified": True,
                "helpful_count": None,
                "review_url": r.get("url") or product_url,
                "product_url": product_url,
            })

    return records


def _parse_next_data(html: str, company: str, product_url: str) -> list[dict]:
    """Fallback: extract reviews from Trustpilot's embedded __NEXT_DATA__ JSON."""
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []

    # Traverse to reviews list — path varies by page version
    reviews_raw = []
    try:
        page_props = data["props"]["pageProps"]
        reviews_raw = page_props.get("reviews") or page_props.get("reviewsList") or []
    except (KeyError, TypeError):
        pass

    records = []
    for r in reviews_raw:
        if not isinstance(r, dict):
            continue
        rating_val = None
        try:
            rating_val = float(r.get("rating", {}).get("stars") or r.get("stars") or 0) or None
        except (TypeError, ValueError):
            pass

        date_str = r.get("dates", {}).get("publishedDate") or r.get("date") or ""
        try:
            date = datetime.fromisoformat(date_str.replace("Z", "+00:00")).date().isoformat()
        except Exception:
            date = date_str or None

        consumer = r.get("consumer") or {}
        records.append({
            "company": company,
            "platform": "trustpilot",
            "reviewer_name": consumer.get("displayName"),
            "reviewer_title": None,
            "reviewer_company_size": None,
            "rating": rating_val,
            "title": r.get("title"),
            "body": r.get("text"),
            "pros": None,
            "cons": None,
            "date": date,
            "verified": bool(r.get("labels", {}).get("verification")),
            "helpful_count": None,
            "review_url": product_url,
            "product_url": product_url,
        })
    return records


async def scrape(
    company: str,
    max_reviews: int = 50,
    sort_by: str = "recent",
    min_rating: int | None = None,
    proxy_url: str | None = None,
) -> list[dict]:
    slug = _derive_slug(company)
    product_url = f"https://www.trustpilot.com/review/{slug}"
    tp_sort = SORT_MAP.get(sort_by, "recency")
    records: list[dict] = []
    page_num = 1

    async with httpx.AsyncClient(
        headers=HEADERS,
        follow_redirects=True,
        timeout=30,
        proxy=proxy_url,
    ) as client:
        while len(records) < max_reviews:
            params: dict = {"sort": tp_sort, "page": page_num}
            if min_rating:
                params["stars"] = min_rating

            try:
                resp = await client.get(product_url, params=params)
            except Exception as exc:
                print(f"[trustpilot] request failed: {exc}", flush=True)
                break

            print(f"[trustpilot] status={resp.status_code} len={len(resp.text)} url={resp.url}", flush=True)
            if resp.status_code == 404:
                break
            if resp.status_code != 200:
                print(f"[trustpilot] non-200 snippet: {resp.text[:300]}", flush=True)
                await asyncio.sleep(2)
                break

            # Check for bot-challenge pages
            if "Verifying" in resp.text[:500] or "challenge" in resp.text[:500].lower():
                print(f"[trustpilot] bot challenge detected: {resp.text[:300]}", flush=True)
                break

            ld_count = resp.text.count('"application/ld+json"')
            print(f"[trustpilot] JSON-LD blocks found: {ld_count}", flush=True)

            page_records = _parse_reviews(resp.text, company, product_url)
            print(f"[trustpilot] parsed {len(page_records)} reviews from page {page_num}", flush=True)
            if not page_records:
                # Try parsing __NEXT_DATA__ as fallback
                page_records = _parse_next_data(resp.text, company, product_url)
                print(f"[trustpilot] __NEXT_DATA__ fallback: {len(page_records)} reviews", flush=True)
            if not page_records:
                break

            records.extend(page_records)
            page_num += 1
            await asyncio.sleep(1)

    return records[:max_reviews]
