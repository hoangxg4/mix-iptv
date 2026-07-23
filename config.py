"""Configuration loading and constants for mix-iptv."""
import logging
import os
import re
import yaml

# Logging configuration
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default configuration
# ---------------------------------------------------------------------------

DEFAULT_CONFIG = {
    'general': {
        'source_file': 'sources.txt',
        'output_file': 'playlist.m3u',
        'output_epg': 'epg.xml.gz',
        'output_channels': 'channels.json',
        'epg_base_url': 'https://github.com/hoangxg4/mix-iptv/releases/latest/download',
        'timeout': 10,
        'stream_timeout': 3,
        'max_workers': 64,
        'spam_keywords': [],
        'epg_trim_days': 7,
    },
    'cache': {
        'enabled': True,
        'dir': '.cache',
        'epg_ttl': 3600,
        'source_ttl': 300,
        'link_ttl': 600,
    },
    'proxy': {
        'enabled': True,
        'socks5': '',
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

# EPG only includes programmes for channels in the playlist — no artificial cap needed

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
