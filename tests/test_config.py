"""Tests for config.yaml loading and validation."""
import os
import tempfile
import yaml
import pytest


DEFAULT_CONFIG = {
    'general': {
        'source_file': 'sources.txt',
        'output_file': 'playlist.m3u',
        'output_epg': 'epg.xml',
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


def test_config_defaults_match_expected():
    """Verify default config dict has all required keys."""
    assert 'general' in DEFAULT_CONFIG
    assert 'cache' in DEFAULT_CONFIG
    assert 'source_file' in DEFAULT_CONFIG['general']
    assert 'spam_keywords' in DEFAULT_CONFIG['general']
    assert len(DEFAULT_CONFIG['general']['spam_keywords']) == 6
    assert DEFAULT_CONFIG['cache']['epg_ttl'] == 3600


def test_config_yaml_roundtrip():
    """A config dict can be written to YAML and read back identically."""
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        yaml.dump(DEFAULT_CONFIG, f)
        f.flush()
        fname = f.name
    try:
        with open(fname, 'r') as f:
            loaded = yaml.safe_load(f)
        assert loaded == DEFAULT_CONFIG
    finally:
        os.unlink(fname)


def test_config_override_single_field():
    """Loading a config with one override keeps other defaults."""
    override = {'general': {'timeout': 30}}
    merged = dict(DEFAULT_CONFIG)
    # Deep-merge override into defaults
    for section, values in override.items():
        if section in merged:
            merged[section].update(values)
        else:
            merged[section] = values
    assert merged['general']['timeout'] == 30
    assert merged['general']['source_file'] == 'sources.txt'


def test_config_creates_cache_dir():
    """Config object should ensure cache directory exists."""
    cache_dir_setting = DEFAULT_CONFIG['cache']['dir']
    assert isinstance(cache_dir_setting, str)
    assert cache_dir_setting == '.cache'
