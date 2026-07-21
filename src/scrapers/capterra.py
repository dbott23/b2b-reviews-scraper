"""Capterra scraper — uses Playwright for JS-rendered review pages."""

import asyncio
import re
from datetime import datetime

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

SORT_MAP = {
    "recent": "most_recent",
    "helpful": "most_helpful",
    "highest": "highest_rating",
    "lowest": "lowest_rating",
}


async def _search_product_url(page, company: str) -> str | None:
    await page.goto(
        f"https://www.capterra.com/search/?query={company}",
        wait_until="domcontentloaded",
        timeout=60000,
    )
    # First product card link
    link = await page.query_selector("a[href*='/p/']")
    if not link:
        return None
    href = await link.get_attribute("href")
    if not href:
        return None
    return "https://www.capterra.com" + href if href.startswith("/") else href


def _parse_reviews(html: str, company: str, product_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    records = []

    for card in soup.select("[data-testid='review-card'], .review-card, article[class*='review']"):
        # Rating
        rating = None
        rating_el = card.select_one("[aria-label*='star'], [class*='rating']")
        if rating_el:
            m = re.search(r"(\d[\d.]*)", rating_el.get("aria-label", "") or rating_el.text)
            if m:
                try:
                    rating = float(m.group(1))
                except ValueError:
                    pass

        # Title
        title_el = card.select_one("h3, [class*='title'], [class*='headline']")
        title = title_el.get_text(strip=True) if title_el else None

        # Body / pros / cons
        body_el = card.select_one("[class*='body'], [class*='comment'], p")
        body = body_el.get_text(strip=True) if body_el else None
        pros = cons = None
        for label_el in card.select("[class*='pros'], [class*='cons']"):
            text = label_el.get_text(strip=True)
            label = label_el.get("class", [""])[0].lower()
            if "pros" in label:
                pros = text
            elif "cons" in label:
                cons = text

        # Reviewer
        reviewer_el = card.select_one("[class*='reviewer'], [class*='author']")
        reviewer_name = reviewer_el.get_text(strip=True) if reviewer_el else None

        # Date
        date_el = card.select_one("time, [class*='date']")
        date_str = ""
        if date_el:
            date_str = date_el.get("datetime") or date_el.get_text(strip=True)
        try:
            date = datetime.fromisoformat(date_str.replace("Z", "+00:00")).date().isoformat()
        except Exception:
            date = date_str or None

        # Review URL
        review_link = card.select_one("a[href*='/reviews/']")
        review_url = None
        if review_link:
            href = review_link.get("href", "")
            review_url = "https://www.capterra.com" + href if href.startswith("/") else href

        if not (title or body):
            continue

        records.append({
            "company": company,
            "platform": "capterra",
            "reviewer_name": reviewer_name,
            "reviewer_title": None,
            "reviewer_company_size": None,
            "rating": rating,
            "title": title,
            "body": body,
            "pros": pros,
            "cons": cons,
            "date": date,
            "verified": False,
            "helpful_count": None,
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
    **_kwargs,
) -> list[dict]:
    records: list[dict] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
        context_opts: dict = {
            "user_agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
        }
        if proxy_url:
            context_opts["proxy"] = {"server": proxy_url}
        context = await browser.new_context(**context_opts)
        page = await context.new_page()

        product_url = await _search_product_url(page, company)
        if not product_url:
            await browser.close()
            return []

        # Navigate to reviews tab
        reviews_url = product_url.rstrip("/") + "/reviews/"
        ct_sort = SORT_MAP.get(sort_by, "most_recent")
        page_num = 1

        while len(records) < max_reviews:
            url = f"{reviews_url}?sort={ct_sort}&page={page_num}"
            if min_rating:
                url += f"&rating={min_rating}"

            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            html = await page.content()
            page_records = _parse_reviews(html, company, product_url)

            if not page_records:
                break

            records.extend(page_records)
            page_num += 1
            await asyncio.sleep(1.5)

        await browser.close()

    return records[:max_reviews]
