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

// ----- Helper Functions -----

/**
 * Return logo HTML: <img> if channel has tvg_logo, otherwise a colored circle with first letter.
 */
function getLogo(channel) {
  if (channel.tvg_logo) {
    return `<img src="${channel.tvg_logo}" alt="${channel.name}">`;
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
  return links.map(l => ({ url: l.url, name: l.name }));
}

let hls = null;
const video = typeof document !== 'undefined' ? document.getElementById('video-player') : null;

function selectChannel(channel) {
  state.selectedChannel = channel;

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
        tryPlayUrl(links, index + 1);
      }
    });
  } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
    // Native HLS (Safari)
    video.src = url;
    video.addEventListener('loadedmetadata', () => hidePlayerLoading());
    video.addEventListener('error', () => tryPlayUrl(links, index + 1));
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
  };
}
