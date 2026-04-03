import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import re
import os
import html
import gzip
import xml.etree.ElementTree as ET
import concurrent.futures
import unicodedata

SOURCE_FILE = "sources.txt"
OUTPUT_FILE = "playlist.m3u"
OUTPUT_EPG = "light_epg.xml"
TIMEOUT = 15
STREAM_TIMEOUT = 3 
MAX_WORKERS = 50 

SPAM_KEYWORDS = ['mời quý khán giả', 'thông báo', 'tạm ngưng', 'bảo trì', 'kênh dự phòng']

GROUP_PRIORITY = {
    'VTV': 1, 'HTV': 2, 'VTC': 3, 'VTVCAB / ON': 4, 'VTVPRIME': 5, 
    'K+': 6, 'THỂ THAO': 7, 'ĐỊA PHƯƠNG': 12
}

class M3UBuilder:
    def __init__(self):
        self.epg_urls = set()
        self.required_tvg_ids = set()
        self.unique_links = {}  
        self.final_channels = []
        self.name_to_tvg_id = {} 
        
        self.session = requests.Session()
        retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retries)
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)
        self.session.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})

    def get_sort_key(self, channel):
        group = channel['group'].upper()
        priority = GROUP_PRIORITY.get(group, 99)
        name = channel['name']
        nat_key = [int(c) if c.isdigit() else c.lower() for c in re.split(r'(\d+)', name)]
        return (priority, group, nat_key)

    def normalize_channel_name(self, name: str) -> str:
        name = re.sub(r'(?i)[\[\(\-_\.]?\b(fhd|hd|sd|1080p|720p|4k|vn|vie|h264|hevc)\b[\]\)\-_\.]?', ' ', name)
        name = re.sub(r'(?i)(vtv|htv|vtc|sctv|vtvcab)\s+(\d+)', r'\1\2', name)
        name = re.sub(r'[^\w\s\+]', '', name)
        return ' '.join(name.split()).strip().upper()

    def smart_grouping(self, raw_group: str, clean_name: str) -> str:
        g_lower = raw_group.lower()
        n_lower = clean_name.lower()
        if 'vtv' in n_lower and 'cab' not in n_lower: return 'VTV'
        if 'htv' in n_lower: return 'HTV'
        if 'vtc' in n_lower: return 'VTC'
        if 'k+' in n_lower: return 'K+'
        if any(x in g_lower for x in ['địa phương', 'tỉnh', 'local']): return 'Địa Phương'
        return raw_group.strip().title() if raw_group else 'Khác'

    def parse_url_headers(self, url: str):
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        clean_url = url
        if '|' in url:
            parts = url.split('|')
            clean_url = parts[0]
            for part in parts[1:]:
                if '=' in part:
                    k, v = part.split('=', 1)
                    headers[k.strip()] = v.strip()
        return clean_url, headers

    def add_channel(self, extinf: str, url: str, raw_group: str, extra_tags: list):
        clean_name = self.normalize_channel_name(extinf.split(',')[-1])
        if len(clean_name) < 2: return
        
        # Lưu ID xịn nếu có để "phát chẩn" cho các link khác cùng tên
        id_match = re.search(r'tvg-id=["\']([^"\']+)["\']', extinf, re.I)
        if id_match:
            self.name_to_tvg_id[clean_name] = id_match.group(1).strip()
            self.required_tvg_ids.add(id_match.group(1).strip())

        if url not in self.unique_links:
            self.unique_links[url] = {
                'original_extinf': extinf,
                'url': url,
                'name': clean_name,
                'group': self.smart_grouping(raw_group, clean_name),
                'extra_tags': extra_tags
            }

    def check_single_link(self, data):
        clean_url, headers = self.parse_url_headers(data['url'])
        try:
            res = self.session.get(clean_url, headers=headers, stream=True, timeout=STREAM_TIMEOUT)
            if res.status_code == 200: return data
        except: pass
        return None

    def run(self):
        if not os.path.exists(SOURCE_FILE): return
        with open(SOURCE_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                parts = re.split(r'[,|]', line.strip(), 1)
                if len(parts) == 2 and parts[1].strip().startswith("http"):
                    self.process_source(parts[0].strip(), parts[1].strip())

        # Check sống chết
        working = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [executor.submit(self.check_single_link, d) for d in self.unique_links.values()]
            for f in concurrent.futures.as_completed(futures):
                res = f.result()
                if res: working.append(res)
        
        working.sort(key=self.get_sort_key)
        
        # Xuất file
        header = '#EXTM3U x-tvg-url="https://raw.githubusercontent.com/hoangxg4/mix-iptv/main/light_epg.xml"'
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            f.write(header + "\n")
            for ch in working:
                best_id = self.name_to_tvg_id.get(ch['name'], "")
                # Gom về 1 tên: Dùng ch['name'] trực tiếp cho phần hiển thị
                line = f'#EXTINF:-1 tvg-id="{best_id}" tvg-name="{ch["name"]}" group-title="{ch["group"]}",{ch["name"]}'
                f.write(line + "\n")
                f.write(f"#EXTGRP:{ch['group']}\n")
                for t in ch['extra_tags']: f.write(t + "\n")
                f.write(ch['url'] + "\n")
        print(f"✅ Xong! Đã gom {len(working)} link vào các tên kênh chuẩn.")

    def process_source(self, name, url):
        try:
            res = self.session.get(url, timeout=TIMEOUT)
            content = res.text
            curr_extinf, curr_grp, extra_tags = "", "Khác", []
            for line in content.splitlines():
                line = line.strip()
                if line.startswith("#EXTINF"):
                    curr_extinf = line
                    m = re.search(r'group-title="([^"]*)"', line)
                    if m: curr_grp = m.group(1)
                    extra_tags = []
                elif line.startswith("#EXTM3U"):
                    m = re.search(r'url-tvg="([^"]*)"', line, re.I)
                    if m: [self.epg_urls.add(e.strip()) for e in m.group(1).split(',') if e.strip()]
                elif line.startswith("#") and curr_extinf: extra_tags.append(line)
                elif not line.startswith("#") and line.startswith("http") and curr_extinf:
                    self.add_channel(curr_extinf, line, curr_grp, extra_tags)
                    curr_extinf = ""
        except: pass

if __name__ == "__main__":
    M3UBuilder().run()
