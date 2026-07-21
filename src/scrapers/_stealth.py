"""Inline stealth patches for Playwright — removes bot-detection signals."""

STEALTH_JS = """
// Remove webdriver flag
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

// Fake plugins
Object.defineProperty(navigator, 'plugins', {
  get: () => [1, 2, 3, 4, 5],
});

// Fake mimeTypes
Object.defineProperty(navigator, 'mimeTypes', {
  get: () => [1],
});

// Languages
Object.defineProperty(navigator, 'languages', {
  get: () => ['en-US', 'en'],
});

// Chrome object
window.chrome = { runtime: {} };

// Permissions API spoof
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) =>
  parameters.name === 'notifications'
    ? Promise.resolve({ state: Notification.permission })
    : originalQuery(parameters);
"""


async def apply_stealth(page) -> None:
    await page.add_init_script(STEALTH_JS)
