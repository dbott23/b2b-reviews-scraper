"""G2 scraper — uses Playwright with stealth patches.

Note: G2 uses DataDome bot detection. Results depend on whether the JS challenge
resolves successfully. Residential proxies improve success rate.
"""

import asyncio
import re
from datetime import datetime

from bs4 import BeautifulSoup
from camoufox.async_api import AsyncCamoufox

SORT_MAP = {
    "recent": "most_recent",
    "helpful": "most_helpful",
    "highest": "highest_star",
    "lowest": "lowest_star",
}


def _parse_reviews(html: str, company: str, product_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    records = []

    for card in soup.select("[itemprop='review'], .paper.paper--white.paper--shadow"):
        rating = None
        stars_el = card.select_one("[class*='stars'], meta[itemprop='ratingValue']")
        if stars_el:
            val = stars_el.get("content") or stars_el.get("data-rating") or ""
            m = re.search(r"(\d[\d.]*)", val)
            if not m:
                filled = len(card.select(".star.star--filled, .fa-star"))
                if filled:
                    rating = float(filled)
            else:
                try:
                    rating = float(m.group(1))
                except ValueError:
                    pass

        title_el = card.select_one("[itemprop='name']")
        if title_el:
            title = title_el.get("content") or title_el.get_text(strip=True) or None
        else:
            h3 = card.select_one("h3")
            title = h3.get_text(strip=True) if h3 else None

        pros = cons = body = None
        for section in card.select("[data-field-name]"):
            field = section.get("data-field-name", "")
            text = section.get_text(strip=True)
            if "like_best" in field or "pros" in field:
                pros = text
            elif "dislike" in field or "cons" in field:
                cons = text
        body_el = card.select_one("[itemprop='reviewBody']")
        if body_el:
            body = body_el.get_text(strip=True)

        reviewer_el = card.select_one("[itemprop='author'], .link--header-color")
        reviewer_name = reviewer_el.get_text(strip=True) if reviewer_el else None
        title_el2 = card.select_one(".mt-4th")
        reviewer_title = title_el2.get_text(strip=True) if title_el2 else None

        size_el = card.select_one("[class*='company-size'], [data-company-size]")
        reviewer_company_size = size_el.get_text(strip=True) if size_el else None

        date_el = card.select_one("time, [itemprop='datePublished']")
        date_str = ""
        if date_el:
            date_str = date_el.get("content") or date_el.get("datetime") or date_el.get_text(strip=True)
        try:
            date = datetime.fromisoformat(date_str.replace("Z", "+00:00")).date().isoformat()
        except Exception:
            date = date_str or None

        helpful = None
        helpful_el = card.select_one("[class*='helpful-count']")
        if helpful_el:
            m = re.search(r"\d+", helpful_el.get_text())
            if m:
                helpful = int(m.group())

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


async def _is_blocked(page) -> bool:
    """Return True if DataDome has served a challenge or block page."""
    title = await page.title()
    url = page.url
    return (
        "datadome" in url.lower()
        or "just a moment" in title.lower()
        or "access denied" in title.lower()
        or await page.query_selector("#datadome") is not None
    )


async def scrape(
    company: str,
    max_reviews: int = 50,
    sort_by: str = "recent",
    min_rating: int | None = None,
    proxy_url: str | None = None,
    get_proxy_url=None,
    **_kwargs,
) -> list[dict]:
    records: list[dict] = []

    proxy = None
    if get_proxy_url:
        try:
            proxy = await get_proxy_url() if asyncio.iscoroutinefunction(get_proxy_url) else get_proxy_url()
        except Exception:
            pass
    if not proxy:
        proxy = proxy_url

    if proxy:
        masked = proxy.split("@")[-1] if "@" in proxy else proxy
        print(f"[g2] using proxy: ...@{masked}", flush=True)

    proxy_opts = {"server": proxy} if proxy else None
    slug = re.sub(r"[^a-z0-9]+", "-", company.lower()).strip("-")
    product_url = f"https://www.g2.com/products/{slug}/reviews"
    g2_sort = SORT_MAP.get(sort_by, "most_recent")

    async with AsyncCamoufox(headless=True, proxy=proxy_opts, firefox_user_prefs={"security.sandbox.content.level": 0}) as browser:
        page = await browser.new_page()
        page_num = 1

        while len(records) < max_reviews:
            url = f"{product_url}?sort={g2_sort}&page={page_num}"
            if min_rating:
                url += f"&filters[star_rating]={min_rating}"

            html = ""
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            except Exception as e:
                print(f"[g2] goto failed page {page_num}: {e}", flush=True)

            for poll in range(10):
                try:
                    html = await page.content()
                except Exception:
                    html = ""
                print(f"[g2] poll {poll}: url={page.url}, html_len={len(html)}", flush=True)
                if html and len(html) > 500:
                    break
                await asyncio.sleep(4)

            if not html:
                break

            if await _is_blocked(page):
                print("[g2] DataDome block detected — stopping", flush=True)
                break

            page_records = _parse_reviews(html, company, product_url)
            if not page_records:
                break

            records.extend(page_records)
            page_num += 1
            await asyncio.sleep(2)

    return records[:max_reviews]
