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
import aiohttp
import aiohttp.client_exceptions
from urllib.parse import urlparse, urljoin
from datetime import datetime, timedelta, timezone
import unicodedata
from aiohttp_socks import ProxyConnector
from cache import Cache
from config import DEFAULT_CONFIG, load_config, GROUP_PRIORITY, SPAM_KEYWORDS
from config import RE_SPLIT_NAME, RE_CLEAN_TAGS, RE_FIX_BRANDS, RE_SPECIAL_CHARS
from config import RE_INTL, RE_VTV_PRIME, RE_VTV_NUM, RE_HTV_NUM, RE_VTC_NUM
from config import RE_VTVCAB, RE_ON, RE_LOCAL, RE_SPORTS, RE_MOVIES
from config import RE_TVG_ID, RE_TVG_LOGO, RE_GROUP_TITLE, RE_TVG_URL, RE_NAT_KEY
from epg import parse_xmltv_datetime, programme_in_window, dedup_programmes
from output import generate_channels_json, write_m3u_playlist

# Logging configuration
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


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
        self.epg_trim_days = g.get('epg_trim_days', 7)

        c = self.config['cache']
        self.cache = Cache(cache_dir=c['dir'], default_ttl=c['epg_ttl'])
        self.cache_enabled = c['enabled']

        p = self.config.get('proxy', {})
        self.proxy_enabled = p.get('enabled', False)
        self.proxy_socks5 = p.get('socks5', '')
        self._proxy_session = None

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

    async def _get_proxy_session(self):
        """Get or create an aiohttp session routed through the SOCKS5 proxy.

        Used for link checking geo-blocked streams. Falls back to direct
        connection if proxy is not configured or fails.
        """
        if not self.proxy_enabled or not self.proxy_socks5:
            return await self._get_session()
        if self._proxy_session is None or self._proxy_session.closed:
            try:
                connector = ProxyConnector.from_url(self.proxy_socks5)
                timeout = aiohttp.ClientTimeout(total=self.stream_timeout)
                self._proxy_session = aiohttp.ClientSession(
                    connector=connector,
                    timeout=timeout,
                    headers={'User-Agent': 'Mozilla/5.0'},
                )
            except Exception:
                logger.warning("Failed to create proxy session, falling back to direct")
                return await self._get_session()
        return self._proxy_session

    async def close(self):
        """Close both HTTP sessions."""
        if self._proxy_session and not self._proxy_session.closed:
            await self._proxy_session.close()
        if self._session and not self._session.closed:
            await self._session.close()

    # -----------------------------------------------------------------------
    # PRESERVED METHODS (unchanged logic)
    # -----------------------------------------------------------------------

    def normalize_channel_name(self, name: str) -> str:
        # Normalize Unicode (NFC) so decomposed characters like Ô+̣→Ộ merge correctly
        name = unicodedata.normalize('NFC', name)
        name = name.replace('VIET NAM', 'VIETNAM')
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

    def smart_grouping(self, raw_group: str, clean_name: str, flags: dict = None) -> str:
        g_lower = raw_group.lower() if raw_group else ""
        n_lower = clean_name.lower()
        # Vietnam Today goes to VTV group (cuối danh sách)
        if re.search(r'\bviet\s*nam\s*today\b', n_lower):
            if flags is not None:
                flags['_vietnam_today'] = True
            return 'VTV'
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
        # Vietnam Today always sorts last within its group
        if channel.get('_vietnam_today'):
            has_number = 2
        # Channels with numbers (e.g. VTV1, HTV2) sort before non-numbered
        elif any(c.isdigit() for c in name):
            has_number = 0
        else:
            has_number = 1
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

        # Helper: tìm brand (vtv/htv/vtc/...) trong tên kênh
        cname_brand = None
        for b in ['vtv', 'htv', 'vtc', 'sctv', 'k+']:
            if b in cname_lower:
                cname_brand = b
                break

        # TẦNG 2: Khớp chính xác 100% theo tên đã chuẩn hóa
        numeric_fallback = None
        text_fallback = None
        if clean_name in self.xml_name_mapping:
            ch_id = self.xml_name_mapping[clean_name]
            if not ch_id.isdigit():
                # Ưu tiên ID có chứa brand (vtv1hd > TV1 cho kênh VTV)
                if cname_brand and cname_brand in ch_id.lower():
                    return ch_id
                text_fallback = ch_id  # Non-numeric, sai brand → fallback
            else:
                numeric_fallback = ch_id

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
                    if not ch_id.isdigit():
                        if cname_brand and cname_brand in ch_id.lower():
                            return ch_id
                        if text_fallback is None:
                            text_fallback = ch_id

        # Fallback: ưu tiên text (dù sai brand), cuối cùng numeric
        if text_fallback:
            return text_fallback
        if numeric_fallback:
            return numeric_fallback
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
            flags = {}
            group = self.smart_grouping(raw_group, clean_name, flags)
            self.unique_links[url] = {
                'url': url,
                'name': clean_name,
                'group': group,
                'tvg_id': found_id,
                'tvg_logo': found_logo,
                'extra_tags': extra_tags,
                **flags,
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
                # Only keep direct stream URLs (.m3u8, .mpd, .ts)
                # Filter out: webview (shaka.html), JSON API, parser/flow URLs
                parsed = urlparse(stream_url)
                path = parsed.path.lower()
                if not (path.endswith('.m3u8') or path.endswith('.mpd') or path.endswith('.ts')):
                    continue

                extinf = (
                    f'#EXTINF:-1 tvg-id="{tvg_id}" '
                    f'tvg-logo="{tvg_logo}" '
                    f'group-title="{raw_group}",{title}'
                )
                self.add_channel(extinf, stream_url, raw_group, extra_tags=[])

    async def _deep_check_segment(self, session, seg_url, headers):
        """HEAD a segment URL — returns False if 404/521 (dead segment)."""
        try:
            async with session.head(seg_url,
                                    timeout=aiohttp.ClientTimeout(total=3),
                                    headers=headers) as hresp:
                return hresp.status not in (404, 521)
        except Exception:
            return True  # Network error on HEAD is non-fatal

    async def _deep_check_variant(self, session, var_url, headers, seg_headers):
        """Fetch a variant playlist and HEAD its first segment.

        Returns False if variant itself is 404/521.
        """
        try:
            async with session.get(var_url,
                                    timeout=aiohttp.ClientTimeout(total=5),
                                    headers=headers) as vresp:
                if vresp.status in (404, 521):
                    return False
                if vresp.status == 200:
                    vchunk = await vresp.content.read(4096)
                    vtext = vchunk.decode('utf-8', errors='replace')
                    for line in vtext.split('\n'):
                        stripped = line.strip()
                        if stripped and not stripped.startswith('#'):
                            seg_url = urljoin(str(vresp.url), stripped)
                            return await self._deep_check_segment(session, seg_url, seg_headers)
                return True
        except Exception:
            return True

    async def _try_link_once(self, data, clean_url, headers, use_proxy=False):
        """Try fetching a stream URL once. Returns (data_or_None, retryable_bool).

        retryable=True means the failure *might* be geo-blocking and worth
        retrying via proxy. 404/521 are fatal (never retry).

        After basic HTTP check, also validates HLS content and verifies
        at least one segment exists (catches undead streams where
        playlists respond 200 but media segments return 404).
        """
        try:
            if use_proxy:
                session = await self._get_proxy_session()
            else:
                session = await self._get_session()
            async with session.get(clean_url, headers=headers,
                                   timeout=aiohttp.ClientTimeout(total=self.stream_timeout),
                                   allow_redirects=True) as res:
                # For HLS URLs, validate content + (if 200) deep-check segments
                if clean_url.lower().endswith('.m3u8'):
                    try:
                        chunk = await res.content.read(4096)
                        text = chunk.decode('utf-8', errors='replace')
                        lines = text.split('\n')

                        # Validate it's real HLS content (EXTM3U header)
                        header = chunk.strip().lower()
                        if not (header.startswith(b'#extm3u') or header.startswith(b'#ext-x-')):
                            # Not HLS content (e.g. geo-block HTML, 403 page)
                            if res.status in (404, 521):
                                return None, False  # Fatal
                            return None, True  # Retry via proxy

                        # Status-based filtering for HLS
                        if res.status in (404, 521):
                            return None, False  # Fatal
                        if res.status != 200:
                            return None, True  # Non-success (e.g. 403) → retry via proxy

                        # Deep check: verify at least one segment exists
                        # Detects "undead" streams: playlist 200, segments 404
                        # (VTV1 AUDIO: chunklist.m3u8 200, but segments 404)
                        has_segments = any(l.strip().lower().startswith('#extinf') for l in lines)
                        has_variants = any('#ext-x-stream-inf' in l.lower() for l in lines)
                        base_url = str(res.url)

                        if has_segments:
                            # Variant playlist: HEAD first segment
                            for line in lines:
                                stripped = line.strip()
                                if stripped and not stripped.startswith('#'):
                                    seg_url = urljoin(base_url, stripped)
                                    ok = await self._deep_check_segment(session, seg_url, headers)
                                    if not ok:
                                        return None, True
                                    break
                        elif has_variants:
                            # Master playlist: follow first variant, then HEAD its segment
                            for line in lines:
                                stripped = line.strip()
                                # Skip comments, attribute lines (URI="..."), non-URL lines
                                if not stripped or stripped.startswith('#'):
                                    continue
                                if '=' in stripped:
                                    continue  # Attribute line, not a bare URL
                                var_url = urljoin(base_url, stripped)
                                ok = await self._deep_check_variant(session, var_url, headers, headers)
                                if not ok:
                                    return None, True
                                break
                    except Exception:
                        pass  # Can't deep-check, assume valid

                # Non-HLS URLs: fatal on 404/521, pass through everything else
                elif res.status in (404, 521):
                    return None, False

                return data, False  # Success
        except (asyncio.TimeoutError, aiohttp.ClientConnectorError):
            return None, True  # Network error, could be geo-block
        except Exception:
            return None, True  # Any other error, could be geo-block

    async def check_single_link(self, data, semaphore):
        """Stream link check: try direct first, retry via proxy on geo-block.

        - 404/521 → immediately dropped (no retry)
        - Connection failures / 403 / bad content → retry via proxy once
        - Success → kept
        """
        async with semaphore:
            clean_url, headers = self.parse_url_headers(data['url'])

            # 1) Try direct (fast path)
            result, retryable = await self._try_link_once(data, clean_url, headers, use_proxy=False)
            if result is not None:
                return result

            # 2) Retry via proxy if failure could be geo-blocking
            if retryable and self.proxy_enabled and self.proxy_socks5:
                result, _ = await self._try_link_once(data, clean_url, headers, use_proxy=True)
                return result

            return None

    async def _try_fetch_epg_once(self, epg_url, semaphore, use_proxy=False):
        """Try fetching and parsing a single EPG source. Returns parsed root or None."""
        async with semaphore:
            try:
                if use_proxy:
                    session = await self._get_proxy_session()
                else:
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

                    self.save_epg_raw(epg_url, xml_data)

                    # Cache the raw XML data
                    if self.cache_enabled and xml_data:
                        cache_key = self.cache.make_content_hash_key(epg_url)
                        await self.cache.set(cache_key, {'body': xml_data.decode('utf-8', errors='replace')},
                                             ttl=self.config['cache'].get('epg_ttl', 3600))

                    return self._parse_epg(xml_data, epg_url)

            except Exception:
                return None

    async def _fetch_single_epg(self, epg_url, semaphore):
        """Fetch EPG: try direct first, retry via proxy on geo-block.

        403/connection errors are retried via proxy once (common for
        region-restricted EPG sources like vnepg.site).
        """
        # 1) Try direct (fast path)
        result = await self._try_fetch_epg_once(epg_url, semaphore, use_proxy=False)
        if result is not None:
            return result

        # 2) Retry via proxy if configured
        if self.proxy_enabled and self.proxy_socks5:
            result = await self._try_fetch_epg_once(epg_url, semaphore, use_proxy=True)
            return result

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
        write_m3u_playlist(self.final_playlist, self.output_file, self.epg_base_url, self.output_epg)

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

            # --- Programme entries: only for matched channels + date-window ---
            # Collect programmes for channels in the final playlist
            now = datetime.now(timezone.utc)
            window_start = now - timedelta(days=1)   # 1 day history
            window_end = now + timedelta(days=self.epg_trim_days)

            all_programmes = []
            for root_in in self.epg_xml_roots:
                for elem in root_in.findall('programme'):
                    ch = elem.get('channel')
                    if ch in added_ch and programme_in_window(elem, window_start, window_end):
                        all_programmes.append(elem)

            logger.info(
                "EPG time window: [now - 1d, now + %dd] — collecting programmes...",
                self.epg_trim_days,
            )

            deduped = dedup_programmes(all_programmes)

            for prog in deduped:
                root_out.append(prog)

            tree = ET.ElementTree(root_out)
            ET.indent(tree, space="  ", level=0)
            if self.output_epg.endswith('.gz'):
                with gzip.open(self.output_epg, 'wb') as f:
                    tree.write(f, encoding='utf-8', xml_declaration=True)
            else:
                tree.write(self.output_epg, encoding='utf-8', xml_declaration=True)

        # Phase 7: Generate channels.json
        if self.final_playlist:
            generate_channels_json(self.final_playlist, self.output_channels, logger)

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
