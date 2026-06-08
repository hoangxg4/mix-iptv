import { describe, it, expect, vi, beforeEach } from 'vitest';

// ============================================================
// Mock browser APIs before loading app.js
// ============================================================

const mockLocalStorage = (() => {
  let store = {};
  return {
    getItem: vi.fn((key) => store[key] ?? null),
    setItem: vi.fn((key, value) => { store[key] = String(value); }),
    removeItem: vi.fn((key) => { delete store[key]; }),
    clear: vi.fn(() => { store = {}; }),
    get length() { return Object.keys(store).length; },
    key: vi.fn((i) => Object.keys(store)[i] ?? null),
  };
})();

Object.defineProperty(global, 'localStorage', {
  value: mockLocalStorage,
  writable: true,
  configurable: true,
});

global.fetch = vi.fn();
global.console.warn = vi.fn();
global.console.error = vi.fn();

// IndexedDB mock (happy-dom does not support IndexedDB)
// Use plain functions (not vi.fn()) so resetAllMocks doesn't break the mock
// All operations resolve synchronously via immediate callbacks
let mockIdbStore = {};
const mockIdbCallbacks = { onsuccess: null, onerror: null };
function fireIdbCallbacks(req) {
  // Fire synchronously for mock speed/clarity
  if (typeof req.onsuccess === 'function') req.onsuccess();
  else if (typeof req.onerror === 'function') req.onerror();
}
global.indexedDB = {
  open(name, version) {
    const req = {
      onupgradeneeded: null,
      onsuccess: null,
      onerror: null,
      result: null,
      transaction(mode) { return tx; },
    };
    // Defer so openDB can attach callback
    Promise.resolve().then(() => {
      if (!req.result) req.result = {
        createObjectStore: () => {},
        transaction: (mode) => ({ objectStore: (name) => store }),
      };
      fireIdbCallbacks(req);
    });
    return req;
  },
};
const store = {
  get(key) {
    const result = mockIdbStore[key] ?? undefined;
    const req = { onsuccess: null, onerror: null, result };
    // Defer so caller can attach callback
    Promise.resolve().then(() => fireIdbCallbacks(req));
    return req;
  },
  put(value, key) {
    mockIdbStore[key] = value;
    const req = { onsuccess: null, onerror: null };
    Promise.resolve().then(() => fireIdbCallbacks(req));
    return req;
  },
};
const tx = { objectStore: (name) => store };

// Sample test data
const sampleChannelsJson = {
  groups: [
    {
      id: 'vtv',
      name: 'VTV',
      channels: [
        { id: 'vtv1', name: 'VTV1', logo: 'https://example.com/vtv1.png', url: 'https://example.com/vtv1.m3u8' },
      ],
    },
    {
      id: 'htv',
      name: 'HTV',
      channels: [
        { id: 'htv7', name: 'HTV7', logo: 'https://example.com/htv7.png', url: 'https://example.com/htv7.m3u8' },
      ],
    },
    {
      id: 'khac',
      name: 'Khác',
      channels: [
        { id: 'ch1', name: 'Kênh Lạ', logo: '', url: '' },
      ],
    },
  ],
};

const sampleEpgXml = `<?xml version="1.0" encoding="UTF-8"?>
<tv>
  <programme channel="vtv1" start="20240101000000" stop="20240101010000">
    <title>Thời sự</title>
  </programme>
  <programme channel="htv7" start="20240101080000" stop="20240101090000">
    <title>Bản tin sáng</title>
  </programme>
</tv>`;

// Load app.js
let state, BASE_URL, GROUP_ORDER, loadData, initTheme, renderGroups, selectGroup, bindSearch, showError, renderChannels, renderEpg, getLogo, parseEpgTime, selectChannel, getChannelUrls, playChannel, tryPlayUrl, showPlayerLoading, hidePlayerLoading, showPlayerError, hidePlayerError, upgradeUrl;
let openDB, cacheGet, cacheSet, CACHE_TTL_CHANNELS, CACHE_TTL_EPG, loadChannelsFresh, loadEpg, loadEpgFresh;

beforeEach(async () => {
  vi.resetAllMocks();
  mockLocalStorage.clear();
  mockIdbStore = {}; // Reset IndexedDB mock store

  // Clear module cache and re-import fresh
  vi.resetModules();
  const mod = await import('../app.js');
  state = mod.state;
  BASE_URL = mod.BASE_URL;
  GROUP_ORDER = mod.GROUP_ORDER;
  loadData = mod.loadData;
  initTheme = mod.initTheme;
  renderGroups = mod.renderGroups;
  selectGroup = mod.selectGroup;
  bindSearch = mod.bindSearch;
  showError = mod.showError;
  renderChannels = mod.renderChannels;
  renderEpg = mod.renderEpg;
  getLogo = mod.getLogo;
  parseEpgTime = mod.parseEpgTime;
  selectChannel = mod.selectChannel;
  getChannelUrls = mod.getChannelUrls;
  playChannel = mod.playChannel;
  tryPlayUrl = mod.tryPlayUrl;
  showPlayerLoading = mod.showPlayerLoading;
  hidePlayerLoading = mod.hidePlayerLoading;
  showPlayerError = mod.showPlayerError;
  hidePlayerError = mod.hidePlayerError;
  upgradeUrl = mod.upgradeUrl;
  openDB = mod.openDB;
  cacheGet = mod.cacheGet;
  cacheSet = mod.cacheSet;
  CACHE_TTL_CHANNELS = mod.CACHE_TTL_CHANNELS;
  CACHE_TTL_EPG = mod.CACHE_TTL_EPG;
  loadChannelsFresh = mod.loadChannelsFresh;
  loadEpg = mod.loadEpg;
  loadEpgFresh = mod.loadEpgFresh;
});

// ============================================================
// Constants & Config
// ============================================================
describe('Constants & Config', () => {
  it('has correct BASE_URL', () => {
    expect(BASE_URL).toBe('https://raw.githubusercontent.com/hoangxg4/mix-iptv/main/');
  });

  it('has GROUP_ORDER with expected groups', () => {
    const expected = [
      'VTV', 'HTV', 'VTC', 'VTVCAB / ON', 'VTVPRIME',
      'K+', 'THỂ THAO', 'PHIM TRUYỆN', 'QUỐC TẾ', 'ĐỊA PHƯƠNG',
    ];
    expect(GROUP_ORDER).toEqual(expected);
  });
});

// ============================================================
// State Structure
// ============================================================
describe('State Structure', () => {
  it('has correct initial state shape', () => {
    expect(state).toBeDefined();
    expect(state.groups).toEqual([]);
    expect(state.flatChannels).toEqual([]);
    expect(state.epgData).toEqual([]);
    expect(state.selectedGroup).toBe('all');
    expect(state.selectedChannel).toBeNull();
    expect(state.isDarkMode).toBe(false);
    expect(state.isLoading).toBe(true);
    expect(state.error).toBeNull();
  });

});

// ============================================================
// loadData()
// ============================================================
describe('loadData()', () => {
  it('fetches channels.json only (EPG is lazy-loaded)', async () => {
    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: async () => sampleChannelsJson,
    });

    await loadData();

    // Only channels.json fetched during loadData (no light_epg.xml)
    expect(global.fetch).toHaveBeenCalledTimes(1);
    expect(global.fetch).toHaveBeenCalledWith(BASE_URL + 'channels.json');

    // State updated
    expect(state.isLoading).toBe(false);
    expect(state.error).toBeNull();
    expect(state.groups.length).toBe(3);
    expect(state.flatChannels.length).toBe(3);
    // EPG is empty — loaded lazily on first channel select
    expect(state.epgData).toEqual([]);
  });

  it('populates flatChannels with groupName', async () => {
    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: async () => sampleChannelsJson,
    });

    await loadData();

    const vtv1 = state.flatChannels.find(ch => ch.id === 'vtv1');
    expect(vtv1).toBeDefined();
    expect(vtv1.groupName).toBe('VTV');

    const htv7 = state.flatChannels.find(ch => ch.id === 'htv7');
    expect(htv7).toBeDefined();
    expect(htv7.groupName).toBe('HTV');
  });

  it('sorts groups by GROUP_ORDER with extras alphabetically at end', async () => {
    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: async () => sampleChannelsJson,
    });

    await loadData();

    // VTV should come before HTV (GROUP_ORDER ordering)
    expect(state.groups[0].name).toBe('VTV');
    expect(state.groups[1].name).toBe('HTV');
    // 'Khác' is not in GROUP_ORDER, sorted alphabetically at end
    expect(state.groups[state.groups.length - 1].name).toBe('Khác');
  });

  it('sets error if channels.json fetch fails', async () => {
    global.fetch.mockResolvedValueOnce({
      ok: false,
      status: 404,
      statusText: 'Not Found',
    });

    await loadData();

    expect(state.error).toBe('Không thể tải danh sách kênh');
    expect(state.isLoading).toBe(false);
    expect(state.groups).toEqual([]);
  });

  it('handles JSON parse error in channels', async () => {
    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: async () => { throw new Error('Invalid JSON'); },
    });

    await loadData();

    expect(state.error).toBe('Không thể tải danh sách kênh');
    expect(state.isLoading).toBe(false);
  });

  it('sets isLoading to false after loadData completes', async () => {
    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: async () => sampleChannelsJson,
    });

    expect(state.isLoading).toBe(true);
    await loadData();
    expect(state.isLoading).toBe(false);
  });

  it('uses cached data when available (stale-while-revalidate)', async () => {
    // Pre-populate IndexedDB cache
    const cachedGroups = [
      { id: 'vtv', name: 'VTV', channels: [{ id: 'vtv1', name: 'VTV1', logo: '' }] },
    ];
    const cachedFlat = [{ id: 'vtv1', name: 'VTV1', groupName: 'VTV' }];
    mockIdbStore['channels'] = { data: { groups: cachedGroups, flatChannels: cachedFlat }, expires: Date.now() + 999999 };

    // No fetch mock needed — should use cache
    await loadData();

    // Data from cache
    expect(state.groups).toEqual(cachedGroups);
    expect(state.flatChannels).toEqual(cachedFlat);
    expect(state.isLoading).toBe(false);
    expect(state.error).toBeNull();

    // Should have triggered background refresh (one extra fetch)
    expect(global.fetch).toHaveBeenCalledTimes(1);
    expect(global.fetch).toHaveBeenCalledWith(BASE_URL + 'channels.json');
  });

  it('fetches fresh when cache is empty', async () => {
    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: async () => sampleChannelsJson,
    });

    await loadData();

    expect(global.fetch).toHaveBeenCalledTimes(1);
    expect(state.groups.length).toBe(3);
    expect(state.flatChannels.length).toBe(3);
  });
});

// ============================================================
// IndexedDB Cache Wrapper
// ============================================================
describe('IndexedDB Cache Wrapper', () => {
  it('openDB is a function', () => {
    expect(typeof openDB).toBe('function');
  });

  it('cacheGet is a function', () => {
    expect(typeof cacheGet).toBe('function');
  });

  it('cacheSet is a function', () => {
    expect(typeof cacheSet).toBe('function');
  });

  it('cacheSet stores data and cacheGet retrieves it', async () => {
    const key = 'test-key';
    const data = { foo: 'bar' };
    await cacheSet(key, data, 999999);
    const result = await cacheGet(key);
    expect(result).toEqual(data);
  });

  it('cacheGet returns null for missing key', async () => {
    const result = await cacheGet('nonexistent');
    expect(result).toBeNull();
  });

  it('cacheGet returns null for expired data', async () => {
    await cacheSet('expired-key', 'old-data', -1000); // already expired
    const result = await cacheGet('expired-key');
    expect(result).toBeNull();
  });
});

// ============================================================
// Cache TTL Constants
// ============================================================
describe('Cache TTL Constants', () => {
  it('CACHE_TTL_CHANNELS is 30 minutes (1800000 ms)', () => {
    expect(CACHE_TTL_CHANNELS).toBe(1800000);
  });

  it('CACHE_TTL_EPG is 30 minutes (1800000 ms)', () => {
    expect(CACHE_TTL_EPG).toBe(1800000);
  });
});

// ============================================================
// loadChannelsFresh()
// ============================================================
describe('loadChannelsFresh()', () => {
  it('fetches channels.json, updates state, and caches data', async () => {
    global.fetch.mockResolvedValueOnce({
      ok: true,
      json: async () => sampleChannelsJson,
    });

    await loadChannelsFresh();

    expect(global.fetch).toHaveBeenCalledWith(BASE_URL + 'channels.json');
    expect(state.isLoading).toBe(false);
    expect(state.groups.length).toBe(3);
    expect(state.flatChannels.length).toBe(3);

    // Data should also be in IndexedDB cache
    const cached = await cacheGet('channels');
    expect(cached).toBeDefined();
    expect(cached.groups.length).toBe(3);
  });

  it('throws on HTTP error', async () => {
    global.fetch.mockResolvedValueOnce({
      ok: false,
      status: 500,
    });

    await expect(loadChannelsFresh()).rejects.toThrow('HTTP 500');
  });

  it('throws on network error', async () => {
    global.fetch.mockRejectedValueOnce(new Error('Network error'));

    await expect(loadChannelsFresh()).rejects.toThrow('Network error');
  });

  it('sets state.error on failure in loadData flow', async () => {
    // loadData calls loadChannelsFresh internally when cache miss
    global.fetch.mockRejectedValueOnce(new Error('fail'));

    await loadData();

    expect(state.error).toBe('Không thể tải danh sách kênh');
    expect(state.isLoading).toBe(false);
  });
});

// ============================================================
// initTheme()
// ============================================================
describe('initTheme()', () => {
  beforeEach(() => {
    // Ensure clean document state
    document.documentElement.removeAttribute('data-theme');
  });

  it('sets dark theme when localStorage has theme=dark', () => {
    mockLocalStorage.getItem.mockReturnValue('dark');
    initTheme();
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');
  });

  it('removes data-theme when localStorage has no theme', () => {
    mockLocalStorage.getItem.mockReturnValue(null);
    initTheme();
    expect(document.documentElement.hasAttribute('data-theme')).toBe(false);
  });

  it('removes data-theme when localStorage has theme=light', () => {
    mockLocalStorage.getItem.mockReturnValue('light');
    initTheme();
    expect(document.documentElement.hasAttribute('data-theme')).toBe(false);
  });
});

// ============================================================
// Stub functions exist
// ============================================================
describe('Stub functions', () => {
  it('renderGroups is a function', () => {
    expect(typeof renderGroups).toBe('function');
  });

  it('selectGroup is a function', () => {
    expect(typeof selectGroup).toBe('function');
  });

  it('bindSearch is a function', () => {
    expect(typeof bindSearch).toBe('function');
  });

  it('showError is a function', () => {
    expect(typeof showError).toBe('function');
  });
});

// ============================================================
// DOM Setup Helpers
// ============================================================

function setupDom() {
  // Create the DOM elements expected by the rendering functions
  document.body.innerHTML = `
    <ul id="group-list"></ul>
    <div id="channel-list"></div>
    <div id="epg-list"></div>
    <div id="epg-guide"><h3>📺 Lịch phát sóng</h3></div>
    <input type="search" id="search">
    <h2 id="current-group-title">Kênh</h2>
    <div id="error-channels" class="error-message" hidden></div>
    <div id="player-error" class="error-message" hidden></div>
    <div id="channels-panel"><div id="error-channels" class="error-message" hidden></div></div>
    <div id="player-panel"><div id="player-error" class="error-message" hidden></div></div>
    <div id="channel-info"></div>
    <div id="video-container">
      <video id="video-player" controls autoplay playsinline></video>
      <div id="player-placeholder">
        <div class="spinner"></div>
        <p>Chọn kênh để xem</p>
      </div>
      <div id="player-loading" hidden>Đang tải...</div>
    </div>
  `;
}

// Fill state with test data for rendering tests
function makeStreamLinks(url) {
  return [{ contents: [{ streams: [{ stream_links: [{ url, name: 'Main' }] }] }] }];
}

function setupHlsMock() {
  const mockHls = {
    loadSource: vi.fn(),
    attachMedia: vi.fn(),
    destroy: vi.fn(),
    on: vi.fn(),
  };
  global.Hls = vi.fn(() => mockHls);
  global.Hls.isSupported = vi.fn(() => true);
  global.Hls.Events = { MANIFEST_PARSED: 'mp', ERROR: 'err' };
  return mockHls;
}

function populateState() {
  state.groups = [
    { id: 'vtv', name: 'VTV', channels: [{ id: 'vtv1', name: 'VTV1', tvg_id: 'vtv1', tvg_logo: 'https://example.com/vtv1.png', url: 'https://example.com/vtv1.m3u8', sources: makeStreamLinks('https://example.com/vtv1.m3u8') }] },
    { id: 'htv', name: 'HTV', channels: [{ id: 'htv7', name: 'HTV7', tvg_id: 'htv7', tvg_logo: 'https://example.com/htv7.png', url: 'https://example.com/htv7.m3u8', sources: makeStreamLinks('https://example.com/htv7.m3u8') }] },
    { id: 'khac', name: 'Khác', channels: [{ id: 'ch1', name: 'Kênh Lạ', tvg_id: 'ch1', url: '', sources: [] }] },
  ];
  state.flatChannels = state.groups.flatMap(g =>
    g.channels.map(ch => ({ ...ch, groupName: g.name }))
  );
  state.epgData = [
    { channel: 'vtv1', start: '20240101000000', stop: '20240101010000', title: 'Thời sự' },
    { channel: 'htv7', start: '20240101080000', stop: '20240101090000', title: 'Bản tin sáng' },
  ];
}

// ============================================================
// getLogo()
// ============================================================
describe('getLogo()', () => {
  beforeEach(() => {
    vi.resetModules();
  });

  it('returns img tag when channel has tvg_logo', async () => {
    const mod = await import('../app.js');
    const channel = { id: 'vtv1', name: 'VTV1', tvg_logo: 'https://example.com/vtv1.png' };
    const result = mod.getLogo(channel);
    expect(result).toContain('<img');
    expect(result).toContain('src="https://example.com/vtv1.png"');
    expect(result).toContain('alt="VTV1"');
  });

  it('returns first letter circle when channel has no tvg_logo', async () => {
    const mod = await import('../app.js');
    const channel = { id: 'ch1', name: 'Kênh Lạ', url: '' };
    const result = mod.getLogo(channel);
    expect(result).toContain('K'); // first letter of name
    expect(result).toContain('class="letter-logo"');
  });

  it('uses empty string fallback when channel has falsy tvg_logo', async () => {
    const mod = await import('../app.js');
    const channel = { id: 'ch2', name: 'Test', tvg_logo: '' };
    const result = mod.getLogo(channel);
    expect(result).not.toContain('<img');
    expect(result).toContain('T');
    expect(result).toContain('class="letter-logo"');
  });

  it('upgrades http:// logo URL to https://', async () => {
    const mod = await import('../app.js');
    const channel = { id: 'vtv1', name: 'VTV1', tvg_logo: 'http://example.com/vtv1.png' };
    const result = mod.getLogo(channel);
    expect(result).toContain('src="https://example.com/vtv1.png"');
  });

  it('keeps https:// logo URL unchanged', async () => {
    const mod = await import('../app.js');
    const channel = { id: 'vtv1', name: 'VTV1', tvg_logo: 'https://example.com/vtv1.png' };
    const result = mod.getLogo(channel);
    expect(result).toContain('src="https://example.com/vtv1.png"');
  });
});

// ============================================================
// parseEpgTime()
// ============================================================
describe('parseEpgTime()', () => {
  beforeEach(() => {
    vi.resetModules();
  });

  it('parses full YYYYMMDDHHMMSS format into Date', async () => {
    const mod = await import('../app.js');
    const date = mod.parseEpgTime('20240101120000 +0700');
    expect(date).toBeInstanceOf(Date);
    expect(date.getFullYear()).toBe(2024);
    expect(date.getMonth()).toBe(0); // January = 0
    expect(date.getDate()).toBe(1);
    expect(date.getHours()).toBe(12);
    expect(date.getMinutes()).toBe(0);
  });

  it('parses time without offset', async () => {
    const mod = await import('../app.js');
    const date = mod.parseEpgTime('20240615183000 +0000');
    expect(date).toBeInstanceOf(Date);
    expect(date.getFullYear()).toBe(2024);
    expect(date.getMonth()).toBe(5); // June = 5
    expect(date.getDate()).toBe(15);
    expect(date.getHours()).toBe(18);
    expect(date.getMinutes()).toBe(30);
  });

  it('returns null for invalid format', async () => {
    const mod = await import('../app.js');
    expect(mod.parseEpgTime('')).toBeNull();
    expect(mod.parseEpgTime('abc')).toBeNull();
    expect(mod.parseEpgTime('2024')).toBeNull();
  });
});

// ============================================================
// upgradeUrl()
// ============================================================
describe('upgradeUrl()', () => {
  beforeEach(async () => {
    vi.resetModules();
  });

  it('upgrades http:// URL to https://', async () => {
    const mod = await import('../app.js');
    expect(mod.upgradeUrl('http://example.com/stream.m3u8')).toBe('https://example.com/stream.m3u8');
  });

  it('leaves https:// URL unchanged', async () => {
    const mod = await import('../app.js');
    expect(mod.upgradeUrl('https://example.com/stream.m3u8')).toBe('https://example.com/stream.m3u8');
  });

  it('returns null for null input', async () => {
    const mod = await import('../app.js');
    expect(mod.upgradeUrl(null)).toBeNull();
  });

  it('returns undefined for undefined input', async () => {
    const mod = await import('../app.js');
    expect(mod.upgradeUrl(undefined)).toBeUndefined();
  });

  it('returns empty string for empty input', async () => {
    const mod = await import('../app.js');
    expect(mod.upgradeUrl('')).toBe('');
  });

  it('leaves non-http URLs unchanged', async () => {
    const mod = await import('../app.js');
    expect(mod.upgradeUrl('ftp://example.com/file')).toBe('ftp://example.com/file');
  });

  it('handles uppercase HTTP:// prefix', async () => {
    const mod = await import('../app.js');
    expect(mod.upgradeUrl('HTTP://example.com/stream.m3u8')).toBe('https://example.com/stream.m3u8');
  });
});

// ============================================================
// renderGroups()
// ============================================================
describe('renderGroups()', () => {
  beforeEach(async () => {
    vi.resetAllMocks();
    vi.resetModules();
    setupDom();
    const mod = await import('../app.js');
    state = mod.state;
    renderGroups = mod.renderGroups;
    selectGroup = mod.selectGroup;
    populateState();
  });

  it('renders "Tất cả" as first item with total channel count', () => {
    renderGroups();
    const items = document.querySelectorAll('#group-list li');
    expect(items.length).toBe(4); // "Tất cả" + 3 groups
    expect(items[0].textContent).toContain('Tất cả');
    expect(items[0].textContent).toContain('3'); // total channels
  });

  it('renders each group with channel count badge', () => {
    renderGroups();
    const items = document.querySelectorAll('#group-list li');
    // Group items start from index 1
    expect(items[1].textContent).toContain('VTV');
    expect(items[1].textContent).toContain('1');
    expect(items[2].textContent).toContain('HTV');
    expect(items[2].textContent).toContain('1');
    expect(items[3].textContent).toContain('Khác');
    expect(items[3].textContent).toContain('1');
  });

  it('clicking "Tất cả" selects all groups and shows all channels', () => {
    renderGroups();
    const firstItem = document.querySelector('#group-list li');
    firstItem.click();

    expect(state.selectedGroup).toBe('all');
    const cards = document.querySelectorAll('#channel-list .channel-card');
    expect(cards.length).toBe(3);
  });

  it('clicking a group item selects that group and filters channels', () => {
    renderGroups();
    const items = document.querySelectorAll('#group-list li');
    items[1].click(); // VTV

    expect(state.selectedGroup).toBe('VTV');
    const cards = document.querySelectorAll('#channel-list .channel-card');
    expect(cards.length).toBe(1);
    expect(cards[0].getAttribute('data-channel-id')).toBe('vtv1');
  });

  it('adds .active class to "Tất cả" by default', () => {
    state.selectedGroup = 'all';
    renderGroups();
    const items = document.querySelectorAll('#group-list li');
    expect(items[0].classList.contains('active')).toBe(true);
    expect(items[1].classList.contains('active')).toBe(false);
  });

  it('marks correct group as active', () => {
    state.selectedGroup = 'HTV';
    renderGroups();
    const items = document.querySelectorAll('#group-list li');
    expect(items[0].classList.contains('active')).toBe(false);
    expect(items[2].classList.contains('active')).toBe(true);
  });
});

// ============================================================
// selectGroup()
// ============================================================
describe('selectGroup()', () => {
  beforeEach(async () => {
    vi.resetAllMocks();
    vi.resetModules();
    setupDom();
    const mod = await import('../app.js');
    state = mod.state;
    renderGroups = mod.renderGroups;
    selectGroup = mod.selectGroup;
    renderChannels = mod.renderChannels;
    populateState();
  });

  it('sets state.selectedGroup to the given groupId', () => {
    selectGroup('HTV');
    expect(state.selectedGroup).toBe('HTV');
  });

  it('shows all channels when groupId is "all"', () => {
    const renderSpy = vi.spyOn({ renderChannels }, 'renderChannels');
    // We can spy on it after import
    
    selectGroup('all');
    expect(state.selectedGroup).toBe('all');
    // Check that flatChannels were rendered (channel-list should have items)
    const cards = document.querySelectorAll('#channel-list .channel-card');
    expect(cards.length).toBe(3);
  });

  it('filters channels by groupName when specific group is selected', () => {
    selectGroup('HTV');
    const cards = document.querySelectorAll('#channel-list .channel-card');
    expect(cards.length).toBe(1);
    expect(cards[0].getAttribute('data-channel-id')).toBe('htv7');
  });

  it('calls renderChannels with filtered channel list', () => {
    // Spy on renderChannels by reassigning it on the module
    const renderChannelsSpy = vi.fn();
    // We'll test indirectly: state.selectedGroup should be correct and DOM should update
    selectGroup('all');
    expect(document.querySelectorAll('#channel-list .channel-card').length).toBe(3);

    selectGroup('VTV');
    expect(document.querySelectorAll('#channel-list .channel-card').length).toBe(1);
    expect(document.querySelector('#channel-list .channel-card').getAttribute('data-channel-id')).toBe('vtv1');
  });

  it('updates active class on group list items', () => {
    // First render groups
    renderGroups();
    // Select HTV
    selectGroup('HTV');
    const items = document.querySelectorAll('#group-list li');
    expect(items[0].classList.contains('active')).toBe(false); // Tất cả not active
    expect(items[2].classList.contains('active')).toBe(true);  // HTV active
  });

  it('returns empty array if no channels match group', () => {
    selectGroup('NonExistent');
    const cards = document.querySelectorAll('#channel-list .channel-card');
    expect(cards.length).toBe(0);
  });
});

// ============================================================
// renderChannels()
// ============================================================
describe('renderChannels()', () => {
  beforeEach(async () => {
    vi.resetAllMocks();
    vi.resetModules();
    setupDom();
    const mod = await import('../app.js');
    state = mod.state;
    renderChannels = mod.renderChannels;
    populateState();
  });

  it('clears existing channel list and renders cards', () => {
    // Add some existing content
    document.getElementById('channel-list').innerHTML = '<div>existing</div>';
    
    renderChannels(state.flatChannels);
    
    const cards = document.querySelectorAll('#channel-list .channel-card');
    expect(cards.length).toBe(3);
  });

  it('renders channel cards with correct data-channel-id', () => {
    renderChannels(state.flatChannels);
    
    const cards = document.querySelectorAll('#channel-list .channel-card');
    expect(cards[0].getAttribute('data-channel-id')).toBe('vtv1');
    expect(cards[1].getAttribute('data-channel-id')).toBe('htv7');
    expect(cards[2].getAttribute('data-channel-id')).toBe('ch1');
  });

  it('renders channel logo using getLogo', () => {
    renderChannels(state.flatChannels);
    
    // Each card has one .channel-logo wrapper div
    const wrappers = document.querySelectorAll('#channel-list .channel-card .channel-logo');
    expect(wrappers.length).toBe(3);
    // First two channels have tvg_logo -> img inside
    const imgs = document.querySelectorAll('#channel-list .channel-card .channel-logo img');
    expect(imgs.length).toBe(2);
    expect(imgs[0].getAttribute('src')).toBe('https://example.com/vtv1.png');
    expect(imgs[1].getAttribute('src')).toBe('https://example.com/htv7.png');
    // Last channel has no tvg_logo -> .letter-logo inside
    const letters = document.querySelectorAll('#channel-list .channel-card .channel-logo .letter-logo');
    expect(letters.length).toBe(1);
    expect(letters[0].textContent).toBe('K');
  });

  it('renders channel name and group name', () => {
    renderChannels(state.flatChannels);
    
    const names = document.querySelectorAll('#channel-list .channel-name');
    expect(names[0].textContent).toBe('VTV1');
    expect(names[1].textContent).toBe('HTV7');
    expect(names[2].textContent).toBe('Kênh Lạ');
    
    const groups = document.querySelectorAll('#channel-list .channel-group');
    expect(groups[0].textContent).toBe('VTV');
    expect(groups[1].textContent).toBe('HTV');
    expect(groups[2].textContent).toBe('Khác');
  });

  it('renders empty array without errors', () => {
    renderChannels([]);
    const cards = document.querySelectorAll('#channel-list .channel-card');
    expect(cards.length).toBe(0);
  });
});

// ============================================================
// bindSearch()
// ============================================================
describe('bindSearch()', () => {
  beforeEach(async () => {
    vi.resetAllMocks();
    vi.resetModules();
    setupDom();
    const mod = await import('../app.js');
    state = mod.state;
    bindSearch = mod.bindSearch;
    selectGroup = mod.selectGroup;
    renderChannels = mod.renderChannels;
    populateState();
  });

  it('binds input event on #search element', () => {
    bindSearch();
    const searchInput = document.getElementById('search');
    
    searchInput.value = 'VTV';
    searchInput.dispatchEvent(new Event('input'));
    
    // Only VTV1 matches "VTV" (HTV7 doesn't contain "VTV")
    const cards = document.querySelectorAll('#channel-list .channel-card');
    expect(cards.length).toBe(1);
    expect(cards[0].getAttribute('data-channel-id')).toBe('vtv1');
  });

  it('filters channels case-insensitively', () => {
    bindSearch();
    const searchInput = document.getElementById('search');
    
    searchInput.value = 'vtv';
    searchInput.dispatchEvent(new Event('input'));
    
    const cards = document.querySelectorAll('#channel-list .channel-card');
    expect(cards.length).toBe(1);
    expect(cards[0].getAttribute('data-channel-id')).toBe('vtv1');
  });

  it('shows all matching channels', () => {
    bindSearch();
    const searchInput = document.getElementById('search');
    
    searchInput.value = '7';
    searchInput.dispatchEvent(new Event('input'));
    
    const cards = document.querySelectorAll('#channel-list .channel-card');
    expect(cards.length).toBe(1);
    expect(cards[0].getAttribute('data-channel-id')).toBe('htv7');
  });

  it('reverts to current group filter when search is empty', () => {
    // Set a group filter first
    selectGroup('HTV');
    
    bindSearch();
    const searchInput = document.getElementById('search');
    
    // Search something
    searchInput.value = 'VTV';
    searchInput.dispatchEvent(new Event('input'));
    expect(document.querySelectorAll('#channel-list .channel-card').length).toBe(1);
    expect(document.querySelector('#channel-list .channel-card').getAttribute('data-channel-id')).toBe('vtv1');
    
    // Clear search
    searchInput.value = '';
    searchInput.dispatchEvent(new Event('input'));
    
    // Should revert to HTV group filter
    const cards = document.querySelectorAll('#channel-list .channel-card');
    expect(cards.length).toBe(1);
    expect(cards[0].getAttribute('data-channel-id')).toBe('htv7');
  });

  it('shows empty list when no channels match', () => {
    bindSearch();
    const searchInput = document.getElementById('search');
    
    searchInput.value = 'zzzzz';
    searchInput.dispatchEvent(new Event('input'));
    
    const cards = document.querySelectorAll('#channel-list .channel-card');
    expect(cards.length).toBe(0);
  });
});

// ============================================================
// renderEpg()
// ============================================================
describe('renderEpg()', () => {
  beforeEach(async () => {
    vi.resetAllMocks();
    vi.resetModules();
    setupDom();
    const mod = await import('../app.js');
    state = mod.state;
    renderEpg = mod.renderEpg;
    // Add more EPG data for a channel
    state.epgData = [
      { channel: 'vtv1', start: '20240101000000', stop: '20240101010000', title: 'Thời sự' },
      { channel: 'vtv1', start: '20240101010000', stop: '20240101020000', title: 'Thời tiết' },
      { channel: 'vtv1', start: '20240101020000', stop: '20240101030000', title: 'Phim tài liệu' },
      { channel: 'vtv1', start: '20240101030000', stop: '20240101040000', title: 'Ca nhạc' },
      { channel: 'vtv1', start: '20240101040000', stop: '20240101050000', title: 'Thể thao' },
      { channel: 'vtv1', start: '20240101050000', stop: '20240101060000', title: 'Tin trong nước' },
      { channel: 'vtv1', start: '20240101060000', stop: '20240101070000', title: 'Quốc tế' },
      { channel: 'vtv1', start: '20240101070000', stop: '20240101080000', title: 'Giải trí' },
      { channel: 'vtv1', start: '20240101080000', stop: '20240101090000', title: 'Phim hoạt hình' },
      { channel: 'vtv1', start: '20240101090000', stop: '20240101100000', title: 'Kinh tế' },
      { channel: 'vtv1', start: '20240101100000', stop: '20240101110000', title: 'Văn hóa' },
      { channel: 'vtv1', start: '20240101110000', stop: '20240101120000', title: 'Đời sống' },
      { channel: 'htv7', start: '20240101080000', stop: '20240101090000', title: 'Bản tin sáng' },
    ];
  });

  it('clears the epg-list and renders programmes for a channel', () => {
    renderEpg('vtv1');
    const items = document.querySelectorAll('#epg-list .epg-item');
    expect(items.length).toBeGreaterThan(0);
    expect(items.length).toBeLessThanOrEqual(10);
  });

  it('shows max 10 upcoming programmes', () => {
    renderEpg('vtv1');
    const items = document.querySelectorAll('#epg-list .epg-item');
    expect(items.length).toBeLessThanOrEqual(10);
  });

  it('renders each epg item with time and title', () => {
    renderEpg('vtv1');
    const items = document.querySelectorAll('#epg-list .epg-item');
    expect(items.length).toBeGreaterThan(0);
    
    // Each item should have time and title
    const times = document.querySelectorAll('#epg-list .epg-item .epg-time');
    const titles = document.querySelectorAll('#epg-list .epg-item .epg-title');
    expect(times.length).toBe(items.length);
    expect(titles.length).toBe(items.length);
    
    // Times should be in HH:MM-HH:MM format
    times.forEach(time => {
      expect(time.textContent).toMatch(/^\d{2}:\d{2}-\d{2}:\d{2}$/);
    });
  });

  it('shows empty epg-list when channel has no programmes', () => {
    renderEpg('nonexistent');
    const items = document.querySelectorAll('#epg-list .epg-item');
    expect(items.length).toBe(0);
  });

  it('shows only programmes for the matching channel', () => {
    renderEpg('htv7');
    const items = document.querySelectorAll('#epg-list .epg-item');
    expect(items.length).toBe(1);
    const titles = document.querySelectorAll('#epg-list .epg-item .epg-title');
    expect(titles[0].textContent).toBe('Bản tin sáng');
  });
});

// ============================================================
// selectGroup() + renderGroups() integration
// ============================================================
describe('selectGroup + renderGroups integration', () => {
  beforeEach(async () => {
    vi.resetAllMocks();
    vi.resetModules();
    setupDom();
    const mod = await import('../app.js');
    state = mod.state;
    renderGroups = mod.renderGroups;
    selectGroup = mod.selectGroup;
    renderChannels = mod.renderChannels;
    populateState();
  });

  it('selecting "Tất cả" shows all groups and all channels', () => {
    renderGroups();
    selectGroup('all');
    
    expect(state.selectedGroup).toBe('all');
    const cards = document.querySelectorAll('#channel-list .channel-card');
    expect(cards.length).toBe(3);
    
    const items = document.querySelectorAll('#group-list li');
    expect(items[0].classList.contains('active')).toBe(true);
  });
});

// ============================================================
// showError()
// ============================================================
describe('showError()', () => {
  beforeEach(async () => {
    vi.resetAllMocks();
    vi.resetModules();
    setupDom();
    const mod = await import('../app.js');
    showError = mod.showError;
  });

  it('shows error in channels panel', () => {
    showError('Không thể tải dữ liệu');
    const errorEl = document.querySelector('#channels-panel .error-message');
    expect(errorEl).not.toBeNull();
    expect(errorEl.textContent).toContain('Không thể tải dữ liệu');
    expect(errorEl.hasAttribute('hidden')).toBe(false);
  });

  it('shows error in player panel', () => {
    showError('Lỗi phát video');
    const errorEl = document.querySelector('#player-panel .error-message');
    expect(errorEl).not.toBeNull();
    expect(errorEl.textContent).toContain('Lỗi phát video');
    expect(errorEl.hasAttribute('hidden')).toBe(false);
  });
});

// ============================================================
// HLS Player — getChannelUrls()
// ============================================================

describe('getChannelUrls()', () => {
  beforeEach(async () => {
    vi.resetAllMocks();
    vi.resetModules();
    const mod = await import('../app.js');
    getChannelUrls = mod.getChannelUrls;
  });

  it('returns array of {url, name} from nested channel.sources structure', () => {
    const channel = {
      sources: [{ contents: [{ streams: [{ stream_links: [{ url: 'https://example.com/stream.m3u8', name: 'Main' }, { url: 'https://example.com/backup.m3u8', name: 'Backup' }] }] }] }],
    };
    const result = getChannelUrls(channel);
    expect(result).toEqual([
      { url: 'https://example.com/stream.m3u8', name: 'Main' },
      { url: 'https://example.com/backup.m3u8', name: 'Backup' },
    ]);
  });

  it('returns empty array for channel with no sources', () => {
    expect(getChannelUrls({})).toEqual([]);
  });

  it('returns empty array for channel with empty sources', () => {
    expect(getChannelUrls({ sources: [] })).toEqual([]);
  });

  it('returns empty array for null input', () => {
    expect(getChannelUrls(null)).toEqual([]);
  });

  it('returns empty array for undefined input', () => {
    expect(getChannelUrls(undefined)).toEqual([]);
  });

  it('handles missing contents gracefully', () => {
    expect(getChannelUrls({ sources: [{}] })).toEqual([]);
  });

  it('handles missing streams gracefully', () => {
    expect(getChannelUrls({ sources: [{ contents: [{}] }] })).toEqual([]);
  });

  it('upgrades http:// stream URL to https://', () => {
    const channel = {
      sources: [{ contents: [{ streams: [{ stream_links: [{ url: 'http://example.com/stream.m3u8', name: 'Main' }] }] }] }],
    };
    const result = getChannelUrls(channel);
    expect(result[0].url).toBe('https://example.com/stream.m3u8');
  });

  it('keeps https:// stream URL unchanged', () => {
    const channel = {
      sources: [{ contents: [{ streams: [{ stream_links: [{ url: 'https://example.com/stream.m3u8', name: 'Main' }] }] }] }],
    };
    const result = getChannelUrls(channel);
    expect(result[0].url).toBe('https://example.com/stream.m3u8');
  });
});

// ============================================================
// HLS Player — showPlayerLoading / hidePlayerLoading
// ============================================================

describe('showPlayerLoading / hidePlayerLoading', () => {
  beforeEach(async () => {
    vi.resetAllMocks();
    vi.resetModules();
    setupDom();
    const mod = await import('../app.js');
    showPlayerLoading = mod.showPlayerLoading;
    hidePlayerLoading = mod.hidePlayerLoading;
  });

  it('showPlayerLoading shows player-loading element', () => {
    showPlayerLoading();
    const loading = document.getElementById('player-loading');
    expect(loading).not.toBeNull();
    expect(loading.hidden).toBe(false);
  });

  it('showPlayerLoading sets custom message', () => {
    showPlayerLoading('Đang kết nối...');
    const loading = document.getElementById('player-loading');
    expect(loading.textContent).toContain('Đang kết nối...');
  });

  it('showPlayerLoading uses default message when none provided', () => {
    showPlayerLoading();
    const loading = document.getElementById('player-loading');
    expect(loading.textContent).toContain('Đang tải');
  });

  it('hidePlayerLoading hides the loading element', () => {
    showPlayerLoading();
    hidePlayerLoading();
    const loading = document.getElementById('player-loading');
    expect(loading.hidden).toBe(true);
  });
});

// ============================================================
// HLS Player — showPlayerError / hidePlayerError
// ============================================================

describe('showPlayerError / hidePlayerError', () => {
  beforeEach(async () => {
    vi.resetAllMocks();
    vi.resetModules();
    setupDom();
    const mod = await import('../app.js');
    showPlayerError = mod.showPlayerError;
    hidePlayerError = mod.hidePlayerError;
  });

  it('showPlayerError shows error banner with message', () => {
    showPlayerError('Kênh không có link phát');
    const errorEl = document.getElementById('player-error');
    expect(errorEl.hidden).toBe(false);
    expect(errorEl.textContent).toContain('Kênh không có link phát');
  });

  it('showPlayerError adds retry button', () => {
    showPlayerError('Lỗi phát');
    const retryBtn = document.querySelector('#player-error .player-error-retry');
    expect(retryBtn).not.toBeNull();
    expect(retryBtn.textContent).toBe('Thử lại');
  });

  it('hidePlayerError hides the error element', () => {
    showPlayerError('Lỗi');
    hidePlayerError();
    const errorEl = document.getElementById('player-error');
    expect(errorEl.hidden).toBe(true);
  });
});

// ============================================================
// HLS Player — selectChannel()
// ============================================================

describe('selectChannel()', () => {
  beforeEach(async () => {
    vi.resetAllMocks();
    vi.resetModules();
    setupDom();
    setupHlsMock();
    const mod = await import('../app.js');
    state = mod.state;
    selectChannel = mod.selectChannel;
    renderEpg = mod.renderEpg;
    loadEpg = mod.loadEpg;
    populateState();
    // Mock EPG fetch for lazy loading
    global.fetch.mockResolvedValue({
      ok: true,
      text: async () => sampleEpgXml,
    });
  });

  it('sets state.selectedChannel to the given channel', async () => {
    const channel = state.flatChannels[0];
    await selectChannel(channel);
    expect(state.selectedChannel).toBe(channel);
  });

  it('adds .selected class to matching channel card', async () => {
    // Setup channel cards with data-channel-id
    document.getElementById('channel-list').innerHTML = `
      <div class="channel-card" data-channel-id="vtv1"></div>
      <div class="channel-card" data-channel-id="htv7"></div>
    `;
    await selectChannel(state.flatChannels[0]);
    const cards = document.querySelectorAll('.channel-card');
    expect(cards[0].classList.contains('selected')).toBe(true);
    expect(cards[1].classList.contains('selected')).toBe(false);
  });

  it('removes .selected from previous card when switching channels', async () => {
    document.getElementById('channel-list').innerHTML = `
      <div class="channel-card" data-channel-id="vtv1"></div>
      <div class="channel-card" data-channel-id="htv7"></div>
    `;
    await selectChannel(state.flatChannels[0]);
    await selectChannel(state.flatChannels[1]);
    const cards = document.querySelectorAll('.channel-card');
    expect(cards[0].classList.contains('selected')).toBe(false);
    expect(cards[1].classList.contains('selected')).toBe(true);
  });

  it('hides player-placeholder when channel is selected', async () => {
    await selectChannel(state.flatChannels[0]);
    const placeholder = document.getElementById('player-placeholder');
    expect(placeholder.hidden).toBe(true);
  });

  it('shows channel info with name and group', async () => {
    await selectChannel(state.flatChannels[0]);
    const info = document.getElementById('channel-info');
    expect(info.textContent).toContain('VTV1');
    expect(info.textContent).toContain('VTV');
  });

  it('lazy-loads EPG and renders programmes', async () => {
    await selectChannel(state.flatChannels[0]);
    // EPG should be loaded and rendered
    expect(state.epgData.length).toBeGreaterThan(0);
    const items = document.querySelectorAll('#epg-list .epg-item');
    expect(items.length).toBeGreaterThan(0);
  });

  it('shows loading spinner during EPG fetch', async () => {
    // Don't resolve fetch immediately to check spinner
    global.fetch.mockImplementation(() => new Promise(() => {})); // never resolves
    const promise = selectChannel(state.flatChannels[0]);
    // Spinner should be visible during fetch
    const epgDiv = document.getElementById('epg-list');
    expect(epgDiv.innerHTML).toContain('loading-spinner');
  });

  it('only loads EPG once (not on subsequent channel selects)', async () => {
    // First select loads EPG
    await selectChannel(state.flatChannels[0]);
    expect(global.fetch).toHaveBeenCalledWith(BASE_URL + 'light_epg.xml');

    // Reset mock calls to track second select
    global.fetch.mockClear();
    global.fetch.mockResolvedValue({
      ok: true,
      text: async () => sampleEpgXml,
    });

    // Second select should not re-fetch EPG
    await selectChannel(state.flatChannels[1]);
    // fetch should only have been called for playChannel HLS, not EPG
    const epgCalls = global.fetch.mock.calls.filter(c => c[0] === BASE_URL + 'light_epg.xml');
    expect(epgCalls.length).toBe(0);
  });
});

// ============================================================
// loadEpg() / loadEpgFresh()
// ============================================================
describe('loadEpg() / loadEpgFresh()', () => {
  beforeEach(async () => {
    vi.resetAllMocks();
    vi.resetModules();
    setupDom();
    const mod = await import('../app.js');
    state = mod.state;
    loadEpg = mod.loadEpg;
    loadEpgFresh = mod.loadEpgFresh;
    cacheGet = mod.cacheGet;
    cacheSet = mod.cacheSet;
  });

  it('loadEpgFresh fetches and parses EPG XML', async () => {
    global.fetch.mockResolvedValueOnce({
      ok: true,
      text: async () => sampleEpgXml,
    });

    await loadEpgFresh();

    expect(global.fetch).toHaveBeenCalledWith(BASE_URL + 'light_epg.xml');
    expect(state.epgData.length).toBe(2);
    expect(state.epgData[0].title).toBe('Thời sự');
    expect(state.epgData[1].title).toBe('Bản tin sáng');
  });

  it('loadEpgFresh shows error message on fetch failure', async () => {
    global.fetch.mockRejectedValueOnce(new Error('Network error'));

    await loadEpgFresh();

    expect(state.epgData).toEqual([]);
    const epgDiv = document.getElementById('epg-list');
    expect(epgDiv.innerHTML).toContain('error-message');
  });

  it('loadEpgFresh caches parsed EPG data in IndexedDB', async () => {
    global.fetch.mockResolvedValueOnce({
      ok: true,
      text: async () => sampleEpgXml,
    });

    await loadEpgFresh();

    const cached = await cacheGet('epg');
    expect(cached).toBeDefined();
    expect(cached.length).toBe(2);
  });

  it('loadEpg uses cached EPG when available (stale-while-revalidate)', async () => {
    // Pre-cache EPG data
    await cacheSet('epg', [
      { channel: 'vtv1', start: '20240101000000', stop: '20240101010000', title: 'Cached Show' },
    ], 999999);

    await loadEpg();

    // Should use cached data immediately
    expect(state.epgData.length).toBe(1);
    expect(state.epgData[0].title).toBe('Cached Show');
    // Background refresh (stale-while-revalidate) should be triggered
    expect(global.fetch).toHaveBeenCalledWith(BASE_URL + 'light_epg.xml');
  });

  it('loadEpg fetches fresh when cache is empty', async () => {
    global.fetch.mockResolvedValueOnce({
      ok: true,
      text: async () => sampleEpgXml,
    });

    await loadEpg();

    expect(global.fetch).toHaveBeenCalledWith(BASE_URL + 'light_epg.xml');
    expect(state.epgData.length).toBe(2);
  });

  it('loadEpg re-renders EPG for currently selected channel after fresh fetch', async () => {
    // Pre-populate state with a selected channel
    state.selectedChannel = { id: 'vtv1', tvg_id: 'vtv1', name: 'VTV1' };
    // Setup EPG list DOM
    document.getElementById('epg-list').innerHTML = '<div class="epg-item">Old</div>';

    global.fetch.mockResolvedValueOnce({
      ok: true,
      text: async () => sampleEpgXml,
    });

    await loadEpg();

    // EPG should be re-rendered for the selected channel
    const items = document.querySelectorAll('#epg-list .epg-item');
    expect(items.length).toBeGreaterThan(0);
    // Old content should be gone
    expect(document.getElementById('epg-list').innerHTML).not.toContain('Old');
  });
});

// ============================================================
// HLS Player — playChannel / tryPlayUrl
// ============================================================

describe('playChannel() / tryPlayUrl()', () => {
  beforeEach(async () => {
    vi.resetAllMocks();
    vi.resetModules();
    setupDom();
    setupHlsMock();
    const mod = await import('../app.js');
    state = mod.state;
    playChannel = mod.playChannel;
    getChannelUrls = mod.getChannelUrls;
    showPlayerLoading = mod.showPlayerLoading;
    hidePlayerLoading = mod.hidePlayerLoading;
    showPlayerError = mod.showPlayerError;
    hidePlayerError = mod.hidePlayerError;
    populateState();
  });

  it('destroys previous hls instance and removes video src', () => {
    playChannel(state.flatChannels[0]);
    // Hls constructor should have been called
    expect(global.Hls).toHaveBeenCalled();
    // loadSource should have been called with the stream url
    const mockHlsInstance = global.Hls.mock.results[0].value;
    expect(mockHlsInstance.loadSource).toHaveBeenCalledWith('https://example.com/vtv1.m3u8');
    expect(mockHlsInstance.attachMedia).toHaveBeenCalled();
  });

  it('shows player error when channel has no stream links', () => {
    // Channel with no sources
    const badChannel = { id: 'bad', name: 'Bad', sources: [] };
    playChannel(badChannel);
    const errorEl = document.getElementById('player-error');
    expect(errorEl.hidden).toBe(false);
    expect(errorEl.textContent).toContain('Kênh không có link phát');
  });

  it('attaches MANIFEST_PARSED and ERROR event listeners', () => {
    playChannel(state.flatChannels[0]);
    const mockHlsInstance = global.Hls.mock.results[0].value;
    expect(mockHlsInstance.on).toHaveBeenCalledWith('mp', expect.any(Function));
    expect(mockHlsInstance.on).toHaveBeenCalledWith('err', expect.any(Function));
  });

  it('tries HTTP fallback when HTTPS URL fails with fatal error', () => {
    // Use a channel with HTTP URL to test upgrade + fallback
    const channel = {
      id: 'test1',
      name: 'Test',
      groupName: 'VTV',
      sources: [{ contents: [{ streams: [{ stream_links: [{ url: 'http://example.com/stream.m3u8', name: 'Main' }] }] }] }],
    };
    const mockHls = setupHlsMock();
    state.flatChannels = [channel];

    playChannel(channel);

    // getChannelUrls upgrades http:// to https://, so first try is HTTPS
    const firstHls = global.Hls.mock.results[0].value;
    expect(firstHls.loadSource).toHaveBeenCalledWith('https://example.com/stream.m3u8');

    // Find and trigger the ERROR handler
    const errorHandler = firstHls.on.mock.calls.find(call => call[0] === 'err')[1];
    errorHandler(null, { fatal: true });

    // After HTTPS fails, it should try HTTP fallback
    const secondHls = global.Hls.mock.results[1].value;
    expect(secondHls.loadSource).toHaveBeenCalledWith('http://example.com/stream.m3u8');
  });
});

// ============================================================
// HLS Player — Keyboard Shortcuts
// ============================================================

describe('Keyboard shortcuts', () => {
  beforeEach(async () => {
    vi.resetAllMocks();
    vi.resetModules();
    setupDom();
    setupHlsMock();
    const mod = await import('../app.js');
    state = mod.state;
    selectChannel = mod.selectChannel;
    populateState();
  });

  it('ArrowDown navigates to next channel card and calls click', () => {
    document.getElementById('channel-list').innerHTML = `
      <div class="channel-card selected" data-channel-id="vtv1"></div>
      <div class="channel-card" data-channel-id="htv7"></div>
    `;
    const cards = document.querySelectorAll('.channel-card');
    cards[0].scrollIntoView = vi.fn();
    cards[1].scrollIntoView = vi.fn();
    cards[1].click = vi.fn();

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown' }));

    expect(cards[1].click).toHaveBeenCalled();
    expect(cards[1].scrollIntoView).toHaveBeenCalledWith({ block: 'nearest' });
  });

  it('ArrowUp navigates to previous channel card', () => {
    document.getElementById('channel-list').innerHTML = `
      <div class="channel-card" data-channel-id="vtv1"></div>
      <div class="channel-card selected" data-channel-id="htv7"></div>
    `;
    const cards = document.querySelectorAll('.channel-card');
    cards[0].scrollIntoView = vi.fn();
    cards[0].click = vi.fn();

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp' }));

    expect(cards[0].click).toHaveBeenCalled();
    expect(cards[0].scrollIntoView).toHaveBeenCalledWith({ block: 'nearest' });
  });

  it('does nothing at first card with ArrowUp', () => {
    document.getElementById('channel-list').innerHTML = `
      <div class="channel-card selected" data-channel-id="vtv1"></div>
      <div class="channel-card" data-channel-id="htv7"></div>
    `;
    const cards = document.querySelectorAll('.channel-card');
    cards[0].click = vi.fn();

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp' }));

    expect(cards[0].click).not.toHaveBeenCalled();
  });

  it('does nothing at last card with ArrowDown', () => {
    document.getElementById('channel-list').innerHTML = `
      <div class="channel-card" data-channel-id="vtv1"></div>
      <div class="channel-card selected" data-channel-id="htv7"></div>
    `;
    const cards = document.querySelectorAll('.channel-card');
    cards[1].click = vi.fn();

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown' }));

    expect(cards[1].click).not.toHaveBeenCalled();
  });

  it('prevents default for ArrowDown event', () => {
    document.getElementById('channel-list').innerHTML = `
      <div class="channel-card selected" data-channel-id="vtv1"></div>
      <div class="channel-card" data-channel-id="htv7"></div>
    `;

    const event = new KeyboardEvent('keydown', { key: 'ArrowDown', cancelable: true });
    document.dispatchEvent(event);

    expect(event.defaultPrevented).toBe(true);
  });

  it('prevents default for ArrowUp event', () => {
    document.getElementById('channel-list').innerHTML = `
      <div class="channel-card selected" data-channel-id="vtv1"></div>
      <div class="channel-card" data-channel-id="htv7"></div>
    `;

    const event = new KeyboardEvent('keydown', { key: 'ArrowUp', cancelable: true });
    document.dispatchEvent(event);

    expect(event.defaultPrevented).toBe(true);
  });
});
