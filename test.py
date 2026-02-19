import requests
from pathlib import Path

url = "https://uu.gdl.netease.com/UU-macOS-2.8.14.dmg?type=pc&key1=c33a3b893af5c74b386aa273abc7da02&key2=6995c06b"

output_file = Path("UU-macOS-2.8.14.dmg")

headers = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36",
    "Referer": "https://uu.163.com/"
}

with requests.get(url, headers=headers, stream=True) as r:
    r.raise_for_status()
    total = int(r.headers.get("content-length", 0))
    
    with open(output_file, "wb") as f:
        downloaded = 0
        for chunk in r.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)
                downloaded += len(chunk)
                print(f"\rDownloaded {downloaded / 1024 / 1024:.2f} MB", end="")

print("\nDownload complete.")
