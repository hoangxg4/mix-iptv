import requests
import re
import os
import html  # Thêm thư viện này để xử lý HTML entities

# Cấu hình file
SOURCE_FILE = "sources.txt"
OUTPUT_FILE = "playlist.m3u"

def merge_m3u():
    channels_content = ""
    epg_urls = set()
    
    skip_channel = False
    
    if not os.path.exists(SOURCE_FILE):
        print(f"Không tìm thấy file {SOURCE_FILE}")
        return

    try:
        with open(SOURCE_FILE, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"): continue
                
            if "," in line:
                parts = line.split(",", 1)
            elif "|" in line:
                parts = line.split("|", 1)
            else:
                continue
                
            source_name = parts[0].strip()
            url = parts[1].strip()
            
            if not url.startswith("http"): continue

            try:
                print(f"Dang tai: {source_name} ...")
                # Thêm User-Agent để tránh bị chặn bởi một số trang web
                headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
                response = requests.get(url, headers=headers, timeout=15)
                
                if response.status_code == 200:
                    content = response.text
                    
                    # --- BƯỚC XỬ LÝ HTML TỰ ĐỘNG (MỚI) ---
                    # Nếu nội dung chứa thẻ HTML cơ bản, tiến hành làm sạch
                    if "<html" in content.lower() or "<body" in content.lower() or "<br" in content.lower():
                        print(f"  -> Phát hiện HTML, đang làm sạch...")
                        # Thay thế <br>, </p>, </div> thành xuống dòng
                        content = re.sub(r'<(br|p|div)\s*/?>', '\n', content, flags=re.IGNORECASE)
                        content = re.sub(r'</(p|div)>', '\n', content, flags=re.IGNORECASE)
                        # Xóa tất cả thẻ HTML còn lại
                        content = re.sub(r'<[^>]+>', '', content)
                        # Giải mã ký tự đặc biệt (&amp; -> &)
                        content = html.unescape(content)
                    # -------------------------------------

                    file_lines = content.splitlines()
                    
                    for f_line in file_lines:
                        f_line = f_line.strip()
                        if not f_line: continue
                        
                        # 1. LẤY EPG
                        if f_line.startswith("#EXTM3U"):
                            tvg_match = re.search(r'(?:x-tvg-url|url-tvg)="([^"]*)"', f_line, re.IGNORECASE)
                            if tvg_match:
                                found_urls = tvg_match.group(1).split(',')
                                for epg in found_urls:
                                    if epg.strip():
                                        epg_urls.add(epg.strip())
                            continue
                        
                        # 2. XỬ LÝ #EXTINF
                        if f_line.startswith("#EXTINF"):
                            skip_channel = False
                            channel_name = f_line.split(',')[-1].strip()
                            
                            # Bộ lọc rác
                            if re.search(r'[-=_*.]{3,}', channel_name) or len(channel_name) < 2:
                                skip_channel = True 
                                continue
                            
                            # Xử lý Group
                            original_group = "Ungrouped"
                            grp_match = re.search(r'group-title="([^"]*)"', f_line)
                            if grp_match:
                                original_group = grp_match.group(1)
                            
                            new_group = f"{source_name} | {original_group}"
                            
                            if 'group-title="' in f_line:
                                f_line = re.sub(r'group-title="[^"]*"', f'group-title="{new_group}"', f_line)
                            else:
                                comma_index = f_line.find(',')
                                if comma_index != -1:
                                    f_line = f_line[:comma_index] + f' group-title="{new_group}"' + f_line[comma_index:]
                                else:
                                    f_line = f_line + f' group-title="{new_group}"'
                            
                            channels_content += f_line + "\n"
                            channels_content += f"#EXTGRP:{new_group}\n"
                        
                        elif f_line.startswith("#EXTGRP"):
                            continue
                        
                        # 3. XỬ LÝ LINK
                        elif not f_line.startswith("#"):
                            if skip_channel: continue
                            # Chỉ lấy dòng nào thực sự là link (có http hoặc rtmp)
                            if f_line.startswith("http") or f_line.startswith("rtmp"):
                                channels_content += f_line + "\n"
                        
                else:
                    print(f" -> Lỗi tải: {response.status_code}")
            except Exception as e:
                print(f" -> Lỗi kết nối: {e}")

        # GHI FILE
        header = "#EXTM3U"
        if epg_urls:
            combined_epg = ",".join(epg_urls)
            header += f' x-tvg-url="{combined_epg}"'
        
        final_content = header + "\n" + channels_content

        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            f.write(final_content)
            
        print("✅ Đã gộp file, làm sạch HTML và lọc rác thành công!")
        
    except Exception as e:
        print(f"Có lỗi hệ thống: {e}")

if __name__ == "__main__":
    merge_m3u()
