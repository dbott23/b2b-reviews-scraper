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

    proxy_dict = {"http://": proxy_url, "https://": proxy_url} if proxy_url else None

    async with httpx.AsyncClient(
        headers=HEADERS,
        follow_redirects=True,
        timeout=30,
        proxies=proxy_dict,
    ) as client:
        while len(records) < max_reviews:
            params: dict = {"sort": tp_sort, "page": page_num}
            if min_rating:
                params["stars"] = min_rating

            try:
                resp = await client.get(product_url, params=params)
            except Exception:
                break

            if resp.status_code == 404:
                break
            if resp.status_code != 200:
                await asyncio.sleep(2)
                break

            page_records = _parse_reviews(resp.text, company, product_url)
            if not page_records:
                break

            records.extend(page_records)
            page_num += 1
            await asyncio.sleep(1)

    return records[:max_reviews]
