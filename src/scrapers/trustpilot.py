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


async def _find_slug(page, company: str) -> str | None:
    """Search Trustpilot and return the company slug (e.g. 'asana.com')."""
    await page.goto(
        f"https://www.trustpilot.com/search?query={company}",
        wait_until="domcontentloaded",
        timeout=30000,
    )
    # Wait for search results to appear
    try:
        await page.wait_for_selector("a[href^='/review/']", timeout=8000)
    except Exception:
        pass
    link = await page.query_selector("a[href^='/review/']")
    if not link:
        return None
    href = await link.get_attribute("href")
    return href.split("/review/")[-1].rstrip("/") if href else None


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
) -> list[dict]:
    records: list[dict] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=USER_AGENT,
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        page = await context.new_page()

        slug = await _find_slug(page, company)
        if not slug:
            await browser.close()
            return []

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
                timeout=30000,
            )
            # Wait briefly for JS to hydrate JSON-LD
            await asyncio.sleep(1)

            html = await page.content()
            page_records = _parse_reviews(html, company, product_url)

            if not page_records:
                break

            records.extend(page_records)
            page_num += 1
            await asyncio.sleep(1)

        await browser.close()

    return records[:max_reviews]
