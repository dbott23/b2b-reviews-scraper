"""G2 scraper — uses Playwright with stealth patches.

Note: G2 uses DataDome bot detection. Results depend on whether the JS challenge
resolves successfully. Residential proxies improve success rate.
"""

import asyncio
import re
from datetime import datetime

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

from src.scrapers._stealth import apply_stealth

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
    get_proxy_url=None,  # async callable () -> str, for per-page rotation
    **_kwargs,
) -> list[dict]:
    records: list[dict] = []

    async def _fresh_proxy() -> str | None:
        if get_proxy_url:
            try:
                return await get_proxy_url()
            except Exception:
                pass
        return proxy_url

    async def _new_context(pw, browser_ref: list):
        if browser_ref:
            try:
                await browser_ref[0].close()
            except Exception:
                pass
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        browser_ref.clear()
        browser_ref.append(browser)
        url = await _fresh_proxy()
        context_opts: dict = {
            "user_agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "extra_http_headers": {
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"macOS"',
            },
            "viewport": {"width": 1440, "height": 900},
        }
        if url:
            context_opts["proxy"] = {"server": url}
        context = await browser.new_context(**context_opts)
        page = await context.new_page()
        await apply_stealth(page)
        return page

    async with async_playwright() as pw:
        browser_ref: list = []
        page = await _new_context(pw, browser_ref)

        # Derive product slug directly from company name — avoids an extra DataDome-exposed search page
        slug = re.sub(r"[^a-z0-9]+", "-", company.lower()).strip("-")

        # Verify the slug resolves — try the direct reviews URL first, fall back to search
        product_url_candidate = f"https://www.g2.com/products/{slug}/reviews"
        resolved_slug = None

        for attempt in range(2):
            try:
                await page.goto(product_url_candidate, wait_until="domcontentloaded", timeout=30000)
            except Exception:
                pass

            try:
                await page.wait_for_function(
                    "document.title !== '' && document.title !== 'Just a moment...'",
                    timeout=15000,
                )
            except Exception:
                pass

            await asyncio.sleep(3)

            if await _is_blocked(page):
                if attempt == 0:
                    page = await _new_context(pw, browser_ref)
                    continue
                break

            # Check if we landed on a real product page or got redirected to search
            current_url = page.url
            m = re.search(r"/products/([^/?#]+)", current_url)
            if m:
                resolved_slug = m.group(1)
            else:
                # Try search as fallback
                try:
                    await page.goto(
                        f"https://www.g2.com/search?query={company}",
                        wait_until="domcontentloaded",
                        timeout=25000,
                    )
                    await asyncio.sleep(3)
                except Exception:
                    pass
                link = await page.query_selector("a[href*='/products/'][href*='/reviews']")
                if not link:
                    link = await page.query_selector("a[href*='/products/']")
                if link:
                    href = await link.get_attribute("href") or ""
                    m2 = re.search(r"/products/([^/?#]+)", href)
                    if m2:
                        resolved_slug = m2.group(1)
            break

        if not resolved_slug:
            if browser_ref:
                await browser_ref[0].close()
            return []

        slug = resolved_slug

        product_url = f"https://www.g2.com/products/{resolved_slug}/reviews"
        g2_sort = SORT_MAP.get(sort_by, "most_recent")
        page_num = 1

        while len(records) < max_reviews:
            url = f"{product_url}?sort={g2_sort}&page={page_num}"
            if min_rating:
                url += f"&filters[star_rating]={min_rating}"

            try:
                await page.goto(url, wait_until="commit", timeout=25000)
            except Exception:
                pass

            await asyncio.sleep(4)
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(2)

            if await _is_blocked(page):
                # Rotate proxy mid-pagination and retry this page once
                page = await _new_context(pw, browser_ref)
                try:
                    await page.goto(url, wait_until="commit", timeout=25000)
                except Exception:
                    pass
                await asyncio.sleep(5)
                if await _is_blocked(page):
                    break

            html = await page.content()
            page_records = _parse_reviews(html, company, product_url)

            if not page_records:
                break

            records.extend(page_records)
            page_num += 1
            await asyncio.sleep(2)

        if browser_ref:
            await browser_ref[0].close()

    return records[:max_reviews]
