import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import re
import os
import html
import gzip
import xml.etree.ElementTree as ET
import concurrent.futures

SOURCE_FILE = "sources.txt"
OUTPUT_FILE = "playlist.m3u"
OUTPUT_EPG = "light_epg.xml"
TIMEOUT = 15
STREAM_TIMEOUT = 3 
MAX_WORKERS = 50 

# Thêm VTVPRIME vào danh sách ưu tiên hàng đầu
GROUP_PRIORITY = {
    'VTVPRIME': 1,
    'VTV': 2,
    'HTV': 3,
    'VTC': 4,
    'VTVCAB / ON': 5,
    'K+': 6,
    'THỂ THAO': 7,
    'TIN TỨC': 8,
    'PHIM TRUYỆN': 9,
    'GIẢI TRÍ': 10,
    'THIẾU NHI': 11,
    'ĐỊA PHƯƠNG': 12,
    'QUỐC TẾ': 13
    # Mọi nhóm khác (của nguồn) sẽ tự động nhận độ ưu tiên 99
}

class M3UBuilder:
    def __init__(self):
        self.epg_urls = set()
        self.required_tvg_ids = set()
        self.unique_links = {}  
        self.final_channels = []
        
        self.session = requests.Session()
        retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retries)
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)
        self.session.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})

    def natural_sort_key(self, text):
        return [int(c) if c.isdigit() else c for c in re.split(r'(\d+)', text)]

    def normalize_channel_name(self, name: str) -> str:
        # Dùng \b để chỉ xóa tag rác khi nó đứng độc lập, không xóa nhầm chữ trong tên (vd: "vie" trong "Việt")
        name = re.sub(r'(?i)[\[\(\-_\.]?\b(fhd|hd|sd|1080p|720p|4k|vn|vie|h264|hevc)\b[\]\)\-_\.]?', ' ', name)
        name = re.sub(r'(?i)(vtv|htv|vtc|sctv|vtvcab)\s+(\d+)', r'\1\2', name)
        
        # [FIX TIẾNG VIỆT] Dùng \w để giữ lại chữ cái có dấu Unicode, số và khoảng trắng
        name = re.sub(r'[^\w\s\+]', '', name)
        
        return ' '.join(name.split()).strip().upper()

    def smart_grouping(self, raw_group: str, clean_name: str) -> str:
        g_lower = raw_group.lower()
        n_lower = clean_name.lower()

        # 1. Bắt VTVPRIME tách biệt hoàn toàn
        if 'vtvprime' in n_lower or 'vtvprime' in g_lower:
            return 'VTVPRIME'

        # 2. Bắt VTVCab và ON
        if 'vtvcab' in g_lower or 'vtvcab' in n_lower or n_lower.startswith('on ') or n_lower.startswith('on+'):
            if any(x in n_lower for x in ['thể thao', 'sports', 'football']): return 'Thể Thao'
            return 'VTVCab / ON'

        # 3. Bắt VTV xịn
        if re.match(r'^vtv\s?\d', n_lower) or 'vtv cần thơ' in n_lower or n_lower == 'vtv':
            return 'VTV'

        # 4. Các kênh quốc dân khác
        if n_lower.startswith('k+'): return 'K+'
        if re.match(r'^htv\s?\d', n_lower) or n_lower == 'htv': return 'HTV'
        if re.match(r'^vtc\s?\d', n_lower) or n_lower == 'vtc': return 'VTC'

        # 5. Gom nhóm logic cơ bản
        if any(x in g_lower for x in ['thể thao', 'sports', 'bong da', 'bóng đá']): return 'Thể Thao'
        if any(x in g_lower for x in ['phim', 'movies', 'cinema']): return 'Phim Truyện'
        if any(x in g_lower for x in ['thiếu nhi', 'kids', 'cartoon']): return 'Thiếu Nhi'
        if any(x in g_lower for x in ['tin tức', 'news']): return 'Tin Tức'
        
        # Nhận diện nhầm chữ VTV trong các nhóm khác
        if 'vtv' in g_lower and 'cab' not in g_lower and 'prime' not in g_lower: return 'VTV'
        if 'htv' in g_lower: return 'HTV'
        if 'vtc' in g_lower: return 'VTC'

        # [FIX NHÓM CŨ] Nếu không trúng quy tắc nào, trả lại đúng tên nhóm gốc của nguồn
        return raw_group.strip().title() if raw_group.strip() else 'Khác'

    def parse_url_headers(self, url: str):
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        clean_url = url
        if '|' in url:
            parts = url.split('|')
            clean_url = parts[0]
            for part in parts[1:]:
                if '=' in part:
                    k, v = part.split('=', 1)
                    if k.lower() == 'user-agent': headers['User-Agent'] = v
                    elif k.lower() == 'referer': headers['Referer'] = v
        return clean_url, headers

    def clean_html(self, content: str) -> str:
        if any(tag in content.lower() for tag in ["<html", "<body", "<br"]):
            content = re.sub(r'<(br|p|div)\s*/?>', '\n', content, flags=re.IGNORECASE)
            content = re.sub(r'</(p|div)>', '\n', content, flags=re.IGNORECASE)
            content = re.sub(r'<[^>]+>', '', content)
            return html.unescape(content)
        return content

    def add_channel(self, extinf: str, url: str, raw_group: str, extra_tags: list):
        raw_name = extinf.split(',')[-1].strip()
        if len(raw_name) < 2 or re.search(r'[-=_*.]{3,}', raw_name): return

        clean_name = self.normalize_channel_name(raw_name)
        clean_group = self.smart_grouping(raw_group, clean_name)
        
        id_match = re.search(r'tvg-id=["\']([^"\']+)["\']', extinf)
        if id_match:
            self.required_tvg_ids.add(id_match.group(1))

        parts = extinf.rsplit(',', 1)
        new_extinf = parts[0] + ',' + clean_name
        
        if 'group-title="' in new_extinf:
            new_extinf = re.sub(r'group-title="[^"]*"', f'group-title="{clean_group}"', new_extinf)
        else:
            new_extinf = new_extinf.replace("#EXTINF:", f'#EXTINF:-1 group-title="{clean_group}",', 1)

        new_extgrp = f"#EXTGRP:{clean_group}"

        if url not in self.unique_links:
            self.unique_links[url] = {
                'extinf': new_extinf,
                'extgrp': new_extgrp,
                'extra_tags': extra_tags, 
                'url': url,           
                'group': clean_group, 
                'name': clean_name    
            }

    def process_url(self, source_name: str, url: str):
        print(f"[*] Đang tải danh sách từ: {source_name} ...")
        try:
            res = self.session.get(url, timeout=TIMEOUT)
            res.raise_for_status()
            content = self.clean_html(res.text)
            
            curr_extinf = ""
            curr_group_name = "Khác"
            extra_tags = [] 

            for line in content.splitlines():
                line = line.strip()
                if not line: continue

                if line.startswith("#EXTM3U"):
                    tvg_match = re.search(r'(?:x-tvg-url|url-tvg)="([^"]*)"', line, re.IGNORECASE)
                    if tvg_match:
                        for epg in tvg_match.group(1).split(','):
                            if epg.strip(): self.epg_urls.add(epg.strip())
                    continue

                if line.startswith("#EXTINF"):
                    grp_match = re.search(r'group-title="([^"]*)"', line)
                    if grp_match:
                        curr_group_name = grp_match.group(1)
                    curr_extinf = line
                    extra_tags = [] 
                
                elif line.startswith("#EXTGRP"):
                    continue
                
                elif line.startswith("#") and curr_extinf:
                    extra_tags.append(line)
                    
                elif not line.startswith("#"):
                    if line.startswith(("http", "rtmp")) and curr_extinf:
                        self.add_channel(curr_extinf, line, curr_group_name, extra_tags)
                        curr_extinf = ""
                        extra_tags = []

        except Exception as e:
            print(f"  [!] Lỗi: {e}")

    def check_single_link(self, data):
        clean_url, headers = self.parse_url_headers(data['url'])
        try:
            res = self.session.get(clean_url, headers=headers, stream=True, timeout=STREAM_TIMEOUT)
            if res.status_code == 200:
                return data
        except:
            pass
        return None

    def fast_health_check(self):
        total_links = len(self.unique_links)
        print(f"\n[*] Đã gom được {total_links} link UNIQUE. Bắt đầu check SỐNG/CHẾT...")

        checked_count = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [executor.submit(self.check_single_link, data) for url, data in self.unique_links.items()]
            for future in concurrent.futures.as_completed(futures):
                checked_count += 1
                if checked_count % 50 == 0 or checked_count == total_links:
                    print(f"  -> Đã check: {checked_count}/{total_links} links...", flush=True)

                result = future.result()
                if result:
                    self.final_channels.append(result)

        # [FIX SORTING] Sắp xếp theo: Độ ưu tiên nhóm -> Tên nhóm gốc (bảng chữ cái) -> Tên kênh
        self.final_channels.sort(key=lambda x: (
            GROUP_PRIORITY.get(x['group'].upper(), 99), 
            x['group'].upper(), 
            self.natural_sort_key(x['name'])
        ))
        
        print(f"✅ Lọc thành công! Giữ lại {len(self.final_channels)} kênh siêu mượt.", flush=True)

    def generate_light_epg(self):
        if not self.required_tvg_ids or not self.epg_urls: return None
        print(f"\n[*] Đang tỉa EPG cho {len(self.required_tvg_ids)} kênh từ {len(self.epg_urls)} nguồn...")
        root_out = ET.Element("tv")
        fully_found_ids = set()

        for epg_url in list(self.epg_urls):
            if len(fully_found_ids) >= len(self.required_tvg_ids):
                break
            try:
                res = self.session.get(epg_url, timeout=30)
                xml_data = gzip.decompress(res.content) if epg_url.endswith('.gz') else res.content
                root_in = ET.fromstring(xml_data)
                active_ids = set()
                
                for elem in root_in:
                    if elem.tag == 'channel':
                        ch_id = elem.get('id')
                        if ch_id in self.required_tvg_ids and ch_id not in fully_found_ids:
                            root_out.append(elem)
                            active_ids.add(ch_id)
                            fully_found_ids.add(ch_id)
                    elif elem.tag == 'programme' and elem.get('channel') in active_ids:
                        root_out.append(elem)
            except:
                pass

        tree = ET.ElementTree(root_out)
        tree.write(OUTPUT_EPG, encoding='utf-8', xml_declaration=True)
        return OUTPUT_EPG

    def run(self):
        if not os.path.exists(SOURCE_FILE): return

        with open(SOURCE_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                parts = re.split(r'[,|]', line.strip(), 1)
                if len(parts) == 2 and parts[1].strip().startswith("http"):
                    self.process_url(parts[0].strip(), parts[1].strip())

        self.fast_health_check()
        self.generate_light_epg()
        
        header = '#EXTM3U x-tvg-url="https://raw.githubusercontent.com/hoangxg4/mix-iptv/main/light_epg.xml"'
        
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            f.write(header + "\n")
            for ch in self.final_channels:
                f.write(ch['extinf'] + "\n")
                f.write(ch['extgrp'] + "\n")
                for tag in ch['extra_tags']:
                    f.write(tag + "\n")
                f.write(ch['url'] + "\n")
            
        print("\n🏆 Build thành công! Sẵn sàng Push lên Server.", flush=True)

if __name__ == "__main__":
    M3UBuilder().run()
