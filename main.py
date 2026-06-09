#!/usr/bin/env python3
"""IPTV Playlist Generator - Async version with config and caching."""
import asyncio
import hashlib
import json
import logging
import os
import re
import gzip
import xml.etree.ElementTree as ET
import yaml
import aiohttp
import aiohttp.client_exceptions
from cache import Cache

# Logging configuration
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

DEFAULT_CONFIG = {
    'general': {
        'source_file': 'sources.txt',
        'output_file': 'playlist.m3u',
        'output_epg': 'light_epg.xml',
        'output_channels': 'channels.json',
        'epg_base_url': 'https://github.com/hoangxg4/mix-iptv/releases/latest/download',
        'timeout': 10,
        'stream_timeout': 3,
        'max_workers': 64,
        'spam_keywords': [
            'mời quý khán giả', 'thông báo', 'tạm ngưng',
            'bảo trì', 'kênh dự phòng', 'test',
        ],
    },
    'cache': {
        'enabled': True,
        'dir': '.cache',
        'epg_ttl': 3600,
        'source_ttl': 300,
        'link_ttl': 600,
    },
}


def load_config(path='config.yaml'):
    """Load YAML config, merging with defaults."""
    cfg = DEFAULT_CONFIG.copy()
    if os.path.exists(path):
        try:
            with open(path, 'r') as f:
                overrides = yaml.safe_load(f) or {}
            # Deep merge
            for section, values in overrides.items():
                if section in cfg and isinstance(cfg[section], dict):
                    cfg[section].update(values)
                else:
                    cfg[section] = values
        except Exception as e:
            logger.warning("Failed to load config %s: %s", path, e)
    return cfg


# ---------------------------------------------------------------------------
# Regex patterns (PRESERVED - must not change)
# ---------------------------------------------------------------------------

SPAM_KEYWORDS = DEFAULT_CONFIG['general']['spam_keywords']

GROUP_PRIORITY = {
    'VTV': 1, 'HTV': 2, 'VTC': 3, 'VTVCAB / ON': 4, 'VTVPRIME': 5,
    'K+': 6, 'THỂ THAO': 7, 'PHIM TRUYỆN': 8, 'QUỐC TẾ': 9, 'ĐỊA PHƯƠNG': 10,
}

# ÈPG only includes programmes for channels in the playlist — no artificial cap needed

RE_SPLIT_NAME = re.compile(r'[_\|]')
RE_CLEAN_TAGS = re.compile(r'(?i)[\[\(\-_\.]?\b(vn|vie|h264|hevc|clip|tv|fpt|sctv|vtc|local|chính|phụ)\b[\]\)\-_\.]?')
RE_FIX_BRANDS = re.compile(r'(?i)(vtv|htv|vtc|sctv|vtvcab|k\+)\s+(\d+)')
RE_SPECIAL_CHARS = re.compile(r'[^\w\s\+]')
RE_INTL = re.compile(r'\b(hbo|cinemax|axn|discovery|disney|cartoon|fox|warner|paramount|nat geo|fashion|fon|cnbc|cnn|bbc)\b')
RE_VTV_PRIME = re.compile(r'\bprime\b')
RE_VTV_NUM = re.compile(r'\bvtv\d*\b')
RE_HTV_NUM = re.compile(r'\bhtv\d*\b')
RE_VTC_NUM = re.compile(r'\bvtc\d*\b')
RE_VTVCAB = re.compile(r'\b(cab|vtvcab)\d*\b')
RE_ON = re.compile(r'\bon\b')
RE_LOCAL = re.compile(r'\b(địa phương|tỉnh|local)\b')
RE_SPORTS = re.compile(r'\b(thể thao|sports|bóng đá)\b')
RE_MOVIES = re.compile(r'\b(phim|movies|cinema)\b')
RE_TVG_ID = re.compile(r'tvg-id=["\']([^"\']+)["\']', re.I)
RE_TVG_LOGO = re.compile(r'tvg-logo=["\']([^"\']+)["\']', re.I)
RE_GROUP_TITLE = re.compile(r'group-title="([^"]*)"')
RE_TVG_URL = re.compile(r'(?:x-tvg-url|url-tvg|tvg-url)=["\']([^"\']+)["\']', re.I)
RE_NAT_KEY = re.compile(r'(\d+)')


# ---------------------------------------------------------------------------
# M3U Builder
# ---------------------------------------------------------------------------

class M3UBuilder:
    """Builds merged M3U playlist from multiple sources with EPG mapping."""

    def __init__(self, config=None):
        self.config = config or load_config()
        g = self.config['general']
        self.source_file = g['source_file']
        self.output_file = g['output_file']
        self.output_epg = g['output_epg']
        self.output_channels = g.get('output_channels', 'channels.json')
        self.timeout = g['timeout']
        self.stream_timeout = g['stream_timeout']
        self.max_workers = g['max_workers']
        self.spam_keywords = g['spam_keywords']
        self.epg_base_url = g.get('epg_base_url', 'https://github.com/hoangxg4/mix-iptv/releases/latest/download')

        c = self.config['cache']
        self.cache = Cache(cache_dir=c['dir'], default_ttl=c['epg_ttl'])
        self.cache_enabled = c['enabled']

        self.epg_urls = set()
        self.unique_links = {}
        self.epg_id_map = {}
        self.xml_name_mapping = {}
        self.epg_xml_roots = []
        self.final_used_ids = set()
        self.source_status = {}
        self.final_playlist = []

        # Async HTTP session (initialized in async context)
        self._session = None

    async def _get_session(self):
        """Get or create the aiohttp session."""
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(
                limit=self.max_workers,
                limit_per_host=10,
                force_close=False,
                enable_cleanup_closed=True,
            )
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
                headers={'User-Agent': 'Mozilla/5.0'},
            )
        return self._session

    async def close(self):
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()

    # -----------------------------------------------------------------------
    # PRESERVED METHODS (unchanged logic)
    # -----------------------------------------------------------------------

    def normalize_channel_name(self, name: str) -> str:
        name = RE_SPLIT_NAME.split(name)[0]
        name = re.sub(r'(?i)(fhd|hd|sd|1080p|720p|4k|hevc|h264)', ' ', name)
        name = RE_CLEAN_TAGS.sub(' ', name)
        name = RE_FIX_BRANDS.sub(r'\1\2', name)
        name = RE_SPECIAL_CHARS.sub('', name)
        # Strip source-identifier suffixes so channels from different providers merge
        name = re.sub(r'(?i)\s+vtvgo$', '', name)
        name = re.sub(r'(?i)\s+tv360$', '', name)
        name = re.sub(r'(?i)\s+đài ptth thành phố hồ chí minh$', '', name)
        name = re.sub(r'(?i)\s+channel$', '', name)
        name = re.sub(r'(?i)\s+orig$', '', name)
        cleaned = ' '.join(name.split()).strip().upper()
        if cleaned.startswith("VV"):
            cleaned = "VTV" + cleaned[2:]
        return cleaned

    def smart_grouping(self, raw_group: str, clean_name: str) -> str:
        g_lower = raw_group.lower() if raw_group else ""
        n_lower = clean_name.lower()
        if RE_INTL.search(n_lower):
            return 'Quốc Tế'
        if RE_VTV_PRIME.search(n_lower) and RE_VTV_NUM.search(n_lower):
            return 'VTVPRIME'
        if RE_VTVCAB.search(n_lower) or RE_ON.search(n_lower):
            return 'VTVCAB / ON'
        if RE_VTV_NUM.search(n_lower):
            return 'VTV'
        if RE_HTV_NUM.search(n_lower):
            return 'HTV'
        if RE_VTC_NUM.search(n_lower):
            return 'VTC'
        if 'k+' in n_lower:
            return 'K+'
        if RE_LOCAL.search(g_lower) or RE_LOCAL.search(n_lower):
            return 'Địa Phương'
        if RE_SPORTS.search(g_lower) or RE_SPORTS.search(n_lower):
            return 'Thể Thao'
        if RE_MOVIES.search(g_lower) or RE_MOVIES.search(n_lower):
            return 'Phim Truyện'
        # Fallback: use raw_group, but don't pollute known brand groups
        # with channels that don't match by name (e.g. SPOTV2 in a "VTV"
        # group-title should NOT end up in VTV group).
        if raw_group and raw_group.strip():
            grp = raw_group.strip()
            # Check if raw_group is essentially a known brand group
            grp_clean = re.sub(r'[^a-z0-9]', '', grp.lower())
            if grp_clean in ('vtv', 'htv', 'vtc', 'vtvcab', 'k'):
                return 'Khác'
            if grp.lower() not in ['khác', 'other', 'undefined']:
                # Normalize group name: capitalize first letter of each word
                return grp.title()
        return 'Khác'

    def get_sort_key(self, channel):
        group = channel['group'].upper()
        priority = GROUP_PRIORITY.get(group, 99)
        name = channel['name']
        # Channels with numbers (e.g. VTV1, HTV2) sort before non-numbered
        has_number = 0 if any(c.isdigit() for c in name) else 1
        nat_key = [int(c) if c.isdigit() else c.lower() for c in RE_NAT_KEY.split(name)]
        return (priority, group, has_number, nat_key)

    def parse_url_headers(self, url: str):
        headers = {'User-Agent': 'Mozilla/5.0'}
        clean_url = url
        if '|' in url:
            parts = url.split('|')
            clean_url = parts[0]
            for part in parts[1:]:
                if '=' in part:
                    k, v = part.split('=', 1)
                    headers[k.strip()] = v.strip()
        return clean_url, headers

    def get_best_id_match(self, clean_name, orig_id):
        orig_id_lower = orig_id.lower() if orig_id else ""
        cname_lower = clean_name.lower()

        for brand in ['vtv', 'htv', 'vtc', 'sctv']:
            if brand in cname_lower and brand not in orig_id_lower:
                orig_id_lower = ""
                break

        # TẦNG 1: Khớp ID gốc chuẩn trực tiếp trong EPG
        if orig_id_lower and orig_id_lower in self.epg_id_map:
            return self.epg_id_map[orig_id_lower]

        # TẦNG 2: Khớp chính xác 100% theo tên đã chuẩn hóa
        if clean_name in self.xml_name_mapping:
            return self.xml_name_mapping[clean_name]

        # TẦNG 3: Dò tìm Fuzzy theo ID chứa tên kênh
        if any(b in cname_lower for b in ['vtv', 'htv', 'vtc', 'sctv', 'k+']):
            for epg_id_low, actual_id in self.epg_id_map.items():
                if (epg_id_low == cname_lower
                        or epg_id_low.startswith(cname_lower + ".")
                        or epg_id_low == cname_lower + "hd"
                        or epg_id_low == cname_lower + "_hd"):
                    return actual_id
            for x_name, ch_id in self.xml_name_mapping.items():
                if cname_lower in x_name.lower() or x_name.lower() in cname_lower:
                    return ch_id

        return ""

    # -----------------------------------------------------------------------
    # CHANNEL PROCESSING (unchanged logic, no I/O)
    # -----------------------------------------------------------------------

    def add_channel(self, extinf: str, url: str, raw_group: str, extra_tags: list):
        raw_name = extinf.split(',')[-1].strip()
        # Skip channels whose name looks like a URL (garbage data from malformed sources)
        if re.match(r'^https?://', raw_name, re.IGNORECASE):
            return
        clean_name = self.normalize_channel_name(raw_name)
        # Skip garbage: too short, too long (>40 chars = junk with embedded ads), or spam
        if len(clean_name) < 2 or len(clean_name) > 40 or any(spam in clean_name.lower() for spam in self.spam_keywords):
            return

        id_match = RE_TVG_ID.search(extinf)
        logo_match = RE_TVG_LOGO.search(extinf)
        found_id = id_match.group(1).strip() if id_match else ""
        found_logo = logo_match.group(1).strip() if logo_match else ""

        if url not in self.unique_links:
            self.unique_links[url] = {
                'url': url,
                'name': clean_name,
                'group': self.smart_grouping(raw_group, clean_name),
                'tvg_id': found_id,
                'tvg_logo': found_logo,
                'extra_tags': extra_tags,
            }

    # -----------------------------------------------------------------------
    # ASYNC I/O OPERATIONS
    # -----------------------------------------------------------------------

    async def process_source(self, url, semaphore):
        """Fetch and parse a source M3U playlist asynchronously."""
        async with semaphore:
            clean_url, headers = self.parse_url_headers(url)
            try:
                session = await self._get_session()

                # Check content-hash cache for source data
                cache_key = self.cache.make_content_hash_key(clean_url)
                cached_data = None
                if self.cache_enabled:
                    cached_data = await self.cache.get(cache_key)

                text = None
                if cached_data is not None:
                    text = cached_data.get('body')
                    self.source_status[url] = True
                else:
                    async with session.get(clean_url, headers=headers,
                                           timeout=aiohttp.ClientTimeout(total=self.timeout)) as res:
                        if res.status < 400:
                            text = await res.text()
                            self.source_status[url] = True
                            if self.cache_enabled and text:
                                source_ttl = self.config['cache'].get('source_ttl', 300)
                                await self.cache.set(cache_key, {'body': text},
                                                     ttl=source_ttl)
                        else:
                            self.source_status[url] = False

                if text is None:
                    self.source_status[url] = False
                    return

                # Auto-detect JSON source format (e.g. freem3u.xyz)
                if text.strip().startswith('{'):
                    try:
                        import json
                        data = json.loads(text)
                        if isinstance(data, dict) and 'channels' in data:
                            await self._process_json_source(data, url)
                            return
                    except (json.JSONDecodeError, Exception):
                        pass

                curr_extinf = ""
                curr_grp = ""
                extra_tags = []
                for line in text.splitlines():
                    line = line.strip()
                    if line.startswith("#EXTINF"):
                        curr_extinf = line
                        m = RE_GROUP_TITLE.search(line)
                        curr_grp = m.group(1) if m else ""
                        extra_tags = []
                    elif line.startswith("#EXTM3U"):
                        m = RE_TVG_URL.search(line)
                        if m:
                            for e in m.group(1).split(','):
                                if e.strip():
                                    self.epg_urls.add(e.strip())
                    elif line.startswith("#") and curr_extinf:
                        extra_tags.append(line)
                    elif not line.startswith("#") and line.startswith("http") and curr_extinf:
                        self.add_channel(curr_extinf, line, curr_grp, extra_tags)
                        curr_extinf = ""

            except (aiohttp.ClientError, asyncio.TimeoutError, Exception):
                self.source_status[url] = False

    async def _process_json_source(self, data, url):
        """Parse a JSON source in freem3u.xyz format.

        Expected structure:
        {
            "epgList": ["https://..."],
            "channels": [
                {
                    "id": "vtv1",
                    "title": "VTV1",
                    "tvgId": "vtv1hd.VN",
                    "urls": [{"url": "https://..."}, ...],
                    "thumbnail": "https://...",
                    "group": ["VTV", "Tin Tức"],
                    "channelIndex": 1
                }
            ]
        }
        """
        # Extract EPG URLs from this source
        for epg_url in data.get('epgList', []):
            if isinstance(epg_url, str) and epg_url.strip():
                self.epg_urls.add(epg_url.strip())

        import json as _json  # already imported at top

        for ch in data.get('channels', []):
            title = (ch.get('title') or '').strip()
            if not title:
                continue
            tvg_id = ch.get('tvgId') or ''
            tvg_logo = ch.get('thumbnail') or ''
            groups = ch.get('group', [])
            if isinstance(groups, list) and groups:
                raw_group = str(groups[0])
            elif isinstance(groups, str):
                raw_group = groups
            else:
                raw_group = ''

            for u in ch.get('urls', []):
                if not isinstance(u, dict):
                    continue
                stream_url = (u.get('url') or '').strip()
                if not stream_url.startswith('http'):
                    continue
                # Include all provider types (direct/webview/json/flow/parser)

                extinf = (
                    f'#EXTINF:-1 tvg-id="{tvg_id}" '
                    f'tvg-logo="{tvg_logo}" '
                    f'group-title="{raw_group}",{title}'
                )
                self.add_channel(extinf, stream_url, raw_group, extra_tags=[])

    async def check_single_link(self, data, semaphore):
        """Lenient stream link check: only filter out connection-level failures.

        Accepts any HTTP response (even 403/404/521) to avoid false
        positives from geo-blocking or transient server issues.
        Only removes links that can't be reached at all (timeout,
        DNS failure, connection refused, SSL error).
        """
        async with semaphore:
            clean_url, headers = self.parse_url_headers(data['url'])
            try:
                session = await self._get_session()
                async with session.get(clean_url, headers=headers,
                                       timeout=aiohttp.ClientTimeout(total=self.stream_timeout),
                                       allow_redirects=True) as res:
                    # Any HTTP response → keep the link (even 403/404/521)
                    return data
            except (asyncio.TimeoutError, aiohttp.ClientConnectorError):
                pass  # truly unreachable → filter out
            except Exception:
                pass  # any other error → filter out
            return None

    async def _fetch_single_epg(self, epg_url, semaphore):
        """Fetch and parse a single EPG XML source asynchronously with ETag caching."""
        async with semaphore:
            try:
                session = await self._get_session()
                headers = {'User-Agent': 'Mozilla/5.0'}

                # Add conditional headers from cache
                if self.cache_enabled:
                    cached_headers = await self.cache.get_headers(epg_url)
                    if cached_headers.get('etag'):
                        headers['If-None-Match'] = cached_headers['etag']
                    if cached_headers.get('last-modified'):
                        headers['If-Modified-Since'] = cached_headers['last-modified']

                async with session.get(epg_url, headers=headers,
                                       timeout=aiohttp.ClientTimeout(total=20)) as res:
                    if res.status == 304:
                        # Not modified — use cached XML data
                        cache_key = self.cache.make_content_hash_key(epg_url)
                        cached = await self.cache.get(cache_key)
                        if cached:
                            xml_str = cached.get('body')
                            xml_data = xml_str.encode('utf-8')
                            # Save raw EPG to .cache/epg/
                            self.save_epg_raw(epg_url, xml_data)
                            return self._parse_epg(xml_data, epg_url)
                        return None

                    if res.status >= 400:
                        return None

                    content = await res.read()

                    # Store ETag/Last-Modified from response
                    if self.cache_enabled:
                        new_headers = {}
                        if 'ETag' in res.headers:
                            new_headers['etag'] = res.headers['ETag']
                        if 'Last-Modified' in res.headers:
                            new_headers['last-modified'] = res.headers['Last-Modified']
                        if new_headers:
                            await self.cache.store_headers(epg_url, new_headers)

                    # Decompress if gzipped
                    if epg_url.endswith('.gz') or res.headers.get('Content-Encoding') == 'gzip':
                        xml_data = gzip.decompress(content)
                    else:
                        xml_data = content

                    # Save raw EPG to .cache/epg/
                    self.save_epg_raw(epg_url, xml_data)

                    # Cache the raw XML data
                    if self.cache_enabled and xml_data:
                        cache_key = self.cache.make_content_hash_key(epg_url)
                        await self.cache.set(cache_key, {'body': xml_data.decode('utf-8', errors='replace')},
                                             ttl=self.config['cache'].get('epg_ttl', 3600))

                    return self._parse_epg(xml_data, epg_url)

            except Exception:
                return None

    def _parse_epg(self, xml_data, epg_url):
        """Parse EPG XML bytes into (root, local_ids, local_mapping)."""
        try:
            root = ET.fromstring(xml_data)
            local_ids = {}
            local_mapping = {}
            for elem in root.findall('channel'):
                ch_id = elem.get('id')
                if not ch_id:
                    continue
                local_ids[ch_id.lower()] = ch_id
                for dn in elem.findall('display-name'):
                    if dn.text:
                        norm_name = self.normalize_channel_name(dn.text)
                        if norm_name not in local_mapping:
                            local_mapping[norm_name] = ch_id
            return root, local_ids, local_mapping
        except ET.ParseError:
            return None

    # -----------------------------------------------------------------------
    # EPG RAW SAVE
    # -----------------------------------------------------------------------

    def save_epg_raw(self, url, xml_data):
        """Save raw EPG XML data to .cache/epg/ directory.

        Args:
            url: EPG source URL (used to derive filename via SHA-256).
            xml_data: Raw bytes of the EPG XML.

        Returns:
            Path to the saved file.
        """
        cache_dir = self.config['cache']['dir']
        epg_dir = os.path.join(cache_dir, 'epg')
        os.makedirs(epg_dir, exist_ok=True)
        # Use SHA-256 of URL for deterministic filename
        safe = hashlib.sha256(url.encode('utf-8')).hexdigest()
        path = os.path.join(epg_dir, f'{safe}.xml')
        with open(path, 'wb') as f:
            f.write(xml_data)
        logger.debug("Saved raw EPG to %s", path)
        return path

    async def fetch_epg_and_map_ids(self):
        """Fetch all EPG sources asynchronously and build ID maps."""
        if not self.epg_urls:
            return
        logger.info("Đang tải và xử lý EPG đồng thời...")
        semaphore = asyncio.Semaphore(8)
        tasks = [self._fetch_single_epg(url, semaphore) for url in list(self.epg_urls)]
        results = await asyncio.gather(*tasks)
        for res in results:
            if res:
                root, local_ids, local_mapping = res
                self.epg_xml_roots.append(root)
                self.epg_id_map.update(local_ids)
                self.xml_name_mapping.update(local_mapping)

    # -----------------------------------------------------------------------
    # PROGRAMME DEDUP
    # -----------------------------------------------------------------------

    @staticmethod
    def _dedup_programmes(programme_elements):
        """Deduplicate programme entries by (channel, start) tuple.

        Args:
            programme_elements: List of Element objects for <programme>.

        Returns:
            Deduplicated list with only the first occurrence of each (channel, start) pair.
        """
        seen = set()
        result = []
        for prog in programme_elements:
            ch = prog.get('channel', '')
            start = prog.get('start', '')
            key = (ch, start)
            if key not in seen:
                seen.add(key)
                result.append(prog)
        return result

    # -----------------------------------------------------------------------
    # CHANNELS JSON OUTPUT (iptvschema.org)
    # -----------------------------------------------------------------------

    def generate_channels_json(self):
        """Generate channels.json following iptvschema.org (Provider -> Group -> Channel -> Source -> Stream -> StreamLink).

        Uses self.final_playlist as the source of channel data.
        """

        # Build groups from final_playlist
        # Merge case-insensitive duplicate group names (safety net for "Vtv" ≠ "VTV")
        groups_dict = {}
        _group_normalized = {}  # lowercase name -> canonical display name
        _group_candidates = {}  # lowercase name -> [all case-variants seen]
        for ch in self.final_playlist:
            group_name = ch.get('group', 'Khác')
            group_lower = group_name.lower()
            if group_lower in _group_normalized:
                canonical = _group_normalized[group_lower]
                groups_dict[canonical].append(ch)
                # Track candidates for canonical name resolution
                if group_name not in _group_candidates[group_lower]:
                    _group_candidates[group_lower].append(group_name)
            else:
                _group_normalized[group_lower] = group_name
                _group_candidates[group_lower] = [group_name]
                groups_dict[group_name] = [ch]

        # Resolve canonical display names: prefer GROUP_PRIORITY match or uppercase
        for lower_key, candidates in _group_candidates.items():
            if len(candidates) > 1:
                # Pick best: exact priority match > uppercase > first-encountered
                best = candidates[0]
                for c in candidates:
                    if c in GROUP_PRIORITY:
                        best = c
                        break
                    if c == c.upper():
                        best = c
                if best != _group_normalized[lower_key]:
                    # Merge into the better-named group
                    old_canonical = _group_normalized[lower_key]
                    groups_dict[best] = groups_dict.pop(old_canonical)
                    _group_normalized[lower_key] = best

        groups = []
        for idx, (group_name, channels) in enumerate(groups_dict.items()):
            group_id = group_name.lower().replace(' ', '-').replace('/', '-')
            json_channels = []
            for ch_idx, ch in enumerate(channels):
                ch_id = ch.get('name', f'ch-{ch_idx}').lower().replace(' ', '-')
                # Build stream_links: primary + fallbacks
                stream_links = []
                primary_url = ch.get('url', '')
                if primary_url:
                    stream_links.append({
                        'id': f'{ch_id}-s1',
                        'name': 'Server 1',
                        'url': primary_url,
                        'type': 'hls' if primary_url.endswith('.m3u8') else 'hls',
                        'default': True,
                        'enableP2P': False,
                        'subtitles': None,
                        'remote_data': None,
                        'request_headers': None,
                        'comments': None,
                    })
                for fb_idx, fb_url in enumerate(ch.get('fallback_urls', [])):
                    stream_links.append({
                        'id': f'{ch_id}-s{fb_idx + 2}',
                        'name': f'Server {fb_idx + 2}',
                        'url': fb_url,
                        'type': 'hls' if fb_url.endswith('.m3u8') else 'hls',
                        'default': False,
                        'enableP2P': False,
                        'subtitles': None,
                        'remote_data': None,
                        'request_headers': None,
                        'comments': None,
                    })

                json_channels.append({
                    'id': ch_id,
                    'name': ch.get('name', ''),
                    'description': None,
                    'label': None,
                    'image': None,
                    'display': 'default',
                    'type': 'single',
                    'enable_detail': True,
                    'tvg_id': ch.get('final_id', ch.get('tvg_id', '')),
                    'tvg_logo': ch.get('final_logo', ch.get('tvg_logo', '')),
                    'sources': [
                        {
                            'id': f'{ch_id}-src-1',
                            'name': 'Source 1',
                            'image': None,
                            'contents': [
                                {
                                    'id': f'{ch_id}-content-1',
                                    'name': 'Content 1',
                                    'image': None,
                                    'streams': [
                                        {
                                            'id': f'{ch_id}-stream-1',
                                            'name': 'Main',
                                            'image': None,
                                            'stream_links': stream_links,
                                        }
                                    ],
                                }
                            ],
                            'remote_data': None,
                        }
                    ],
                })

            groups.append({
                'id': group_id,
                'name': group_name,
                'display': 'vertical',
                'image': None,
                'grid_number': idx + 1,
                'enable_detail': True,
                'channels': json_channels,
            })

        provider = {
            'id': 'mix-iptv',
            'name': 'Mix IPTV',
            'description': 'Mixed IPTV playlist auto-generated from multiple sources',
            'url': None,
            'color': None,
            'image': None,
            'grid_number': 1,
            'groups': groups,
        }

        with open(self.output_channels, 'w', encoding='utf-8') as f:
            json.dump(provider, f, ensure_ascii=False, indent=2)

        logger.info("Đã tạo %s với %d nhóm, %d kênh",
                    self.output_channels, len(groups), len(self.final_playlist))

    # -----------------------------------------------------------------------
    # MAIN RUN LOOP
    # -----------------------------------------------------------------------

    async def run(self):
        """Main entry point: read sources, fetch playlists, check links, build output."""
        if not os.path.exists(self.source_file):
            logger.error("Không tìm thấy file nguồn: %s", self.source_file)
            return

        logger.info("Đang xử lý và dọn dẹp các liên kết trong sources.txt...")
        with open(self.source_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        unique_urls = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            raw_url = line.replace('[DIE]', '').strip()
            if raw_url.startswith("http") and raw_url not in unique_urls:
                unique_urls.append(raw_url)

        # Phase 1: Fetch all sources concurrently
        source_sem = asyncio.Semaphore(10)
        source_tasks = [self.process_source(url, source_sem) for url in unique_urls]
        await asyncio.gather(*source_tasks)

        # Update source status file
        with open(self.source_file, 'w', encoding='utf-8') as f:
            for url in unique_urls:
                status_suffix = " [DIE]" if not self.source_status.get(url, True) else ""
                f.write(f"{url}{status_suffix}\n")

        # Phase 2: Lenient stream link check
        # Only filters out unreachable links (timeout/DNS/connection refused).
        # Accepts any HTTP response (403/521 etc.) to avoid geo-blocking false positives.
        working_links = []
        logger.info("Đang kiểm tra sơ bộ stream links (lỏng)...")
        link_sem = asyncio.Semaphore(self.max_workers)
        check_tasks = [self.check_single_link(d, link_sem) for d in self.unique_links.values()]
        results = await asyncio.gather(*check_tasks)
        working_links = [r for r in results if r is not None]

        # Phase 3: Fetch EPG and build ID maps
        await self.fetch_epg_and_map_ids()

        # Phase 4: Group channels and build final playlist
        logger.info("Đang đồng bộ Metadata để gộp nhóm Multi-source...")
        grouped_channels = {}
        for ch in working_links:
            cname = ch['name']
            if cname not in grouped_channels:
                grouped_channels[cname] = []
            grouped_channels[cname].append(ch)

        self.final_playlist = []
        for cname, links in grouped_channels.items():
            best_id = ""
            for l in links:
                pid = self.get_best_id_match(cname, l['tvg_id'])
                if pid:
                    best_id = pid
                    break
            best_logo = ""
            for l in links:
                if l['tvg_logo']:
                    best_logo = l['tvg_logo']
                    break
            primary = links[0]
            primary['final_id'] = best_id
            primary['final_logo'] = best_logo
            if best_id:
                self.final_used_ids.add(best_id)
            primary['fallback_urls'] = [l['url'] for l in links[1:]]
            self.final_playlist.append(primary)

        self.final_playlist.sort(key=self.get_sort_key)

        # Phase 5: Write output playlist
        with open(self.output_file, 'w', encoding='utf-8') as f:
            f.write(f'#EXTM3U x-tvg-url="{self.epg_base_url}/{self.output_epg}"\n')
            for ch in self.final_playlist:
                line = (
                    f'#EXTINF:-1 tvg-id="{ch["final_id"]}" '
                    f'tvg-name="{ch["name"]}" '
                    f'tvg-logo="{ch["final_logo"]}" '
                    f'group-title="{ch["group"]}",{ch["name"]}'
                )
                f.write(line + "\n")
                f.write(f"#EXTGRP:{ch['group']}\n")
                for t in ch['extra_tags']:
                    f.write(t + "\n")
                f.write(ch['url'] + "\n")
                for fb_url in ch.get('fallback_urls', []):
                    f.write(fb_url + "\n")

        # Phase 6: Write trimmed EPG with programme dedup + size safety cap
        if self.epg_xml_roots:
            logger.info("Đang trích xuất cấu trúc EPG tinh gọn...")
            root_out = ET.Element("tv")
            added_ch = set()

            # --- Channel entries: only include matched channels ---
            if self.final_used_ids:
                for root_in in self.epg_xml_roots:
                    for elem in root_in.findall('channel'):
                        ch_id = elem.get('id')
                        if ch_id in self.final_used_ids and ch_id not in added_ch:
                            root_out.append(elem)
                            added_ch.add(ch_id)

            # --- Programme entries: only for matched channels ---
            # Collect programmes for channels that exist in the final playlist
            all_programmes = []
            for root_in in self.epg_xml_roots:
                for elem in root_in.findall('programme'):
                    ch = elem.get('channel')
                    if ch in added_ch:
                        all_programmes.append(elem)

            deduped = self._dedup_programmes(all_programmes)

            for prog in deduped:
                root_out.append(prog)

            tree = ET.ElementTree(root_out)
            ET.indent(tree, space="  ", level=0)
            tree.write(self.output_epg, encoding='utf-8', xml_declaration=True)

        # Phase 7: Generate channels.json
        if self.final_playlist:
            self.generate_channels_json()

        logger.info("Hoàn tất! Đã xử lý playlist thành công.")
        await self.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main():
    """Async entry point."""
    config = load_config()
    builder = M3UBuilder(config)
    await builder.run()


if __name__ == "__main__":
    asyncio.run(main())
