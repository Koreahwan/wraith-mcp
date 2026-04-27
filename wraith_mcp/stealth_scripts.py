"""JavaScript init scripts for browser fingerprint normalization."""

NAVIGATOR_STEALTH_SCRIPT = r"""
(() => {
  if (window.__wraithNavigatorStealthPatched) return;
  window.__wraithNavigatorStealthPatched = true;

  const defineGetter = (target, prop, getter) => {
    try {
      Object.defineProperty(target, prop, {
        get: getter,
        configurable: true,
      });
    } catch (_) {}
  };

  try {
    delete Navigator.prototype.webdriver;
    delete navigator.webdriver;
  } catch (_) {}
  defineGetter(Navigator.prototype, 'webdriver', () => undefined);

  const makeMimeType = (type, suffixes, description, plugin) => ({
    type,
    suffixes,
    description,
    enabledPlugin: plugin,
  });

  const pluginData = [
    ['PDF Viewer', 'Portable Document Format', [['application/pdf', 'pdf', 'Portable Document Format']]],
    ['Chrome PDF Viewer', 'Portable Document Format', [['application/pdf', 'pdf', 'Portable Document Format']]],
    ['Chromium PDF Viewer', 'Portable Document Format', [['application/pdf', 'pdf', 'Portable Document Format']]],
    ['Microsoft Edge PDF Viewer', 'Portable Document Format', [['application/pdf', 'pdf', 'Portable Document Format']]],
    ['WebKit built-in PDF', 'Portable Document Format', [['application/pdf', 'pdf', 'Portable Document Format']]],
  ];

  const plugins = pluginData.map(([name, description, mimes], index) => {
    const plugin = {
      name,
      filename: 'internal-pdf-viewer',
      description,
      length: mimes.length,
      item: (i) => plugin[i] || null,
      namedItem: (mimeName) => plugin[mimeName] || null,
    };
    mimes.forEach(([type, suffixes, mimeDescription], mimeIndex) => {
      const mime = makeMimeType(type, suffixes, mimeDescription, plugin);
      plugin[mimeIndex] = mime;
      plugin[type] = mime;
    });
    Object.defineProperty(plugin, '0', { enumerable: true });
    return plugin;
  });

  const pluginArray = {
    length: plugins.length,
    item: (index) => plugins[index] || null,
    namedItem: (name) => plugins.find((plugin) => plugin.name === name) || null,
    refresh: () => undefined,
    [Symbol.iterator]: function* () {
      for (const plugin of plugins) yield plugin;
    },
  };
  plugins.forEach((plugin, index) => {
    pluginArray[index] = plugin;
    pluginArray[plugin.name] = plugin;
  });
  try {
    Object.setPrototypeOf(pluginArray, PluginArray.prototype);
    plugins.forEach((plugin) => Object.setPrototypeOf(plugin, Plugin.prototype));
  } catch (_) {}
  defineGetter(Navigator.prototype, 'plugins', () => pluginArray);

  const languages = Object.freeze(['en-US', 'en']);
  defineGetter(Navigator.prototype, 'languages', () => languages);
  defineGetter(Navigator.prototype, 'language', () => 'en-US');
  defineGetter(Navigator.prototype, 'hardwareConcurrency', () => 8);
  defineGetter(Navigator.prototype, 'deviceMemory', () => 8);

  const connection = {
    downlink: 10,
    effectiveType: '4g',
    rtt: 50,
    saveData: false,
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    dispatchEvent: () => true,
  };
  defineGetter(Navigator.prototype, 'connection', () => connection);
  defineGetter(Navigator.prototype, 'mozConnection', () => connection);
  defineGetter(Navigator.prototype, 'webkitConnection', () => connection);

  window.chrome = window.chrome || {};
  window.chrome.runtime = window.chrome.runtime || {
    PlatformOs: {
      MAC: 'mac',
      WIN: 'win',
      ANDROID: 'android',
      CROS: 'cros',
      LINUX: 'linux',
      OPENBSD: 'openbsd',
    },
    PlatformArch: {
      ARM: 'arm',
      ARM64: 'arm64',
      X86_32: 'x86-32',
      X86_64: 'x86-64',
    },
    PlatformNaclArch: {
      ARM: 'arm',
      X86_32: 'x86-32',
      X86_64: 'x86-64',
    },
    RequestUpdateCheckStatus: {
      THROTTLED: 'throttled',
      NO_UPDATE: 'no_update',
      UPDATE_AVAILABLE: 'update_available',
    },
    OnInstalledReason: {
      INSTALL: 'install',
      UPDATE: 'update',
      CHROME_UPDATE: 'chrome_update',
      SHARED_MODULE_UPDATE: 'shared_module_update',
    },
    OnRestartRequiredReason: {
      APP_UPDATE: 'app_update',
      OS_UPDATE: 'os_update',
      PERIODIC: 'periodic',
    },
    connect: () => ({ onMessage: {}, onDisconnect: {}, postMessage: () => undefined, disconnect: () => undefined }),
    sendMessage: () => undefined,
  };

  const originalQuery = window.navigator.permissions && window.navigator.permissions.query;
  if (originalQuery) {
    window.navigator.permissions.query = function query(permissionDescriptor) {
      if (permissionDescriptor && permissionDescriptor.name === 'notifications') {
        return Promise.resolve({ state: 'granted', onchange: null });
      }
      return originalQuery.apply(this, arguments);
    };
  }
})();
"""

WINDOW_SCREEN_STEALTH_SCRIPT = r"""
(() => {
  if (window.__wraithWindowScreenStealthPatched) return;
  window.__wraithWindowScreenStealthPatched = true;

  const defineGetter = (target, prop, getter) => {
    try {
      Object.defineProperty(target, prop, {
        get: getter,
        configurable: true,
      });
    } catch (_) {}
  };

  defineGetter(window, 'outerWidth', () => window.innerWidth || 1920);
  defineGetter(window, 'outerHeight', () => window.innerHeight || 1080);
  defineGetter(Screen.prototype, 'colorDepth', () => 24);
  defineGetter(Screen.prototype, 'pixelDepth', () => 24);
})();
"""

FINGERPRINT_NOISE_SCRIPT = r"""
(() => {
  if (window.__wraithFingerprintNoisePatched) return;
  window.__wraithFingerprintNoisePatched = true;

  const seed = Math.floor(Math.random() * 1000000) + 1;
  const noise = (index) => (((seed * (index + 1)) % 7) - 3);

  const patchCanvas = (canvas) => {
    try {
      const context = canvas.getContext && canvas.getContext('2d', { willReadFrequently: true });
      if (!context) return;
      const width = canvas.width;
      const height = canvas.height;
      if (!width || !height) return;
      const imageData = context.getImageData(0, 0, width, height);
      const data = imageData.data;
      const stride = Math.max(1, Math.floor(data.length / 64));
      for (let i = 0; i < data.length; i += 4 * stride) {
        data[i] = Math.max(0, Math.min(255, data[i] + noise(i)));
        data[i + 1] = Math.max(0, Math.min(255, data[i + 1] + noise(i + 1)));
        data[i + 2] = Math.max(0, Math.min(255, data[i + 2] + noise(i + 2)));
      }
      context.putImageData(imageData, 0, 0);
    } catch (_) {}
  };

  const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
  HTMLCanvasElement.prototype.toDataURL = function toDataURL() {
    patchCanvas(this);
    return originalToDataURL.apply(this, arguments);
  };

  const originalToBlob = HTMLCanvasElement.prototype.toBlob;
  HTMLCanvasElement.prototype.toBlob = function toBlob() {
    patchCanvas(this);
    return originalToBlob.apply(this, arguments);
  };

  const patchAudioContext = (AudioContextClass) => {
    if (!AudioContextClass || !AudioContextClass.prototype) return;
    const originalCreateAnalyser = AudioContextClass.prototype.createAnalyser;
    if (!originalCreateAnalyser) return;
    AudioContextClass.prototype.createAnalyser = function createAnalyser() {
      const analyser = originalCreateAnalyser.apply(this, arguments);
      const originalGetFloatFrequencyData = analyser.getFloatFrequencyData;
      const originalGetByteFrequencyData = analyser.getByteFrequencyData;
      if (originalGetFloatFrequencyData) {
        analyser.getFloatFrequencyData = function getFloatFrequencyData(array) {
          originalGetFloatFrequencyData.apply(this, arguments);
          for (let i = 0; i < array.length; i += 16) array[i] += noise(i) * 0.0001;
        };
      }
      if (originalGetByteFrequencyData) {
        analyser.getByteFrequencyData = function getByteFrequencyData(array) {
          originalGetByteFrequencyData.apply(this, arguments);
          for (let i = 0; i < array.length; i += 16) array[i] = Math.max(0, Math.min(255, array[i] + noise(i)));
        };
      }
      return analyser;
    };
  };

  patchAudioContext(window.AudioContext);
  patchAudioContext(window.webkitAudioContext);
})();
"""

WEBGL_STEALTH_SCRIPT = r"""
(() => {
  if (window.__wraithWebGLStealthPatched) return;
  window.__wraithWebGLStealthPatched = true;

  const vendor = 'Google Inc. (Intel)';
  const renderer = 'ANGLE (Intel, Intel(R) UHD Graphics 620 Direct3D11 vs_5_0 ps_5_0, D3D11)';
  const unmaskedVendor = 37445;
  const unmaskedRenderer = 37446;

  const patchGetParameter = (prototype) => {
    if (!prototype || !prototype.getParameter || prototype.__wraithWebGLPatched) return;
    const originalGetParameter = prototype.getParameter;
    Object.defineProperty(prototype, '__wraithWebGLPatched', { value: true });
    prototype.getParameter = function getParameter(parameter) {
      if (parameter === unmaskedVendor) return vendor;
      if (parameter === unmaskedRenderer) return renderer;
      return originalGetParameter.apply(this, arguments);
    };
  };

  patchGetParameter(window.WebGLRenderingContext && WebGLRenderingContext.prototype);
  patchGetParameter(window.WebGL2RenderingContext && WebGL2RenderingContext.prototype);
})();
"""

STEALTH_INIT_SCRIPTS = (
    NAVIGATOR_STEALTH_SCRIPT,
    WINDOW_SCREEN_STEALTH_SCRIPT,
    FINGERPRINT_NOISE_SCRIPT,
    WEBGL_STEALTH_SCRIPT,
)
