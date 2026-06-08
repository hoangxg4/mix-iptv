"""Async file-based cache with TTL, ETag/Last-Modified, and content hash support."""
import os
import json
import time
import hashlib
import asyncio


def _read_file(path):
    """Synchronous file read (run in executor)."""
    with open(path, 'r') as f:
        return f.read()


def _write_file(path, data):
    """Synchronous file write (run in executor)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write(data)


def _remove_file(path):
    """Synchronous file remove (run in executor)."""
    if os.path.exists(path):
        os.remove(path)
        # Clean up empty parent dirs
        parent = os.path.dirname(path)
        try:
            os.rmdir(parent)
        except OSError:
            pass
        grandparent = os.path.dirname(parent)
        try:
            os.rmdir(grandparent)
        except OSError:
            pass


class Cache:
    """Async file-based cache for EPG and M3U8 content.

    Features:
    - TTL-based expiry per entry
    - ETag/Last-Modified header storage (for conditional HTTP requests)
    - Content hash key generation (for M3U8 link deduplication)
    - On-disk JSON storage in a configurable directory
    """

    def __init__(self, cache_dir='.cache', default_ttl=3600):
        self.cache_dir = cache_dir
        self.default_ttl = default_ttl
        self._meta = {}  # in-memory index: key -> {path, expires_at}

    async def _run_sync(self, func, *args):
        """Run a synchronous function in a thread executor."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, func, *args)

    def _ensure_dir(self):
        """Ensure cache directory exists."""
        os.makedirs(self.cache_dir, exist_ok=True)

    def _meta_path(self):
        return os.path.join(self.cache_dir, '_meta.json')

    def _entry_path(self, key):
        # Use hex-safe filename from SHA-256 of key
        safe = hashlib.sha256(key.encode()).hexdigest()
        return os.path.join(self.cache_dir, safe[:2], safe[2:4], safe + '.json')

    def _load_meta_sync(self):
        if not self._meta:
            meta_file = self._meta_path()
            if os.path.exists(meta_file):
                try:
                    with open(meta_file, 'r') as f:
                        self._meta = json.load(f)
                except (json.JSONDecodeError, OSError):
                    self._meta = {}
        return self._meta

    async def _load_meta(self):
        return await self._run_sync(self._load_meta_sync)

    def _save_meta_sync(self):
        self._ensure_dir()
        meta_file = self._meta_path()
        with open(meta_file, 'w') as f:
            json.dump(self._meta, f)

    async def _save_meta(self):
        await self._run_sync(self._save_meta_sync)

    async def _is_expired(self, key):
        meta = await self._load_meta()
        entry = meta.get(key)
        if entry is None:
            return True
        expires_at = entry.get('expires_at', 0)
        return time.time() > expires_at

    async def get(self, key):
        """Retrieve a cached value. Returns None if missing or expired."""
        if await self._is_expired(key):
            await self.delete(key)
            return None
        meta = await self._load_meta()
        entry = meta.get(key)
        if not entry:
            return None
        path = entry.get('path')
        if not path or not os.path.exists(path):
            await self.delete(key)
            return None
        try:
            data = await self._run_sync(_read_file, path)
            return json.loads(data)
        except (json.JSONDecodeError, OSError, FileNotFoundError):
            await self.delete(key)
            return None

    async def set(self, key, value, ttl=None):
        """Store a value in cache with optional TTL override."""
        self._ensure_dir()
        ttl = ttl if ttl is not None else self.default_ttl
        meta = await self._load_meta()
        path = self._entry_path(key)
        await self._run_sync(_write_file, path, json.dumps(value, ensure_ascii=False))
        meta[key] = {
            'path': path,
            'expires_at': time.time() + ttl,
            'created_at': time.time(),
        }
        await self._save_meta()

    async def delete(self, key):
        """Remove a key from cache."""
        meta = await self._load_meta()
        entry = meta.pop(key, None)
        if entry:
            path = entry.get('path')
            if path:
                await self._run_sync(_remove_file, path)
            await self._save_meta()

    async def clear(self):
        """Remove all cache entries."""
        meta = await self._load_meta()
        for key, entry in list(meta.items()):
            path = entry.get('path')
            if path and os.path.exists(path):
                try:
                    await self._run_sync(os.remove, path)
                except OSError:
                    pass
        self._meta = {}
        meta_file = self._meta_path()
        if os.path.exists(meta_file):
            try:
                await self._run_sync(os.remove, meta_file)
            except OSError:
                pass
        # Remove empty subdirs
        if os.path.exists(self.cache_dir):
            for root, dirs, files in os.walk(self.cache_dir, topdown=False):
                for d in dirs:
                    try:
                        os.rmdir(os.path.join(root, d))
                    except OSError:
                        pass

    def make_content_hash_key(self, url):
        """Generate a deterministic SHA-256 content hash key for a URL."""
        return hashlib.sha256(url.encode('utf-8')).hexdigest()

    async def store_headers(self, url, headers):
        """Store ETag/Last-Modified headers for a URL."""
        key = f'_headers:{url}'
        await self.set(key, headers, ttl=self.default_ttl * 24)

    async def get_headers(self, url):
        """Retrieve stored ETag/Last-Modified headers for a URL."""
        key = f'_headers:{url}'
        result = await self.get(key)
        return result if result is not None else {}
