"""Trustpilot scraper — uses Playwright + JSON-LD data embedded in pages."""

import asyncio
import json
import re
from datetime import datetime

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

SORT_MAP = {
    "recent": "recency",
    "helpful": "relevance",
    "highest": "5_stars_first",
    "lowest": "1_stars_first",
}

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def _derive_slug(company: str) -> str:
    """Derive a likely Trustpilot slug directly from the company name.

    Trustpilot slugs follow the pattern of the company's primary domain, e.g.
    'Asana' → 'asana.com', 'monday.com' → 'monday.com'.
    """
    slug = company.lower().strip()
    # Already looks like a domain
    if "." in slug:
        return slug
    # Strip common suffixes and append .com
    slug = re.sub(r"[^a-z0-9-]", "", slug)
    return f"{slug}.com"


def _parse_reviews(html: str, company: str, product_url: str) -> list[dict]:
    """Extract reviews from a Trustpilot page using embedded JSON-LD."""
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
    records: list[dict] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
        context_opts: dict = {
            "user_agent": USER_AGENT,
            "extra_http_headers": {"Accept-Language": "en-US,en;q=0.9"},
        }
        if proxy_url:
            context_opts["proxy"] = {"server": proxy_url}
        context = await browser.new_context(**context_opts)
        page = await context.new_page()

        slug = _derive_slug(company)
        product_url = f"https://www.trustpilot.com/review/{slug}"
        tp_sort = SORT_MAP.get(sort_by, "recency")
        page_num = 1

        while len(records) < max_reviews:
            params = f"sort={tp_sort}&page={page_num}"
            if min_rating:
                params += f"&stars={min_rating}"

            await page.goto(
                f"{product_url}?{params}",
                wait_until="domcontentloaded",
                timeout=60000,
            )
            await asyncio.sleep(2)

            html = await page.content()
            page_records = _parse_reviews(html, company, product_url)

            if not page_records:
                break

            records.extend(page_records)
            page_num += 1
            await asyncio.sleep(1)

        await browser.close()

    return records[:max_reviews]
