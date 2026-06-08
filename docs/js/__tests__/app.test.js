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
let state, BASE_URL, GROUP_ORDER, loadData, initTheme, renderGroups, selectGroup, bindSearch, showError;

beforeEach(async () => {
  vi.resetAllMocks();
  mockLocalStorage.clear();

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
  it('fetches channels.json and light_epg.xml in parallel', async () => {
    global.fetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => sampleChannelsJson,
      })
      .mockResolvedValueOnce({
        ok: true,
        text: async () => sampleEpgXml,
      });

    await loadData();

    // Both URLs were fetched
    expect(global.fetch).toHaveBeenCalledTimes(2);
    expect(global.fetch).toHaveBeenCalledWith(BASE_URL + 'channels.json');
    expect(global.fetch).toHaveBeenCalledWith(BASE_URL + 'light_epg.xml');

    // State updated
    expect(state.isLoading).toBe(false);
    expect(state.error).toBeNull();
    expect(state.groups.length).toBe(3);
    expect(state.flatChannels.length).toBe(3);
    expect(state.epgData.length).toBe(2);
  });

  it('populates flatChannels with groupName', async () => {
    global.fetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => sampleChannelsJson,
      })
      .mockResolvedValueOnce({
        ok: true,
        text: async () => sampleEpgXml,
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
    global.fetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => sampleChannelsJson,
      })
      .mockResolvedValueOnce({
        ok: true,
        text: async () => sampleEpgXml,
      });

    await loadData();

    // VTV should come before HTV (GROUP_ORDER ordering)
    expect(state.groups[0].name).toBe('VTV');
    expect(state.groups[1].name).toBe('HTV');
    // 'Khác' is not in GROUP_ORDER, sorted alphabetically at end
    expect(state.groups[state.groups.length - 1].name).toBe('Khác');
  });

  it('parses EPG XML correctly', async () => {
    global.fetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => sampleChannelsJson,
      })
      .mockResolvedValueOnce({
        ok: true,
        text: async () => sampleEpgXml,
      });

    await loadData();

    expect(state.epgData).toHaveLength(2);
    expect(state.epgData[0]).toEqual({
      channel: 'vtv1',
      start: '20240101000000',
      stop: '20240101010000',
      title: 'Thời sự',
    });
    expect(state.epgData[1]).toEqual({
      channel: 'htv7',
      start: '20240101080000',
      stop: '20240101090000',
      title: 'Bản tin sáng',
    });
  });

  it('sets error if channels.json fetch fails', async () => {
    global.fetch
      .mockResolvedValueOnce({
        ok: false,
        status: 404,
        statusText: 'Not Found',
      })
      .mockResolvedValueOnce({
        ok: true,
        text: async () => sampleEpgXml,
      });

    await loadData();

    expect(state.error).toBe('Không thể tải danh sách kênh');
    expect(state.isLoading).toBe(false);
    expect(state.groups).toEqual([]);
  });

  it('handles EPG failure gracefully (non-fatal)', async () => {
    global.fetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => sampleChannelsJson,
      })
      .mockRejectedValueOnce(new Error('EPG network error'));

    await loadData();

    // EPG error should not set state.error
    expect(state.error).toBeNull();
    expect(state.isLoading).toBe(false);
    // Console.warn should have been called
    expect(global.console.warn).toHaveBeenCalled();
    // EPG data should be empty
    expect(state.epgData).toEqual([]);
  });

  it('handles JSON parse error in channels', async () => {
    global.fetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => { throw new Error('Invalid JSON'); },
      })
      .mockResolvedValueOnce({
        ok: true,
        text: async () => sampleEpgXml,
      });

    await loadData();

    expect(state.error).toBe('Không thể tải danh sách kênh');
    expect(state.isLoading).toBe(false);
  });

  it('sets isLoading to false after loadData completes', async () => {
    global.fetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => sampleChannelsJson,
      })
      .mockResolvedValueOnce({
        ok: true,
        text: async () => sampleEpgXml,
      });

    expect(state.isLoading).toBe(true);
    await loadData();
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
