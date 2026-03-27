import requests
import re
import os
import html
from io import StringIO

# Cấu hình
SOURCE_FILE = "sources.txt"
OUTPUT_FILE = "playlist.m3u"
TIMEOUT = 15
MAX_EPG_LINKS = 5  # Số lượng EPG chất lượng tối đa muốn giữ lại
WHITELIST_DOMAINS = ["epg.vn", "bepg", "github", "vthanhtivi"] # Các keyword ưu tiên

class M3UBuilder:
    def __init__(self):
        self.epg_urls = set()
        self.channels_buffer = StringIO()
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})

    def clean_html(self, content: str) -> str:
        if any(tag in content.lower() for tag in ["<html", "<body", "<br"]):
            content = re.sub(r'<(br|p|div)\s*/?>', '\n', content, flags=re.IGNORECASE)
            content = re.sub(r'</(p|div)>', '\n', content, flags=re.IGNORECASE)
            content = re.sub(r'<[^>]+>', '', content)
            return html.unescape(content)
        return content

    def filter_quality_epgs(self) -> list:
        print(f"\n[*] Đang phân tích và chọn lọc EPGs (Tối đa {MAX_EPG_LINKS})...")
        valid_epgs = []
        seen_sizes = set()

        # Sắp xếp: URL nào chứa keyword trong Whitelist sẽ được đưa lên check trước
        sorted_urls = sorted(
            list(self.epg_urls), 
            key=lambda u: not any(w in u.lower() for w in WHITELIST_DOMAINS)
        )

        for url in sorted_urls:
            if len(valid_epgs) >= MAX_EPG_LINKS:
                break
            
            try:
                # Dùng HEAD để check header, không tải body
                res = self.session.head(url, timeout=5, allow_redirects=True)
                
                if res.status_code == 200:
                    file_size = res.headers.get('Content-Length')
                    
                    # Chống trùng lặp nội dung dựa vào kích thước file
                    if file_size and int(file_size) > 1024: # Bỏ qua các file lỗi quá nhỏ (<1KB)
                        if file_size in seen_sizes:
                            print(f"  [-] Bỏ qua (Trùng nội dung): {url}")
                            continue
                        seen_sizes.add(file_size)
                    
                    valid_epgs.append(url)
                    print(f"  [+] Sống & Unique: {url} (Size: {file_size or 'Unknown'})")
                else:
                    print(f"  [-] Lỗi {res.status_code}: {url}")
            except requests.RequestException:
                print(f"  [-] Timeout/Die: {url}")

        return valid_epgs

    def process_url(self, source_name: str, url: str):
        print(f"[*] Đang tải kênh từ: {source_name} ...")
        try:
            res = self.session.get(url, timeout=TIMEOUT)
            res.raise_for_status()
            
            content = self.clean_html(res.text)
            skip_channel = False
            
            for line in content.splitlines():
                line = line.strip()
                if not line: continue

                # 1. Thu thập EPG
                if line.startswith("#EXTM3U"):
                    tvg_match = re.search(r'(?:x-tvg-url|url-tvg)="([^"]*)"', line, re.IGNORECASE)
                    if tvg_match:
                        for epg in tvg_match.group(1).split(','):
                            if epg.strip(): self.epg_urls.add(epg.strip())
                    continue

                # 2. Xử lý Kênh
                if line.startswith("#EXTINF"):
                    channel_name = line.split(',')[-1].strip()
                    if re.search(r'[-=_*.]{3,}', channel_name) or len(channel_name) < 2:
                        skip_channel = True
                        continue
                    
                    skip_channel = False
                    grp_match = re.search(r'group-title="([^"]*)"', line)
                    orig_group = grp_match.group(1) if grp_match else "Ungrouped"
                    new_group = f"{source_name} | {orig_group}"

                    if grp_match:
                        line = re.sub(r'group-title="[^"]*"', f'group-title="{new_group}"', line)
                    else:
                        line = line.replace("#EXTINF:", f'#EXTINF:-1 group-title="{new_group}",', 1)
                    
                    self.channels_buffer.write(line + "\n")
                    self.channels_buffer.write(f"#EXTGRP:{new_group}\n")

                # 3. Ghi Link
                elif not line.startswith("#") and not skip_channel:
                    if line.startswith(("http", "rtmp")):
                        self.channels_buffer.write(line + "\n")

        except Exception as e:
            print(f"  [!] Lỗi: {e}")

    def run(self):
        if not os.path.exists(SOURCE_FILE):
            print(f"Không tìm thấy file {SOURCE_FILE}")
            return

        with open(SOURCE_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"): continue
                
                parts = re.split(r'[,|]', line, 1)
                if len(parts) == 2 and parts[1].strip().startswith("http"):
                    self.process_url(parts[0].strip(), parts[1].strip())

        # Xử lý và ghi file
        final_epgs = self.filter_quality_epgs()
        
        header = "#EXTM3U"
        if final_epgs:
            header += f' x-tvg-url="{",".join(final_epgs)}"'
            
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            f.write(header + "\n")
            f.write(self.channels_buffer.getvalue())
            
        print(f"\n✅ Hoàn tất! Đã lưu playlist với {len(final_epgs)} EPGs chất lượng cao.")

if __name__ == "__main__":
    M3UBuilder().run()
