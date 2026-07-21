"""Trustpilot scraper.

Primary: official Consumer API (free key at developers.trustpilot.com) — structured, reliable.
Fallback: Playwright web scraper — works without a key, less structured.
"""

import asyncio
import re
from datetime import datetime

import httpx
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

from src.scrapers._stealth import apply_stealth

SORT_MAP_API = {
    "recent": "createdat.desc",
    "helpful": "createdat.desc",
    "highest": "stars.desc",
    "lowest": "stars.asc",
}

TP_API = "https://api.trustpilot.com/v1"


def _derive_domain(company: str) -> str:
    slug = company.lower().strip()
    if "." in slug:
        return slug
    slug = re.sub(r"[^a-z0-9-]", "", slug.replace(" ", ""))
    return f"{slug}.com"


def _parse_web_reviews(html: str, company: str, product_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    records = []

    for card in soup.select("[data-service-review-card-paper], article[class*='styles_reviewCard']"):
        rating = None
        star_el = card.select_one("[class*='star-rating'] img, [data-service-review-rating]")
        if star_el:
            src = star_el.get("src", "") or star_el.get("data-service-review-rating", "")
            m = re.search(r"(\d)", src)
            if m:
                rating = float(m.group(1))

        title_el = card.select_one("h2[data-service-review-title-typography], [class*='title']")
        title = title_el.get_text(strip=True) if title_el else None

        body_el = card.select_one("[data-service-review-text-typography], [class*='reviewContent'] p")
        body = body_el.get_text(strip=True) if body_el else None

        name_el = card.select_one("[class*='consumerName'], [data-consumer-name-typography]")
        reviewer_name = name_el.get_text(strip=True) if name_el else None

        date_el = card.select_one("time")
        date_str = date_el.get("datetime", "") if date_el else ""
        try:
            date = datetime.fromisoformat(date_str.replace("Z", "+00:00")).date().isoformat()
        except Exception:
            date = date_str or None

        verified = bool(card.select_one("[class*='verified'], [data-service-review-verified]"))

        review_link = card.select_one("a[href*='/reviews/']")
        review_url = None
        if review_link:
            href = review_link.get("href", "")
            review_url = "https://www.trustpilot.com" + href if href.startswith("/") else href

        if not (title or body):
            continue

        records.append({
            "company": company,
            "platform": "trustpilot",
            "reviewer_name": reviewer_name,
            "reviewer_title": None,
            "reviewer_company_size": None,
            "rating": rating,
            "title": title,
            "body": body,
            "pros": None,
            "cons": None,
            "date": date,
            "verified": verified,
            "helpful_count": None,
            "review_url": review_url or product_url,
            "product_url": product_url,
        })

    return records


async def _scrape_web(
    company: str,
    max_reviews: int,
    sort_by: str,
    min_rating: int | None,
    proxy_url: str | None,
) -> list[dict]:
    domain = _derive_domain(company)
    sort_param = {"recent": "recency", "highest": "stars_desc", "lowest": "stars_asc"}.get(sort_by, "recency")
    product_url = f"https://www.trustpilot.com/review/{domain}"
    records: list[dict] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        context_opts: dict = {
            "user_agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "extra_http_headers": {"Accept-Language": "en-US,en;q=0.9"},
            "viewport": {"width": 1440, "height": 900},
        }
        if proxy_url:
            context_opts["proxy"] = {"server": proxy_url}
        context = await browser.new_context(**context_opts)
        page = await context.new_page()
        await apply_stealth(page)

        page_num = 1
        while len(records) < max_reviews:
            url = f"{product_url}?sort={sort_param}&page={page_num}"
            if min_rating:
                url += f"&stars={min_rating}"

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            except Exception:
                break

            await asyncio.sleep(3)
            html = await page.content()
            page_records = _parse_web_reviews(html, company, product_url)

            if not page_records:
                break

            records.extend(page_records)
            page_num += 1
            await asyncio.sleep(1.5)

        await browser.close()

    return records[:max_reviews]


async def _scrape_api(
    company: str,
    max_reviews: int,
    sort_by: str,
    min_rating: int | None,
    api_key: str,
) -> list[dict]:
    domain = _derive_domain(company)
    order_by = SORT_MAP_API.get(sort_by, "createdat.desc")
    records: list[dict] = []

    async with httpx.AsyncClient(base_url=TP_API, follow_redirects=True, timeout=30) as client:
        find_resp = await client.get("/business-units/find", params={"name": domain, "apikey": api_key})
        if find_resp.status_code != 200:
            return []
        biz_id = find_resp.json().get("id")
        if not biz_id:
            return []

        product_url = f"https://www.trustpilot.com/review/{domain}"
        page = 1
        per_page = min(20, max_reviews)

        while len(records) < max_reviews:
            params: dict = {"apikey": api_key, "perPage": per_page, "page": page, "orderBy": order_by}
            if min_rating:
                params["stars"] = min_rating

            resp = await client.get(f"/business-units/{biz_id}/reviews", params=params)
            if resp.status_code != 200:
                break

            page_reviews = resp.json().get("reviews") or []
            if not page_reviews:
                break

            for r in page_reviews:
                date_str = r.get("createdAt") or ""
                try:
                    date = datetime.fromisoformat(date_str.replace("Z", "+00:00")).date().isoformat()
                except Exception:
                    date = date_str or None

                consumer = r.get("consumer") or {}
                rating_val = r.get("stars")
                records.append({
                    "company": company,
                    "platform": "trustpilot",
                    "reviewer_name": consumer.get("displayName"),
                    "reviewer_title": None,
                    "reviewer_company_size": None,
                    "rating": float(rating_val) if rating_val else None,
                    "title": r.get("title"),
                    "body": r.get("text"),
                    "pros": None,
                    "cons": None,
                    "date": date,
                    "verified": bool((r.get("labels") or {}).get("verification")),
                    "helpful_count": None,
                    "review_url": f"https://www.trustpilot.com/reviews/{r.get('id', '')}",
                    "product_url": f"https://www.trustpilot.com/review/{domain}",
                })

            if len(page_reviews) < per_page:
                break
            page += 1

    return records[:max_reviews]


async def scrape(
    company: str,
    max_reviews: int = 50,
    sort_by: str = "recent",
    min_rating: int | None = None,
    proxy_url: str | None = None,
    api_key: str | None = None,
    **_kwargs,
) -> list[dict]:
    if api_key:
        return await _scrape_api(company, max_reviews, sort_by, min_rating, api_key)
    return await _scrape_web(company, max_reviews, sort_by, min_rating, proxy_url)
