#!/usr/bin/env python3
"""IPTV Playlist Generator — entry point."""
import asyncio
from config import load_config
from m3u_builder import M3UBuilder

async def main():
    config = load_config()
    builder = M3UBuilder(config)
    await builder.run()

if __name__ == "__main__":
    asyncio.run(main())
