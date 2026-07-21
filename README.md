# B2B Reviews Scraper — G2, Capterra & Trustpilot in One Run

**Stop switching between three tabs. Pull reviews from G2, Capterra, and Trustpilot with a single actor run.**

Whether you're monitoring your own reputation, researching a competitor, or building a review dashboard for a client — this actor gives you a unified, structured dataset across the three most-cited B2B review platforms.

---

## What it scrapes

| Platform | What you get |
|---|---|
| **G2** | Rating, title, pros/cons, reviewer job title, company size, helpful votes, verified badge |
| **Capterra** | Rating, title, pros/cons, reviewer name, date |
| **Trustpilot** | Rating, title, full review body, reviewer name, date, verified badge |

All three platforms in one dataset. One row per review, ready to export or pipe into your workflow.

---

## Who uses it

- **Agencies** building monthly reputation reports for clients — run once, get everything
- **Product teams** monitoring competitor reviews for feature gaps and complaints
- **Sales teams** pulling social proof for battlecards and case studies
- **Founders** tracking their own NPS trends across platforms over time
- **Researchers** building datasets of product reviews for analysis or fine-tuning

---

## Input

```json
{
  "companies": ["Asana", "monday.com", "Trello"],
  "platforms": ["g2", "capterra", "trustpilot"],
  "maxReviewsPerPlatform": 50,
  "sortBy": "recent"
}
```

**Input fields:**

| Field | Required | Default | Description |
|---|---|---|---|
| `companies` | ✅ | — | Company or product names to search for. Use the name as it appears on the review site (e.g. `"monday.com"`, `"HubSpot CRM"`). |
| `platforms` | — | all three | Which platforms to scrape: `g2`, `capterra`, `trustpilot`. Pass a subset to skip platforms. |
| `maxReviewsPerPlatform` | — | `50` | Max reviews per company per platform. Up to 500. |
| `sortBy` | — | `recent` | `recent`, `helpful`, `highest`, `lowest` |
| `minRating` | — | — | Only collect reviews at or above this star rating (1–5). Useful for pulling only negative reviews for competitor research. |

---

## Output

### Dataset — one row per review

```json
{
  "company": "Asana",
  "platform": "g2",
  "reviewer_name": "Sarah K.",
  "reviewer_title": "Head of Operations",
  "reviewer_company_size": "51-200 employees",
  "rating": 4.5,
  "title": "Best project management tool we've tried",
  "body": "We moved from Jira and haven't looked back. The timeline view alone saved us hours every week.",
  "pros": "Timeline view, automation rules, clean UI.",
  "cons": "Reporting could be more flexible. Exporting is clunky.",
  "date": "2025-04-12",
  "verified": true,
  "helpful_count": 14,
  "review_url": "https://www.g2.com/reviews/asana-review-12345",
  "product_url": "https://www.g2.com/products/asana/reviews"
}
```

**Key fields:**

- `pros` / `cons` — G2 and Capterra split reviews into structured pros and cons sections; the full text is also in `body`
- `reviewer_title` / `reviewer_company_size` — available on G2; tells you who is actually using the product
- `verified` — whether the platform has verified the reviewer used the product
- `helpful_count` — number of upvotes on G2; useful for surfacing the most resonant reviews

---

## Use cases and recipes

### Competitor research — pull only negative reviews

```json
{
  "companies": ["CompetitorA", "CompetitorB"],
  "platforms": ["g2", "capterra"],
  "sortBy": "lowest",
  "minRating": 1,
  "maxReviewsPerPlatform": 100
}
```

Filter for `rating <= 2` in your dataset. The `cons` field gives you structured complaints — paste the most common ones into your sales battlecard.

### Weekly reputation monitoring

1. Set up a **Schedule** in Apify (Actors → Schedules → New schedule)
2. Run weekly with `sortBy: "recent"` and `maxReviewsPerPlatform: 20`
3. Connect the dataset to a Google Sheet or Slack webhook via Apify integrations
4. Get alerted when new reviews come in — without ever opening a browser

### Build a review dashboard

Export the dataset to Google Sheets, Airtable, or any BI tool. The unified schema (same fields regardless of platform) means you can chart average rating by platform, review volume over time, or rating distribution — all in one pivot table.

---

## Pricing

Pay per review collected — a small flat fee per review pushed to the dataset. No subscription, no minimum commitment. Run it once or schedule it weekly.

**Example:** 3 companies × 3 platforms × 50 reviews = up to 450 reviews. You only pay for reviews actually collected (some companies may have fewer).

---

## Scheduling

1. Go to **Actors → Schedules → New schedule** in Apify console
2. Point it at this actor with your saved input
3. Choose your cadence — weekly is typical for reputation monitoring
4. Connect the output dataset to your reporting tool via Apify integrations

---

## Use as an MCP tool

Add this actor to Claude Desktop, Cursor, or any MCP-compatible client via [Apify's MCP server](https://apify.com/apify/actors-mcp-server) and ask your agent to pull competitor reviews on demand.

---

## FAQ

**The company name I entered returned no results — why?**
Try the name exactly as it appears on the review platform. For example, `"HubSpot CRM"` works better than `"HubSpot"` on G2, where each product is listed separately. If a company has multiple products, search for the specific product name.

**Why are `pros` and `cons` null for some Trustpilot reviews?**
Trustpilot doesn't split reviews into pros/cons — the full review text is in `body`. G2 and Capterra have structured pros/cons sections; the combined text is also copied into `body` for consistency.

**Can I scrape my own company's reviews alongside competitors?**
Yes — just add your company to the `companies` list. Comparing your review profile against competitors is one of the most common use cases.

**How many reviews can I get per company?**
Up to 500 per platform per company (`maxReviewsPerPlatform: 500`). G2 and Trustpilot typically have the most reviews for established products; Capterra varies by category.

**Does this work for niche software products?**
Yes, as long as the product has a listing on the platform. If a product is very new or small, G2 and Capterra may have few reviews; Trustpilot is broader and covers non-software businesses too.

**Can I use this alongside the AI Brand Visibility Tracker?**
Yes — they're complementary. The [AI Brand Visibility Tracker](https://apify.com/dbott23/ai-brand-visibility-tracker) tells you what AI assistants say about a brand; this actor tells you what real customers say on review platforms. Together they give you the full picture of a brand's reputation.
