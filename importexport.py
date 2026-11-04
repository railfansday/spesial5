import sqlite3
import feedparser
import requests
from bs4 import BeautifulSoup
import os
import re
import json
import html as html_mod
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

# ==========================================================================
# KONFIGURASI PATH (PORTABEL & LANGSUNG KE FOLDER UTAMA)
# ==========================================================================
BASE_DIR = Path(__file__).parent.resolve()
DB_PATH = str(BASE_DIR / "database_hasil5" / "database.sqlite")
RSS_FILE = str(BASE_DIR / "rss_list.txt")

# ⚠️ PENTING: File HTML dan sitemap disimpan di folder utama (BASE_DIR)
OUTPUT_DIR = str(BASE_DIR) 

# ⚠️ GANTI DENGAN URL GITHUB PAGES ANDA
BASE_URL = "https://railfansday.github.io/spesial2" 

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

ADSTERRA_CODE = """
<div style="text-align:center; margin:20px 0;">
    <script type="text/javascript">
        atOptions = {'key' : 'f9e40eb3b3cd05f1038aac7b36b33ad2','format' : 'iframe','height' : 90,'width' : 728,'params' : {}};
    </script>
    <script type="text/javascript" src="https://upholdbrilliancerecast.com/f9e40eb3b3cd05f1038aac7b36b33ad2/invoke.js"></script>
</div>
"""

# ==========================================================================
# BAGIAN 1: FUNGSI IMPORT (SCRAPING)
# ==========================================================================
def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS artikel (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            link TEXT UNIQUE, judul TEXT, konten TEXT, gambar TEXT,
            tanggal_publish TEXT, tanggal_update TEXT
        )
    """)
    conn.commit()
    conn.close()

def extract_full_content(url):
    try:
        response = requests.get(url, headers=HEADERS, timeout=5)
        if response.status_code != 200: return None, None, None
        soup = BeautifulSoup(response.text, 'html.parser')

        image_url = None
        og_image = soup.find('meta', property='og:image')
        if og_image: image_url = og_image.get('content')

        paragraphs = soup.find_all('p')
        full_text = []
        for p in paragraphs:
            text = p.get_text().strip()
            if len(text) > 40 and not any(x in text.lower() for x in ['cookie', 'privacy', 'subscribe', 'rights reserved']):
                full_text.append(f"<p>{text}</p>")
        
        return "\n".join(full_text) if full_text else None, image_url, None
    except Exception:
        return None, None, None

def process_single_feed(feed_url):
    feed_url = feed_url.strip()
    if not feed_url or feed_url.startswith("#"): return 0, 0

    baru = 0
    duplikat = 0

    try:
        response = requests.get(feed_url, headers=HEADERS, timeout=8)
        if response.status_code != 200: return 0, 0
        
        feed = feedparser.parse(response.content)
        conn = sqlite3.connect(DB_PATH, timeout=15)
        cursor = conn.cursor()

        for entry in feed.entries:
            link = entry.get('link', '')
            judul = entry.get('title', 'Tanpa Judul')
            if not link: continue

            cursor.execute("SELECT id FROM artikel WHERE link = ?", (link,))
            if cursor.fetchone():
                duplikat += 1
                continue

            full_text_html, image_url, _ = extract_full_content(link)
            if not full_text_html:
                full_text_html = f"<p>{entry.get('summary', 'Konten tidak tersedia.')}</p>"

            tanggal = datetime.now().isoformat()
            img_tag = f'<div style="text-align:center; margin-bottom:20px;"><img src="{image_url}" alt="{judul}" style="max-width:100%; height:auto; border-radius:8px;" loading="lazy"></div>' if image_url else ""
            
            konten_lengkap = f"""
            {img_tag}
            <div class="article-body" style="font-size: 16px; line-height: 1.8; color: #333;">
                {full_text_html}
                <p style="margin-top: 25px;">
                    <a href="{link}" target="_blank" rel="nofollow noopener" style="color: #0d6efd; text-decoration: none; font-weight: bold;">
                        &rarr; Baca artikel asli
                    </a>
                </p>
            </div>
            """

            try:
                cursor.execute(
                    "INSERT INTO artikel (link, judul, konten, gambar, tanggal_publish, tanggal_update) VALUES (?, ?, ?, ?, ?, ?)",
                    (link, judul, konten_lengkap, image_url, tanggal, tanggal)
                )
                conn.commit()
                baru += 1
            except sqlite3.IntegrityError:
                duplikat += 1
            except Exception:
                pass
        conn.close()
    except Exception:
        pass
    return baru, duplikat

def jalankan_scraping():
    if not os.path.exists(RSS_FILE):
        print(f"[ERROR] File {RSS_FILE} tidak ditemukan!")
        return
    init_db()
    with open(RSS_FILE, "r", encoding="utf-8") as f:
        rss_urls = [line.strip() for line in f if line.strip() and not line.startswith("#")]
    
    print(f"[*] Memulai scraping dari {len(rss_urls)} RSS...")
    total_baru, total_duplikat = 0, 0
    
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(process_single_feed, url): url for url in rss_urls}
        for future in as_completed(futures):
            b, d = future.result()
            total_baru += b
            total_duplikat += d

    print(f"[SELESAI SCRAPING] Baru: {total_baru} | Duplikat: {total_duplikat}\n")


# ==========================================================================
# BAGIAN 2: FUNGSI EXPORT (1 ARTIKEL = 1 FILE HTML)
# ==========================================================================
def esc(text):
    return html_mod.escape(str(text)) if text else ""

def jalankan_export_1_per_halaman():
    if not os.path.exists(DB_PATH):
        print("Database tidak ditemukan.")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id, judul, konten, gambar, tanggal_publish, link FROM artikel ORDER BY id DESC")
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        print("Tidak ada artikel.")
        return

    print(f"[*] Membuat halaman HTML (1 artikel per file) di folder utama...")

    # 1. BUAT INDEX (Berisi daftar link ke semua artikel)
    index_html = f"""<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="UTF-8">
    <title>Daftar Berita</title>
    <style>
        body {{ font-family: Arial, sans-serif; padding: 20px; background: #f4f4f9; }}
        .container {{ max-width: 800px; margin: 0 auto; background: #fff; padding: 20px; border-radius: 10px; }}
        ul {{ list-style-type: none; padding: 0; }}
        li {{ margin-bottom: 15px; border-bottom: 1px solid #eee; padding-bottom: 10px; }}
        a {{ text-decoration: none; color: #0d6efd; font-size: 18px; font-weight: bold; }}
        a:hover {{ text-decoration: underline; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>Daftar Berita Terkini</h1>
        <ul>
    """
    
    for item in rows:
        artikel_id, judul, konten, gambar, tanggal, link = item
        index_html += f'<li><a href="artikel_{artikel_id}.html">{judul}</a></li>\n'
        
    index_html += """
        </ul>
    </div>
</body>
</html>
    """
    with open(os.path.join(OUTPUT_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(index_html)

    # 2. BUAT FILE HTML UNTUK MASING-MASING ARTIKEL
    for item in rows:
        artikel_id, judul, konten, gambar, tanggal, link = item
        
        img_tag = f'<img src="{esc(gambar)}" alt="{esc(judul)}" style="max-width:100%; height:auto; border-radius:8px;">' if gambar else ""
        
        html_content = f"""<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{esc(judul)}</title>
    <link rel="canonical" href="{BASE_URL}/artikel_{artikel_id}.html">
    <style>
        body {{ font-family: Arial, sans-serif; padding: 20px; background: #f4f4f9; line-height: 1.6; }}
        .container {{ max-width: 800px; margin: 0 auto; background: #fff; padding: 25px; border-radius: 10px; box-shadow: 0 4px 10px rgba(0,0,0,0.05); }}
        .btn-back {{ display: inline-block; margin-bottom: 20px; color: #0d6efd; text-decoration: none; }}
        .article-body img {{ max-width: 100%; height: auto; border-radius: 8px; margin-bottom: 15px; }}
    </style>
</head>
<body>
    <div class="container">
        <a href="index.html" class="btn-back">&larr; Kembali ke Daftar Berita</a>
        <h1>{esc(judul)}</h1>
        <p style="color: #888; font-size: 14px;">Tanggal: {tanggal[:10] if tanggal else '-'}</p>
        
        <div class="article-body">
            {img_tag}
            {konten}
            {ADSTERRA_CODE}
        </div>
    </div>
</body>
</html>
        """
        
        filename = f"artikel_{artikel_id}.html"
        with open(os.path.join(OUTPUT_DIR, filename), "w", encoding="utf-8") as f:
            f.write(html_content)

    # 3. BUAT SITEMAP & ROBOTS
    with open(os.path.join(OUTPUT_DIR, "sitemap.xml"), "w", encoding="utf-8") as f:
        f.write(f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n')
        f.write(f'<url><loc>{BASE_URL}/</loc></url>\n')
        for item in rows:
            f.write(f'<url><loc>{BASE_URL}/artikel_{item[0]}.html</loc></url>\n')
        f.write('</urlset>')
        
    with open(os.path.join(OUTPUT_DIR, "robots.txt"), "w", encoding="utf-8") as f:
        f.write(f"User-agent: *\nAllow: /\nSitemap: {BASE_URL}/sitemap.xml")

    print(f"[SELESAI EXPORT] Total {len(rows)} file HTML artikel berhasil dibuat di folder utama.")

# ==========================================================================
# JALANKAN SEMUANYA
# ==========================================================================
if __name__ == "__main__":
    print("="*50)
    print("PROSES GABUNGAN: IMPORT & EXPORT")
    print("="*50)
    
    # 1. Import
    jalankan_scraping()
    
    # 2. Export
    print("\n" + "="*50)
    print("MULAI MEMBUAT FILE HTML")
    print("="*50)
    jalankan_export_1_per_halaman()
    
    print("\n[PROSES SELESAI]")
