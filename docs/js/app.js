// ============================================================
// Mix IPTV — Data loading + State management
// ============================================================

// ----- Constants & Config -----
const BASE_URL = 'https://raw.githubusercontent.com/hoangxg4/mix-iptv/main/';
const GROUP_ORDER = [
  'VTV', 'HTV', 'VTC', 'VTVCAB / ON', 'VTVPRIME',
  'K+', 'THỂ THAO', 'PHIM TRUYỆN', 'QUỐC TẾ', 'ĐỊA PHƯƠNG',
];

// ----- Simple IndexedDB Cache -----
const DB_NAME = 'mix-iptv-cache';
const DB_VERSION = 1;
const CACHE_STORE = 'cache';

function openDB() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      request.result.createObjectStore(CACHE_STORE);
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function cacheGet(key) {
  try {
    const db = await openDB();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(CACHE_STORE, 'readonly');
      const req = tx.objectStore(CACHE_STORE).get(key);
      req.onsuccess = () => {
        const entry = req.result;
        if (entry && entry.expires > Date.now()) {
          resolve(entry.data);
        } else {
          resolve(null);
        }
      };
      req.onerror = () => resolve(null);
    });
  } catch { return null; }
}

async function cacheSet(key, data, ttlMs = 30 * 60 * 1000) {
  try {
    const db = await openDB();
    const tx = db.transaction(CACHE_STORE, 'readwrite');
    tx.objectStore(CACHE_STORE).put({ data, expires: Date.now() + ttlMs }, key);
  } catch { /* silent fail */ }
}

const CACHE_TTL_CHANNELS = 30 * 60 * 1000;  // 30 min
const CACHE_TTL_EPG = 30 * 60 * 1000;       // 30 min

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
  // Try IndexedDB cache first
  const cached = await cacheGet('channels');
  if (cached) {
    state.groups = cached.groups;
    state.flatChannels = cached.flatChannels;
    state.isLoading = false;
    renderGroups();
    selectGroup('all');
    bindSearch();
    // Re-fetch in background (stale-while-revalidate)
    loadChannelsFresh().catch(() => {});
  } else {
    // No cache — show loading and fetch
    try {
      await loadChannelsFresh();
    } catch (err) {
      state.error = 'Không thể tải danh sách kênh';
      state.isLoading = false;
    }
  }
}

async function loadChannelsFresh() {
  const res = await fetch(BASE_URL + 'channels.json');
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const provider = await res.json();
  const groups = provider.groups;
  const flatChannels = provider.groups.flatMap(g =>
    g.channels.map(ch => ({ ...ch, groupName: g.name }))
  );

  // Cache in IndexedDB
  await cacheSet('channels', { groups, flatChannels }, CACHE_TTL_CHANNELS);

  // Update state
  state.groups = groups;
  state.flatChannels = flatChannels;
  sortGroups();
  state.isLoading = false;

  // Re-render if initial render already happened (guard for SSR/test env)
  const groupList = document.querySelector('#group-list');
  if (groupList && groupList.children.length > 0) {
    renderGroups();
    selectGroup(state.selectedGroup);
  } else {
    renderGroups();
    selectGroup('all');
    bindSearch();
  }
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

// ----- Helper Functions -----

/**
 * Upgrade a URL from http:// to https://. Leaves other URLs unchanged.
 */
function upgradeUrl(url) {
  if (!url) return url;
  return url.replace(/^http:\/\//i, 'https://');
}

/**
 * Return logo HTML: <img> if channel has tvg_logo, otherwise a colored circle with first letter.
 */
function getLogo(channel) {
  if (channel.tvg_logo) {
    return `<img src="${upgradeUrl(channel.tvg_logo)}" alt="${channel.name}">`;
  }
  const letter = channel.name ? channel.name.charAt(0).toUpperCase() : '?';
  return `<span class="letter-logo">${letter}</span>`;
}

/**
 * Parse EPG time string (YYYYMMDDHHMMSS +ZZZZ) into a Date object.
 */
function parseEpgTime(t) {
  const match = t.match(/^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})/);
  if (!match) return null;
  return new Date(`${match[1]}-${match[2]}-${match[3]}T${match[4]}:${match[5]}:${match[6]}`);
}

// ----- Rendering Functions -----

function renderGroups() {
  const list = document.getElementById('group-list');
  if (!list) return;

  list.innerHTML = '';

  const totalChannels = state.flatChannels.length;
  const allItem = document.createElement('li');
  allItem.textContent = `Tất cả (${totalChannels})`;
  allItem.classList.toggle('active', state.selectedGroup === 'all');
  allItem.addEventListener('click', () => selectGroup('all'));
  list.appendChild(allItem);

  state.groups.forEach(group => {
    const li = document.createElement('li');
    li.textContent = `${group.name} (${group.channels.length})`;
    li.classList.toggle('active', state.selectedGroup === group.name);
    li.addEventListener('click', () => selectGroup(group.name));
    list.appendChild(li);
  });
}

function selectGroup(groupId) {
  state.selectedGroup = groupId;

  let filtered;
  if (groupId === 'all') {
    filtered = state.flatChannels;
  } else {
    filtered = state.flatChannels.filter(ch => ch.groupName === groupId);
  }

  // Update group list active state
  const items = document.querySelectorAll('#group-list li');
  items.forEach(item => {
    // First item is "Tất cả", rest are groups
    const isAll = item.textContent.startsWith('Tất cả');
    if ((isAll && groupId === 'all') || (!isAll && item.textContent.startsWith(groupId))) {
      item.classList.add('active');
    } else {
      item.classList.remove('active');
    }
  });

  renderChannels(filtered);
}

function renderChannels(channelList) {
  const container = document.getElementById('channel-list');
  if (!container) return;

  container.innerHTML = '';

  channelList.forEach(channel => {
    const card = document.createElement('div');
    card.className = 'channel-card';
    card.setAttribute('data-channel-id', channel.id);

    card.innerHTML = `
      <div class="channel-logo">${getLogo(channel)}</div>
      <div class="channel-info">
        <div class="channel-name">${channel.name}</div>
        <div class="channel-group">${channel.groupName}</div>
      </div>
    `;

    if (state.selectedChannel && state.selectedChannel.id === channel.id) {
      card.classList.add('selected');
    }

    card.addEventListener('click', () => selectChannel(channel));
    container.appendChild(card);
  });
}

function bindSearch() {
  const searchInput = document.getElementById('search');
  if (!searchInput) return;

  searchInput.addEventListener('input', () => {
    const query = searchInput.value.trim().toLowerCase();
    if (!query) {
      // Revert to current group filter
      selectGroup(state.selectedGroup);
      return;
    }

    const filtered = state.flatChannels.filter(ch =>
      ch.name.toLowerCase().includes(query)
    );
    renderChannels(filtered);
  });
}

function renderEpg(channelId) {
  const epgList = document.getElementById('epg-list');
  if (!epgList) return;

  epgList.innerHTML = '';

  const now = new Date();
  const programmes = state.epgData
    .filter(p => p.channel === channelId)
    .map(p => ({
      ...p,
      startDate: parseEpgTime(p.start),
      stopDate: parseEpgTime(p.stop),
    }))
    .filter(p => p.startDate && p.stopDate)
    .sort((a, b) => a.startDate - b.startDate);

  // Find currently airing programme
  let currentIndex = -1;
  for (let i = 0; i < programmes.length; i++) {
    if (now >= programmes[i].startDate && now < programmes[i].stopDate) {
      currentIndex = i;
      break;
    }
  }

  // Show from current programme or first upcoming, max 10 items
  const startIdx = currentIndex >= 0 ? currentIndex : 0;
  const visible = programmes.slice(startIdx, startIdx + 10);

  visible.forEach((prog, i) => {
    const item = document.createElement('div');
    item.className = 'epg-item';
    if (currentIndex >= 0 && startIdx + i === currentIndex) {
      item.classList.add('current');
    }

    const startStr = prog.startDate.toTimeString().slice(0, 5);
    const stopStr = prog.stopDate.toTimeString().slice(0, 5);

    item.innerHTML = `
      <span class="epg-time">${startStr}-${stopStr}</span>
      <span class="epg-title">${prog.title}</span>
    `;
    epgList.appendChild(item);
  });
}

function showError(message) {
  const errorEls = document.querySelectorAll('.error-message');
  errorEls.forEach(el => {
    el.textContent = message;
    el.removeAttribute('hidden');
  });
}

// ----- HLS Player Functions -----

/**
 * Navigate channel.sources[0].contents[0].streams[0].stream_links
 * and return array of {url, name} objects.
 */
function getChannelUrls(channel) {
  if (!channel) return [];
  const links = channel.sources?.[0]?.contents?.[0]?.streams?.[0]?.stream_links;
  if (!links) return [];
  return links.map(l => ({ url: upgradeUrl(l.url), name: l.name }));
}

let hls = null;
let epgLoaded = false;
const video = typeof document !== 'undefined' ? document.getElementById('video-player') : null;

async function selectChannel(channel) {
  state.selectedChannel = channel;

  // Lazy load EPG if not yet loaded
  if (!epgLoaded) {
    const epgDiv = document.getElementById('epg-list');
    if (epgDiv) epgDiv.innerHTML = '<div class="loading-spinner">Đang tải EPG...</div>';
    await loadEpg();
    epgLoaded = true;
  }

  // Update active state in channel list
  document.querySelectorAll('.channel-card').forEach(card => {
    if (card.dataset.channelId === channel.id) {
      card.classList.add('selected');
    } else {
      card.classList.remove('selected');
    }
  });

  // Hide placeholder, show video player
  const placeholder = document.getElementById('player-placeholder');
  if (placeholder) placeholder.hidden = true;

  // Show channel info: name + group
  const info = document.getElementById('channel-info');
  if (info) info.textContent = `${channel.name} - ${channel.groupName}`;

  playChannel(channel);
  renderEpg(channel.tvg_id || channel.id);
}

async function loadEpg() {
  // Try cache first
  const cached = await cacheGet('epg');
  if (cached) {
    state.epgData = cached;
    // Re-fetch in background
    loadEpgFresh().catch(() => {});
    return;
  }
  await loadEpgFresh();
}

async function loadEpgFresh() {
  const epgDiv = document.getElementById('epg-list');

  try {
    const res = await fetch(BASE_URL + 'light_epg.xml');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const xmlText = await res.text();
    const parser = new DOMParser();
    const xml = parser.parseFromString(xmlText, 'text/xml');
    const programmes = Array.from(xml.querySelectorAll('programme')).map(prog => ({
      channel: prog.getAttribute('channel'),
      start: prog.getAttribute('start'),
      stop: prog.getAttribute('stop'),
      title: prog.querySelector('title')?.textContent || '',
    }));

    state.epgData = programmes;
    await cacheSet('epg', programmes, CACHE_TTL_EPG);

    // If a channel is already selected, re-render EPG
    if (state.selectedChannel) {
      renderEpg(state.selectedChannel.tvg_id || state.selectedChannel.id);
    }
  } catch (err) {
    console.warn('EPG loading failed:', err);
    if (epgDiv) {
      epgDiv.innerHTML = '<div class="error-message">Không thể tải lịch EPG</div>';
    }
    // Only clear data when called as initial load (no prior data)
    // When called as background refresh, preserve existing cached data
    if (state.epgData.length === 0) {
      state.epgData = [];
    }
  }
}

function playChannel(channel) {
  // Cleanup previous
  if (hls) { hls.destroy(); hls = null; }
  if (video) video.removeAttribute('src');

  const links = getChannelUrls(channel);
  if (links.length === 0) {
    showPlayerError('Kênh không có link phát');
    return;
  }

  hidePlayerError();
  showPlayerLoading();

  tryPlayUrl(links, 0);
}

function tryPlayUrl(links, index) {
  if (index >= links.length) {
    showPlayerError('Tất cả link đều không hoạt động');
    return;
  }

  const url = links[index].url;
  hidePlayerError();

  // For HTTPS URLs, prepare HTTP fallback to try if HTTPS fails
  const httpFallbackUrl = url.startsWith('https://') ? url.replace(/^https:\/\//i, 'http://') : null;

  function tryNext() {
    // Insert HTTP fallback as next to try before moving to the next link
    if (httpFallbackUrl) {
      const newLinks = [...links];
      newLinks.splice(index + 1, 0, { url: httpFallbackUrl, name: links[index].name + ' (HTTP)' });
      tryPlayUrl(newLinks, index + 1);
    } else {
      tryPlayUrl(links, index + 1);
    }
  }

  if (typeof Hls !== 'undefined' && Hls.isSupported()) {
    hls = new Hls({
      enableWorker: true,
      lowLatencyMode: true,
    });
    hls.loadSource(url);
    hls.attachMedia(video);
    hls.on(Hls.Events.MANIFEST_PARSED, () => {
      hidePlayerLoading();
      video.play().catch(() => {});
    });
    hls.on(Hls.Events.ERROR, (event, data) => {
      if (data.fatal) {
        console.warn(`HLS error on ${url}, trying fallback...`);
        hls.destroy();
        hls = null;
        tryNext();
      }
    });
  } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
    // Native HLS (Safari)
    video.src = url;
    video.addEventListener('loadedmetadata', () => hidePlayerLoading());
    video.addEventListener('error', () => tryNext());
  } else {
    showPlayerError('Trình duyệt không hỗ trợ HLS');
  }
}

function showPlayerLoading(msg) {
  const el = document.getElementById('player-loading');
  if (!el) return;
  el.textContent = msg || 'Đang tải...';
  el.hidden = false;
}

function hidePlayerLoading() {
  const el = document.getElementById('player-loading');
  if (el) el.hidden = true;
}

function showPlayerError(msg) {
  const el = document.getElementById('player-error');
  if (!el) return;
  el.innerHTML = `${msg} <button class="player-error-retry">Thử lại</button>`;
  el.hidden = false;
  const retryBtn = el.querySelector('.player-error-retry');
  if (retryBtn) {
    retryBtn.onclick = () => selectChannel(state.selectedChannel);
  }
}

function hidePlayerError() {
  const el = document.getElementById('player-error');
  if (el) el.hidden = true;
}

// ----- Keyboard Shortcuts -----
if (typeof document !== 'undefined') {
  document.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
      const cards = document.querySelectorAll('.channel-card');
      const activeIdx = Array.from(cards).findIndex(c => c.classList.contains('selected'));
      let nextIdx;
      if (e.key === 'ArrowUp') nextIdx = Math.max(0, activeIdx - 1);
      else nextIdx = Math.min(cards.length - 1, activeIdx + 1);
      if (nextIdx !== activeIdx && cards[nextIdx]) {
        cards[nextIdx].click();
        cards[nextIdx].scrollIntoView({ block: 'nearest' });
      }
      e.preventDefault();
    }
    if (e.key === ' ' && state.selectedChannel) {
      e.preventDefault();
      if (video && video.paused) video.play();
      else if (video) video.pause();
    }
  });
}

// ----- Init -----
// Only auto-init in browser environments where document exists
if (typeof document !== 'undefined') {
  document.addEventListener('DOMContentLoaded', async () => {
    // Create player-loading element if not present in HTML
    if (!document.getElementById('player-loading')) {
      const loading = document.createElement('div');
      loading.id = 'player-loading';
      loading.hidden = true;
      loading.textContent = 'Đang tải...';
      const container = document.getElementById('video-container');
      if (container) container.appendChild(loading);
    }

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
    upgradeUrl,
    state,
    BASE_URL,
    GROUP_ORDER,
    loadData,
    initTheme,
    renderGroups,
    selectGroup,
    bindSearch,
    showError,
    renderChannels,
    renderEpg,
    getLogo,
    parseEpgTime,
    selectChannel,
    getChannelUrls,
    playChannel,
    tryPlayUrl,
    showPlayerLoading,
    hidePlayerLoading,
    showPlayerError,
    hidePlayerError,
    openDB,
    cacheGet,
    cacheSet,
    CACHE_TTL_CHANNELS,
    CACHE_TTL_EPG,
    loadChannelsFresh,
    loadEpg,
    loadEpgFresh,
  };
}
