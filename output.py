"""Output generation functions — standalone with explicit parameters."""
import json
from config import GROUP_PRIORITY


def generate_channels_json(final_playlist, output_channels, logger=None):
    """Generate channels.json following iptvschema.org (Provider -> Group -> Channel -> Source -> Stream -> StreamLink).

    Args:
        final_playlist: List of channel dicts with name, group, url, final_id, final_logo, etc.
        output_channels: Path to write channels.json.
        logger: Optional logger instance for info messages.
    """
    # Build groups from final_playlist
    # Merge case-insensitive duplicate group names (safety net for "Vtv" ≠ "VTV")
    groups_dict = {}
    _group_normalized = {}  # lowercase name -> canonical display name
    _group_candidates = {}  # lowercase name -> [all case-variants seen]
    for ch in final_playlist:
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

    with open(output_channels, 'w', encoding='utf-8') as f:
        json.dump(provider, f, ensure_ascii=False, indent=2)

    if logger:
        logger.info("Đã tạo %s với %d nhóm, %d kênh",
                    output_channels, len(groups), len(final_playlist))


def write_stats_json(final_playlist, path='docs/stats.json', logger=None):
    """Write a tiny stats.json for the web UI (local, no CORS needed).

    Args:
        final_playlist: List of channel dicts from M3UBuilder.
        path: Output path for stats.json.
        logger: Optional logger.
    """
    from datetime import datetime, timezone

    groups = {}
    for ch in final_playlist:
        g = ch.get('group', 'Khác')
        groups.setdefault(g, {})[ch['name']] = ch

    total_links = sum(
        len(ch.get('fallback_urls', [])) + 1
        for ch in final_playlist
    )

    import os
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)

    stats = {
        'groups': len(groups),
        'channels': len(final_playlist),
        'links': total_links,
        'updated': datetime.now(timezone.utc).isoformat(),
    }

    with open(path, 'w', encoding='utf-8') as f:
        json.dump(stats, f, ensure_ascii=False)

    if logger:
        logger.info("Đã tạo %s: %d nhóm, %d kênh, %d links",
                    path, stats['groups'], stats['channels'], stats['links'])


def write_m3u_playlist(final_playlist, output_file, epg_base_url, output_epg):
    """Write the final M3U playlist to disk.

    Args:
        final_playlist: List of channel dicts.
        output_file: Path to the output .m3u file.
        epg_base_url: Base URL for the EPG guide.
        output_epg: EPG filename appended to epg_base_url in the header.
    """
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(f'#EXTM3U url-tvg="{epg_base_url}/{output_epg}" x-tvg-url="{epg_base_url}/{output_epg}"\n')
        for ch in final_playlist:
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
