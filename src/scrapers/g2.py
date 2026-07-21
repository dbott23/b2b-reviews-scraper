"""G2 scraper — uses Playwright; G2 is JS-heavy with anti-bot measures."""

import asyncio
import re
from datetime import datetime

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

SORT_MAP = {
    "recent": "most_recent",
    "helpful": "most_helpful",
    "highest": "highest_star",
    "lowest": "lowest_star",
}


async def _find_product_slug(page, company: str) -> str | None:
    await page.goto(
        f"https://www.g2.com/search?query={company}",
        wait_until="domcontentloaded",
        timeout=30000,
    )
    # Product cards link to /products/<slug>/reviews
    link = await page.query_selector("a[href*='/products/'][href*='/reviews']")
    if not link:
        # Try broader match
        link = await page.query_selector("a[href*='/products/']")
    if not link:
        return None
    href = await link.get_attribute("href")
    m = re.search(r"/products/([^/]+)", href or "")
    return m.group(1) if m else None


def _parse_reviews(html: str, company: str, product_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    records = []

    for card in soup.select("[itemprop='review'], .paper.paper--white.paper--shadow"):
        # Rating
        rating = None
        stars_el = card.select_one("[class*='stars'], meta[itemprop='ratingValue']")
        if stars_el:
            val = stars_el.get("content") or stars_el.get("data-rating") or ""
            m = re.search(r"(\d[\d.]*)", val)
            if not m:
                # Count filled stars
                filled = len(card.select(".star.star--filled, .fa-star"))
                if filled:
                    rating = float(filled)
            else:
                try:
                    rating = float(m.group(1))
                except ValueError:
                    pass

        # Title — prefer meta content attribute over text nodes
        title_el = card.select_one("[itemprop='name']")
        if title_el:
            title = title_el.get("content") or title_el.get_text(strip=True) or None
        else:
            h3 = card.select_one("h3")
            title = h3.get_text(strip=True) if h3 else None

        # Pros / cons — G2 splits into "What do you like best?" / "What do you dislike?"
        pros = cons = body = None
        for section in card.select("[data-field-name]"):
            field = section.get("data-field-name", "")
            text = section.get_text(strip=True)
            if "like_best" in field or "pros" in field:
                pros = text
            elif "dislike" in field or "cons" in field:
                cons = text
        # Fallback body
        body_el = card.select_one("[itemprop='reviewBody']")
        if body_el:
            body = body_el.get_text(strip=True)

        # Reviewer
        reviewer_el = card.select_one("[itemprop='author'], .link--header-color")
        reviewer_name = reviewer_el.get_text(strip=True) if reviewer_el else None
        title_el2 = card.select_one(".mt-4th")
        reviewer_title = title_el2.get_text(strip=True) if title_el2 else None

        # Company size
        size_el = card.select_one("[class*='company-size'], [data-company-size]")
        reviewer_company_size = (
            size_el.get_text(strip=True) if size_el else None
        )

        # Date
        date_el = card.select_one("time, [itemprop='datePublished']")
        date_str = ""
        if date_el:
            date_str = date_el.get("content") or date_el.get("datetime") or date_el.get_text(strip=True)
        try:
            date = datetime.fromisoformat(date_str.replace("Z", "+00:00")).date().isoformat()
        except Exception:
            date = date_str or None

        # Helpful count
        helpful = None
        helpful_el = card.select_one("[class*='helpful-count']")
        if helpful_el:
            m = re.search(r"\d+", helpful_el.get_text())
            if m:
                helpful = int(m.group())

        # Review URL
        review_link = card.select_one("a[href*='/reviews/']")
        review_url = None
        if review_link:
            href = review_link.get("href", "")
            review_url = "https://www.g2.com" + href if href.startswith("/") else href

        if not (title or pros or body):
            continue

        records.append({
            "company": company,
            "platform": "g2",
            "reviewer_name": reviewer_name,
            "reviewer_title": reviewer_title,
            "reviewer_company_size": reviewer_company_size,
            "rating": rating,
            "title": title,
            "body": body or (f"{pros or ''} {cons or ''}".strip()) or None,
            "pros": pros,
            "cons": cons,
            "date": date,
            "verified": True,
            "helpful_count": helpful,
            "review_url": review_url or product_url,
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
            "user_agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            "extra_http_headers": {"Accept-Language": "en-US,en;q=0.9"},
        }
        if proxy_url:
            context_opts["proxy"] = {"server": proxy_url}
        context = await browser.new_context(**context_opts)
        page = await context.new_page()

        slug = await _find_product_slug(page, company)
        if not slug:
            await browser.close()
            return []

        product_url = f"https://www.g2.com/products/{slug}/reviews"
        g2_sort = SORT_MAP.get(sort_by, "most_recent")
        page_num = 1

        while len(records) < max_reviews:
            url = f"{product_url}?sort={g2_sort}&page={page_num}"
            if min_rating:
                url += f"&filters[star_rating]={min_rating}"

            await page.goto(url, wait_until="networkidle", timeout=30000)
            # G2 lazy-loads — scroll to trigger reviews
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(1)

            html = await page.content()
            page_records = _parse_reviews(html, company, product_url)

            if not page_records:
                break

            records.extend(page_records)
            page_num += 1
            await asyncio.sleep(2)

        await browser.close()

    return records[:max_reviews]
