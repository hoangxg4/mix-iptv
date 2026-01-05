import json
import requests
import re

# Tên file đầu ra
OUTPUT_FILE = "playlist.m3u"

def merge_m3u():
    # Biến lưu trữ nội dung các kênh
    channels_content = ""
    # Biến lưu trữ danh sách các link EPG (dùng set để tránh trùng lặp)
    epg_urls = set()
    
    try:
        # Đọc danh sách nguồn từ file json
        with open('sources.json', 'r', encoding='utf-8') as f:
            sources = json.load(f)
            
        for source in sources:
            group_name = source.get('name', 'Ungrouped')
            url = source.get('url')
            
            if not url: continue

            try:
                print(f"Dang tai: {group_name}")
                response = requests.get(url, timeout=15)
                
                if response.status_code == 200:
                    content = response.text
                    lines = content.splitlines()
                    
                    for line in lines:
                        line = line.strip()
                        if not line: continue
                        
                        # 1. XỬ LÝ HEADER ĐỂ LẤY EPG
                        if line.startswith("#EXTM3U"):
                            # Tìm kiếm x-tvg-url hoặc url-tvg
                            # Regex này tìm nội dung trong ngoặc kép sau x-tvg-url=
                            tvg_match = re.search(r'(?:x-tvg-url|url-tvg)="([^"]*)"', line, re.IGNORECASE)
                            if tvg_match:
                                # Nếu trong 1 file có nhiều link EPG cách nhau bằng dấu phẩy, tách ra
                                found_urls = tvg_match.group(1).split(',')
                                for epg in found_urls:
                                    if epg.strip():
                                        epg_urls.add(epg.strip())
                            continue # Đã lấy xong EPG, bỏ qua dòng này
                        
                        # 2. XỬ LÝ THÔNG TIN KÊNH
                        if line.startswith("#EXTINF"):
                            # Logic đổi tên Group như cũ
                            if 'group-title="' in line:
                                line = re.sub(r'group-title="[^"]*"', f'group-title="{group_name}"', line)
                            else:
                                comma_index = line.find(',')
                                if comma_index != -1:
                                    line = line[:comma_index] + f' group-title="{group_name}"' + line[comma_index:]
                                else:
                                    line = line + f' group-title="{group_name}"'
                            
                            channels_content += line + "\n"
                            channels_content += f"#EXTGRP:{group_name}\n"
                        
                        elif line.startswith("#EXTGRP"):
                            continue
                        else:
                            # Link stream hoặc metadata khác
                            channels_content += line + "\n"
                            
                else:
                    print(f"Lỗi tải {url}: {response.status_code}")
            except Exception as e:
                print(f"Lỗi kết nối {url}: {e}")

        # 3. TẠO HEADER TỔNG HỢP
        header = "#EXTM3U"
        if epg_urls:
            # Nối các link EPG lại bằng dấu phẩy
            combined_epg = ",".join(epg_urls)
            header += f' x-tvg-url="{combined_epg}"'
        
        final_content = header + "\n" + channels_content

        # Ghi ra file
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            f.write(final_content)
            
        print(f"Đã gộp thành công! Tổng hợp {len(epg_urls)} nguồn EPG.")
        
    except Exception as e:
        print(f"Có lỗi xảy ra: {e}")

if __name__ == "__main__":
    merge_m3u()
