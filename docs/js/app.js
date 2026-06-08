// ============================================================
// Mix IPTV — Data loading + State management
// ============================================================

// ----- Constants & Config -----
const BASE_URL = 'https://raw.githubusercontent.com/hoangxg4/mix-iptv/main/';
const GROUP_ORDER = [
  'VTV', 'HTV', 'VTC', 'VTVCAB / ON', 'VTVPRIME',
  'K+', 'THỂ THAO', 'PHIM TRUYỆN', 'QUỐC TẾ', 'ĐỊA PHƯƠNG',
];

// ----- State -----
const state = {
  groups: [],           // [{id, name, channels[]}] from JSON provider.groups
  flatChannels: [],     // [{...channel, groupName}] flattened for search
  epgData: [],          // [{channel, start, stop, title}] parsed from EPG XML
  selectedGroup: 'all',
  selectedChannel: null,
  isDarkMode: localStorage.getItem('theme') === 'dark',
  isLoading: true,
  error: null,          // string or null
};

// ----- Data Loading -----
async function loadData() {
  // Start both fetches in parallel
  const channelsPromise = fetch(BASE_URL + 'channels.json');
  const epgPromise = fetch(BASE_URL + 'light_epg.xml');

  // Handle channels.json (fatal if fails)
  try {
    const channelsRes = await channelsPromise;
    if (!channelsRes.ok) {
      throw new Error(`HTTP ${channelsRes.status}`);
    }
    const provider = await channelsRes.json();
    state.groups = provider.groups;
    state.flatChannels = provider.groups.flatMap(g =>
      g.channels.map(ch => ({ ...ch, groupName: g.name }))
    );

    // Sort groups by GROUP_ORDER (known groups first, then alphabetical 'Khác' etc.)
    sortGroups();
  } catch (err) {
    state.error = 'Không thể tải danh sách kênh';
    state.isLoading = false;
    return;
  }

  // Handle EPG XML (non-fatal if fails)
  try {
    const epgRes = await epgPromise;
    if (!epgRes.ok) {
      console.warn('EPG fetch failed:', epgRes.status);
      state.epgData = [];
    } else {
      const xmlText = await epgRes.text();
      const parser = new DOMParser();
      const xml = parser.parseFromString(xmlText, 'text/xml');
      state.epgData = Array.from(xml.querySelectorAll('programme')).map(prog => ({
        channel: prog.getAttribute('channel'),
        start: prog.getAttribute('start'),
        stop: prog.getAttribute('stop'),
        title: prog.querySelector('title')?.textContent || '',
      }));
    }
  } catch (err) {
    console.warn('EPG loading failed:', err);
    state.epgData = [];
  }

  state.isLoading = false;
}

// Sort groups: known groups from GROUP_ORDER first (in order), then alphabetical
function sortGroups() {
  const orderMap = {};
  GROUP_ORDER.forEach((name, index) => {
    orderMap[name] = index;
  });

  state.groups.sort((a, b) => {
    const aOrder = orderMap[a.name];
    const bOrder = orderMap[b.name];

    if (aOrder !== undefined && bOrder !== undefined) {
      return aOrder - bOrder;
    }
    if (aOrder !== undefined) return -1;
    if (bOrder !== undefined) return 1;

    // Both not in GROUP_ORDER — alphabetical
    return a.name.localeCompare(b.name, 'vi');
  });
}

// ----- Theme -----
function initTheme() {
  const theme = localStorage.getItem('theme');
  if (theme === 'dark') {
    document.documentElement.setAttribute('data-theme', 'dark');
  } else {
    document.documentElement.removeAttribute('data-theme');
  }
}

// ----- Stub functions (implemented in future tasks) -----
function renderGroups() {}
function selectGroup(groupId) {}
function bindSearch() {}
function showError(message) {}

// ----- Init -----
// Only auto-init in browser environments where document exists
if (typeof document !== 'undefined') {
  document.addEventListener('DOMContentLoaded', async () => {
    initTheme();
    await loadData();
    if (!state.error) {
      renderGroups();
      selectGroup('all');
      bindSearch();
    } else {
      showError(state.error);
    }
  });
}

// ----- Exports for testing -----
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    state,
    BASE_URL,
    GROUP_ORDER,
    loadData,
    initTheme,
    renderGroups,
    selectGroup,
    bindSearch,
    showError,
  };
}
