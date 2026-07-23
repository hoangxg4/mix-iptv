"""Integration tests for config.yaml loading."""
import os
import yaml
import pytest
import tempfile


def test_config_yaml_exists():
    """config.yaml must exist at project root."""
    assert os.path.exists('config.yaml'), "config.yaml not found"


def test_config_yaml_is_valid():
    """config.yaml must be valid YAML with required keys."""
    with open('config.yaml', 'r') as f:
        cfg = yaml.safe_load(f)
    assert cfg is not None
    assert 'general' in cfg
    assert 'cache' in cfg
    assert 'source_file' in cfg['general']
    assert 'dir' in cfg['cache']
    assert cfg['cache']['dir'] == '.cache'


def test_load_config_function():
    """load_config returns defaults when no config file exists."""
    from config import load_config, DEFAULT_CONFIG
    # Temporarily hide config.yaml
    with tempfile.NamedTemporaryFile() as tmp:
        # Rename config out of the way
        if os.path.exists('config.yaml'):
            os.rename('config.yaml', 'config.yaml.bak')
        try:
            cfg = load_config('nonexistent.yaml')
            # Should have all default keys
            assert cfg['general']['source_file'] == 'sources.txt'
            assert cfg['cache']['epg_ttl'] == 3600
        finally:
            if os.path.exists('config.yaml.bak'):
                os.rename('config.yaml.bak', 'config.yaml')


def test_config_spam_keywords_inherited():
    """SPAM_KEYWORDS module constant matches config."""
    from config import SPAM_KEYWORDS, load_config
    cfg = load_config()
    assert SPAM_KEYWORDS == cfg['general']['spam_keywords']
