import json
import os
import sys

SOURCE_FILE = 'sources.json'

def bulk_add_sources(raw_data):
    if not raw_data:
        print("Không có dữ liệu đầu vào.")
        return

    # Đọc dữ liệu cũ
    if os.path.exists(SOURCE_FILE):
        try:
            with open(SOURCE_FILE, 'r', encoding='utf-8') as f:
                sources = json.load(f)
        except json.JSONDecodeError:
            sources = []
    else:
        sources = []

    # Tạo set các URL đã có để kiểm tra trùng nhanh hơn
    existing_urls = {s.get('url') for s in sources}
    
    count_added = 0
    lines = raw_data.splitlines()

    for line in lines:
        line = line.strip()
        if not line: continue

        # Hỗ trợ phân cách bằng dấu gạch đứng | hoặc dấu phẩy ,
        if '|' in line:
            parts = line.split('|', 1)
        elif ',' in line:
            parts = line.split(',', 1)
        else:
            print(f"Bỏ qua dòng sai định dạng: {line}")
            continue

        name = parts[0].strip()
        url = parts[1].strip()

        if url in existing_urls:
            print(f"Đã tồn tại, bỏ qua: {name}")
            continue

        sources.append({
            "name": name,
            "url": url
        })
        existing_urls.add(url)
        count_added += 1
        print(f"Đã thêm: {name}")

    # Ghi lại file
    if count_added > 0:
        with open(SOURCE_FILE, 'w', encoding='utf-8') as f:
            json.dump(sources, f, indent=2, ensure_ascii=False)
        print(f"--- Tổng cộng đã thêm {count_added} nguồn mới ---")
    else:
        print("Không có nguồn nào mới được thêm.")

if __name__ == "__main__":
    # Lấy dữ liệu từ biến môi trường (an toàn hơn tham số dòng lệnh với chuỗi dài)
    input_data = os.environ.get('INPUT_DATA', '')
    bulk_add_sources(input_data)
