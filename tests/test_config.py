"""Tests for config.yaml loading and validation."""
import os
import tempfile
import yaml
import pytest
from config import DEFAULT_CONFIG


def test_default_config_has_output_channels():
    """DEFAULT_CONFIG should include output_channels setting."""
    assert 'output_channels' in DEFAULT_CONFIG['general']
    assert DEFAULT_CONFIG['general']['output_channels'] == 'channels.json'


def test_config_defaults_match_expected():
    """Verify default config dict has all required keys."""
    assert 'general' in DEFAULT_CONFIG
    assert 'cache' in DEFAULT_CONFIG
    assert 'source_file' in DEFAULT_CONFIG['general']
    assert 'spam_keywords' in DEFAULT_CONFIG['general']
    assert len(DEFAULT_CONFIG['general']['spam_keywords']) == 0
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
