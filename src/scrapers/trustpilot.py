"""Trustpilot scraper — uses public JSON-LD data embedded in pages."""

import asyncio
import json
import re
from datetime import datetime

import httpx
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

SORT_MAP = {
    "recent": "recency",
    "helpful": "relevance",
    "highest": "5_stars_first",
    "lowest": "1_stars_first",
}


async def search_company(client: httpx.AsyncClient, company: str) -> tuple[str, str] | None:
    """Return (slug, product_url) for the best-matching company on Trustpilot."""
    resp = await client.get(
        "https://www.trustpilot.com/search",
        params={"query": company},
        headers=HEADERS,
        follow_redirects=True,
    )
    soup = BeautifulSoup(resp.text, "html.parser")
    # First result card
    link = soup.select_one("a[href^='/review/']")
    if not link:
        return None
    slug = link["href"].split("/review/")[-1].rstrip("/")
    return slug, f"https://www.trustpilot.com/review/{slug}"


def _parse_reviews(html: str, company: str, product_url: str) -> list[dict]:
    """Extract reviews from a Trustpilot reviews page using JSON-LD."""
    soup = BeautifulSoup(html, "html.parser")
    records = []

    # Trustpilot embeds review data in <script type="application/ld+json">
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
            rating_val = r.get("reviewRating", {}).get("ratingValue")
            try:
                rating = float(rating_val)
            except (TypeError, ValueError):
                rating = None

            author = r.get("author", {})
            date_str = r.get("datePublished", "")
            try:
                date = datetime.fromisoformat(date_str.replace("Z", "+00:00")).date().isoformat()
            except Exception:
                date = date_str

            body = r.get("reviewBody", "")

            records.append({
                "company": company,
                "platform": "trustpilot",
                "reviewer_name": author.get("name"),
                "reviewer_title": None,
                "reviewer_company_size": None,
                "rating": rating,
                "title": r.get("name"),
                "body": body,
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
) -> list[dict]:
    async with httpx.AsyncClient(timeout=30) as client:
        result = await search_company(client, company)
        if not result:
            return []
        slug, product_url = result

        tp_sort = SORT_MAP.get(sort_by, "recency")
        records: list[dict] = []
        page = 1

        while len(records) < max_reviews:
            params: dict = {"sort": tp_sort, "page": page}
            if min_rating:
                params["stars"] = min_rating

            resp = await client.get(
                f"https://www.trustpilot.com/review/{slug}",
                params=params,
                headers=HEADERS,
                follow_redirects=True,
            )
            if resp.status_code != 200:
                break

            page_records = _parse_reviews(resp.text, company, product_url)
            if not page_records:
                break

            records.extend(page_records)
            page += 1
            await asyncio.sleep(1)

        return records[:max_reviews]
