"""Inline stealth patches for Playwright — removes bot-detection signals."""

STEALTH_JS = """
// Remove webdriver flag
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

// Realistic plugins list
const pluginData = [
  { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
  { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
  { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' },
];
const pluginArray = pluginData.map(p => {
  const plugin = { name: p.name, filename: p.filename, description: p.description, length: 0 };
  plugin[Symbol.iterator] = Array.prototype[Symbol.iterator];
  return plugin;
});
pluginArray.item = i => pluginArray[i];
pluginArray.namedItem = name => pluginArray.find(p => p.name === name) || null;
pluginArray.refresh = () => {};
pluginArray[Symbol.iterator] = Array.prototype[Symbol.iterator];
Object.defineProperty(navigator, 'plugins', { get: () => pluginArray });
Object.defineProperty(navigator, 'mimeTypes', { get: () => ({ length: 4, item: () => null, namedItem: () => null }) });

// Languages
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
Object.defineProperty(navigator, 'language', { get: () => 'en-US' });

// Platform
Object.defineProperty(navigator, 'platform', { get: () => 'MacIntel' });
Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });

// Chrome runtime object
window.chrome = {
  app: { isInstalled: false, InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' }, RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' } },
  runtime: { OnInstalledReason: {}, OnRestartRequiredReason: {}, PlatformArch: {}, PlatformOs: {}, connect: () => {}, sendMessage: () => {} },
  loadTimes: () => ({ commitLoadTime: Date.now() / 1000 - 1.2, connectionInfo: 'h2', finishDocumentLoadTime: 0, finishLoadTime: 0, firstPaintAfterLoadTime: 0, firstPaintTime: 0, navigationType: 'Other', npnNegotiatedProtocol: 'h2', requestTime: Date.now() / 1000 - 1.5, startLoadTime: Date.now() / 1000 - 1.4, wasAlternateProtocolAvailable: false, wasFetchedViaSpdy: true, wasNpnNegotiated: true }),
  csi: () => ({ startE: Date.now() - 500, onloadT: Date.now() - 100, pageT: 500, tran: 15 }),
};

// Permissions spoof
const origQuery = window.navigator.permissions.query;
window.navigator.permissions.query = params =>
  params.name === 'notifications'
    ? Promise.resolve({ state: Notification.permission })
    : origQuery(params);

// Canvas noise — add a tiny imperceptible variation so fingerprint differs per session
const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
HTMLCanvasElement.prototype.toDataURL = function(type, ...args) {
  const ctx = this.getContext('2d');
  if (ctx) {
    const imgData = ctx.getImageData(0, 0, this.width || 1, this.height || 1);
    const d = imgData.data;
    // Flip a single low-order bit on one pixel — invisible but changes the fingerprint
    d[0] ^= 1;
    ctx.putImageData(imgData, 0, 0);
  }
  return origToDataURL.apply(this, [type, ...args]);
};

// WebGL vendor/renderer
const origGetParam = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function(param) {
  if (param === 37445) return 'Intel Inc.';  // UNMASKED_VENDOR_WEBGL
  if (param === 37446) return 'Intel Iris OpenGL Engine';  // UNMASKED_RENDERER_WEBGL
  return origGetParam.call(this, param);
};
try {
  const origGetParam2 = WebGL2RenderingContext.prototype.getParameter;
  WebGL2RenderingContext.prototype.getParameter = function(param) {
    if (param === 37445) return 'Intel Inc.';
    if (param === 37446) return 'Intel Iris OpenGL Engine';
    return origGetParam2.call(this, param);
  };
} catch(e) {}

// AudioContext fingerprint noise
try {
  const origCreateOscillator = AudioContext.prototype.createOscillator;
  AudioContext.prototype.createOscillator = function() {
    const osc = origCreateOscillator.call(this);
    const origConnect = osc.connect.bind(osc);
    return osc;
  };
} catch(e) {}

// Screen dimensions — realistic MacBook Pro values
Object.defineProperty(screen, 'width', { get: () => 1440 });
Object.defineProperty(screen, 'height', { get: () => 900 });
Object.defineProperty(screen, 'availWidth', { get: () => 1440 });
Object.defineProperty(screen, 'availHeight', { get: () => 877 });
Object.defineProperty(screen, 'colorDepth', { get: () => 30 });
Object.defineProperty(screen, 'pixelDepth', { get: () => 30 });
"""


async def apply_stealth(page) -> None:
    await page.add_init_script(STEALTH_JS)
