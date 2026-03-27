import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import re
import os
import html
import gzip
import xml.etree.ElementTree as ET
import concurrent.futures

# Cấu hình lõi
SOURCE_FILE = "sources.txt"
OUTPUT_FILE = "playlist.m3u"
OUTPUT_EPG = "light_epg.xml"
TIMEOUT = 15
STREAM_TIMEOUT = 3 
MAX_WORKERS = 50 

class M3UBuilder:
    def __init__(self):
        self.epg_urls = set()
        self.required_tvg_ids = set()
        self.unique_links = {}  
        self.final_channels = []
        
        # [Nâng cấp 3] Lắp bộ giảm xóc mạng (Auto-Retry)
        self.session = requests.Session()
        retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retries)
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)
        self.session.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})

    # [Nâng cấp 1] AI Rule-based: Chuẩn hóa tên kênh
    def normalize_channel_name(self, name: str) -> str:
        # Xóa các tag rác phổ biến
        name = re.sub(r'(?i)[\[\(\-_\.]?(fhd|hd|sd|1080p|720p|4k|vn|vie|h264|hevc)[\]\)\-_\.]?', '', name)
        # Nối liền VTV 1 thành VTV1, HTV 7 thành HTV7
        name = re.sub(r'(?i)(vtv|htv|vtc|sctv|vtvcab)\s+(\d+)', r'\1\2', name)
        # Xóa ký tự đặc biệt thừa, chỉ giữ lại chữ, số và dấu + (ví dụ K+1)
        name = re.sub(r'[^a-zA-Z0-9\+\s]', '', name)
        return ' '.join(name.split()).strip().upper()

    # [Nâng cấp 1] AI Rule-based: Chuẩn hóa nhóm
    def normalize_group_name(self, group: str) -> str:
        group_lower = group.lower()
        if any(x in group_lower for x in ['thể thao', 'sports', 'bong da', 'bóng đá']): return 'Thể Thao'
        if any(x in group_lower for x in ['vtv']): return 'VTV'
        if any(x in group_lower for x in ['htv']): return 'HTV'
        if any(x in group_lower for x in ['vtc']): return 'VTC'
        if any(x in group_lower for x in ['phim', 'movies']): return 'Phim Truyện'
        if any(x in group_lower for x in ['thiếu nhi', 'kids']): return 'Thiếu Nhi'
        if any(x in group_lower for x in ['tin tức', 'news']): return 'Tin Tức'
        if any(x in group_lower for x in ['k+', 'kplus']): return 'K+'
        if any(x in group_lower for x in ['địa phương', 'local']): return 'Địa Phương'
        return group.title()

    # [Nâng cấp 2] Bóc tách Header ẩn trong link
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

    def add_channel(self, extinf: str, url: str, raw_group: str):
        raw_name = extinf.split(',')[-1].strip()
        
        # Bỏ qua kênh rác
        if len(raw_name) < 2 or re.search(r'[-=_*.]{3,}', raw_name): return

        # Làm sạch tên và nhóm
        clean_name = self.normalize_channel_name(raw_name)
        clean_group = self.normalize_group_name(raw_group)
        
        # Bắt EPG ID
        id_match = re.search(r'tvg-id=["\']([^"\']+)["\']', extinf)
        if id_match:
            self.required_tvg_ids.add(id_match.group(1))

        # Cấu trúc lại dòng EXTINF với tên và nhóm đã được chuẩn hóa
        parts = extinf.rsplit(',', 1)
        new_extinf = parts[0] + ',' + clean_name
        
        if 'group-title="' in new_extinf:
            new_extinf = re.sub(r'group-title="[^"]*"', f'group-title="{clean_group}"', new_extinf)
        else:
            new_extinf = new_extinf.replace("#EXTINF:", f'#EXTINF:-1 group-title="{clean_group}",', 1)

        new_extgrp = f"#EXTGRP:{clean_group}"

        # Lọc trùng bằng URL nguyên bản
        if url not in self.unique_links:
            self.unique_links[url] = {
                'extinf': new_extinf,
                'extgrp': new_extgrp,
                'url': url,           # Link nguyên bản lưu vào file
                'group': clean_group, # Dùng để sắp xếp
                'name': clean_name    # Dùng để sắp xếp
            }

    def process_url(self, source_name: str, url: str):
        print(f"[*] Đang tải danh sách từ: {source_name} ...")
        try:
            res = self.session.get(url, timeout=TIMEOUT)
            res.raise_for_status()
            content = self.clean_html(res.text)
            
            curr_extinf = ""
            curr_group_name = "Khác"

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
                
                elif line.startswith("#EXTGRP"):
                    continue
                    
                elif not line.startswith("#"):
                    if line.startswith(("http", "rtmp")) and curr_extinf:
                        self.add_channel(curr_extinf, line, curr_group_name)
                        curr_extinf = ""

        except Exception as e:
            print(f"  [!] Lỗi: {e}")

    def check_single_link(self, data):
        # Lấy URL thực tế và Header giả mạo để vượt tường lửa
        clean_url, headers = self.parse_url_headers(data['url'])
        try:
            # Gõ cửa server bằng Header nhà đài
            res = self.session.get(clean_url, headers=headers, stream=True, timeout=STREAM_TIMEOUT)
            if res.status_code == 200:
                return data
        except:
            pass
        return None

    def fast_health_check(self):
        print(f"\n[*] Đã gom được {len(self.unique_links)} link UNIQUE. Bắt đầu check SỐNG/CHẾT với hệ thống Anti-Ban...")

        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [executor.submit(self.check_single_link, data) for url, data in self.unique_links.items()]
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                if result:
                    self.final_channels.append(result)

        # Sắp xếp gọn gàng theo Nhóm -> Tên kênh
        self.final_channels.sort(key=lambda x: (x['group'], x['name']))
        print(f"✅ Lọc thành công! Giữ lại {len(self.final_channels)} kênh siêu mượt.")

    def generate_light_epg(self):
        if not self.required_tvg_ids or not self.epg_urls: return None
        print(f"\n[*] Đang tỉa EPG cho {len(self.required_tvg_ids)} kênh từ {len(self.epg_urls)} nguồn...")
        root_out = ET.Element("tv")
        fully_found_ids = set()

        for epg_url in list(self.epg_urls):
            if len(fully_found_ids) >= len(self.required_tvg_ids):
                break
            try:
                # Có Retry bọc lót nên kéo file 50MB an tâm không đứt giữa chừng
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
                f.write(ch['url'] + "\n")
            
        print("\n🏆 Build thành công! Sẵn sàng Push lên Server.")

if __name__ == "__main__":
    M3UBuilder().run()
