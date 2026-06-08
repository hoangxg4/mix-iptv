"""Tests for Cache class."""
import os
import json
import time
import asyncio
import tempfile
import shutil
import hashlib
import pytest


@pytest.fixture
def cache_dir():
    """Provide a temporary cache directory."""
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d)


@pytest.mark.asyncio
async def test_cache_set_get(cache_dir):
    """A value stored in cache can be retrieved."""
    from cache import Cache
    c = Cache(cache_dir=cache_dir)
    await c.set('test_key', {'data': 42})
    val = await c.get('test_key')
    assert val == {'data': 42}


@pytest.mark.asyncio
async def test_cache_miss_returns_none(cache_dir):
    """Getting a non-existent key returns None."""
    from cache import Cache
    c = Cache(cache_dir=cache_dir)
    val = await c.get('nonexistent')
    assert val is None


@pytest.mark.asyncio
async def test_cache_ttl_expiry(cache_dir):
    """Cache entry past TTL returns None."""
    from cache import Cache
    c = Cache(cache_dir=cache_dir, default_ttl=0)  # 0s TTL = immediate expiry
    await c.set('expiry_key', 'data')
    await asyncio.sleep(0.01)
    val = await c.get('expiry_key')
    assert val is None


@pytest.mark.asyncio
async def test_cache_delete(cache_dir):
    """Deleted cache entry returns None."""
    from cache import Cache
    c = Cache(cache_dir=cache_dir)
    await c.set('del_key', 'data')
    await c.delete('del_key')
    val = await c.get('del_key')
    assert val is None


@pytest.mark.asyncio
async def test_cache_clear(cache_dir):
    """Clearing cache removes all entries."""
    from cache import Cache
    c = Cache(cache_dir=cache_dir)
    await c.set('a', 1)
    await c.set('b', 2)
    await c.clear()
    assert await c.get('a') is None
    assert await c.get('b') is None


@pytest.mark.asyncio
async def test_cache_content_hash_key(cache_dir):
    """Cache generates consistent hash-based keys for content."""
    from cache import Cache
    c = Cache(cache_dir=cache_dir)
    url = "https://example.com/playlist.m3u8"
    key = c.make_content_hash_key(url)
    assert isinstance(key, str)
    assert len(key) == 64  # SHA256 hex
    # Same URL -> same key
    assert c.make_content_hash_key(url) == key
    # Different URL -> different key
    assert c.make_content_hash_key(url + "?a=1") != key


@pytest.mark.asyncio
async def test_cache_etag_storage(cache_dir):
    """ETag/Last-Modified headers can be stored and retrieved."""
    from cache import Cache
    c = Cache(cache_dir=cache_dir)
    url = "https://example.com/epg.xml.gz"
    headers = {"etag": '"abc123"', "last-modified": "Mon, 01 Jan 2024 00:00:00 GMT"}
    await c.store_headers(url, headers)
    retrieved = await c.get_headers(url)
    assert retrieved == headers


@pytest.mark.asyncio
async def test_cache_etag_miss(cache_dir):
    """Non-cached URL returns empty dict for headers."""
    from cache import Cache
    c = Cache(cache_dir=cache_dir)
    headers = await c.get_headers("https://example.com/nonexistent")
    assert headers == {}


@pytest.mark.asyncio
async def test_cache_content_hash_consistency(cache_dir):
    """make_content_hash_key is deterministic."""
    from cache import Cache
    c = Cache(cache_dir=cache_dir)
    url = "https://example.com/data"
    h1 = c.make_content_hash_key(url)
    h2 = c.make_content_hash_key(url)
    assert h1 == h2
