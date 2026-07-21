"""B2B Reviews Scraper — orchestrates G2, Capterra, and Trustpilot scrapers."""

import asyncio
import sys

from apify import Actor

from src.scrapers import capterra, g2, trustpilot

CHECKPOINT_KEY = "SCRAPER_CHECKPOINT"

SCRAPER_MAP = {
    "g2": g2.scrape,
    "capterra": capterra.scrape,
    "trustpilot": trustpilot.scrape,
}


async def main() -> None:
    print("B2B Reviews Scraper starting", flush=True)
    async with Actor:
        Actor.log.info("Actor initialized")

        inp = await Actor.get_input() or {}
        Actor.log.info(f"Input received: {list(inp.keys())}")

        companies: list[str] = inp.get("companies") or []
        platforms: list[str] = inp.get("platforms") or ["g2", "capterra", "trustpilot"]
        max_per_platform: int = int(inp.get("maxReviewsPerPlatform") or 50)
        sort_by: str = inp.get("sortBy") or "recent"
        min_rating: int | None = inp.get("minRating")
        trustpilot_api_key: str | None = inp.get("trustpilotApiKey") or None
        proxy_input = inp.get("proxyConfiguration")

        if not companies:
            await Actor.fail(status_message="Input must include at least one company name.")
            return

        unknown = [p for p in platforms if p not in SCRAPER_MAP]
        if unknown:
            await Actor.fail(status_message=f"Unknown platform(s): {unknown}. Use g2, capterra, trustpilot.")
            return

        proxy_url: str | None = None
        proxy_config = None
        try:
            if proxy_input and isinstance(proxy_input, dict):
                groups = proxy_input.get("groups") or []
                country = proxy_input.get("countryCode")
                proxy_config = await Actor.create_proxy_configuration(
                    groups=groups,
                    **({"country_code": country} if country else {}),
                )
            else:
                # Prefer residential proxies — datacenter IPs are blocked by G2/Capterra/Trustpilot
                try:
                    proxy_config = await Actor.create_proxy_configuration(groups=["RESIDENTIAL"])
                except Exception:
                    proxy_config = await Actor.create_proxy_configuration()
            proxy_url = await proxy_config.new_url() if proxy_config else None
        except Exception as exc:
            Actor.log.warning(f"Proxy setup failed ({exc}) — running without proxy")

        Actor.log.info(f"Proxy: {'enabled' if proxy_url else 'disabled'}")

        # Checkpoint: track which (company, platform) pairs are done
        checkpoint = await Actor.get_value(CHECKPOINT_KEY) or {}
        done: set[str] = set(checkpoint.get("done") or [])
        total_pushed: int = checkpoint.get("total_pushed") or 0

        async def save_checkpoint() -> None:
            await Actor.set_value(CHECKPOINT_KEY, {"done": list(done), "total_pushed": total_pushed})

        for company in companies:
            for platform in platforms:
                pair_key = f"{company}||{platform}"
                if pair_key in done:
                    Actor.log.info(f"Skipping {company} / {platform} (already done)")
                    continue

                Actor.log.info(f"Scraping {platform} for: {company}")
                scrape_fn = SCRAPER_MAP[platform]

                try:
                    extra: dict = {}
                    if platform == "trustpilot" and trustpilot_api_key:
                        extra["api_key"] = trustpilot_api_key
                    if proxy_config:
                        extra["get_proxy_url"] = proxy_config.new_url
                    records = await scrape_fn(
                        company=company,
                        max_reviews=max_per_platform,
                        sort_by=sort_by,
                        min_rating=min_rating,
                        proxy_url=proxy_url,
                        **extra,
                    )
                except Exception as exc:
                    Actor.log.warning(f"Error scraping {platform} for {company}: {exc}")
                    records = []

                if records:
                    await Actor.push_data(records)
                    total_pushed += len(records)
                    await Actor.charge("review-scraped", count=len(records))

                done.add(pair_key)
                await save_checkpoint()

                Actor.log.info(f"  → {len(records)} reviews from {platform} for {company} (total: {total_pushed})")

        Actor.log.info(f"Done. Total reviews pushed: {total_pushed}")

if __name__ == "__main__":
    asyncio.run(main())
