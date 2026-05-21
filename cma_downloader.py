#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import time
import subprocess
import re
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

__version__ = "6.2.2-去噪版"

def clean_text_noise(text):
    if not text:
        return ""
    cleaned = text.replace('\ufffd', '').replace('\u0000', '')
    cleaned = re.sub(r'\?{2,}', '', cleaned)
    return re.sub(r'\s+', ' ', cleaned).strip()

def push_to_github():
    print("\n📤 启动 GitHub 自动同步 (CMA Feeds)...")
    current_dir = os.path.dirname(os.path.abspath(__file__))
    try:
        subprocess.run(["git", "add", "cma_*.xml"], cwd=current_dir, check=True)
        commit_msg = f"Auto-update CMA feeds: {time.strftime('%Y-%m-%d %H:%M:%S')}"
        subprocess.run(["git", "commit", "-m", commit_msg], cwd=current_dir, check=True)
        subprocess.run(["git", "push"], cwd=current_dir, check=True)
        print("✅ 同步成功！")
    except subprocess.CalledProcessError:
        print("ℹ️ 未检测到新文献，跳过推送。")

def fetch_cma_journal(playwright_context, base_url, journal_name, output_filename):
    print(f"\n📡 正在抓取: {journal_name}")
    page = playwright_context.new_page()
    rss_items = []
    page_num = 1
    
    while True:
        sep = "&" if "?" in base_url else "?"
        url = f"{base_url}{sep}page={page_num}"
        print(f"  ├─ 探测第 {page_num} 页...")
        
        try:
            page.goto(url, timeout=45000)
            page.wait_for_selector(".journal-article-item", timeout=15000)
            content = page.content()
        except Exception:
            break
            
        soup = BeautifulSoup(content, 'html.parser')
        items = soup.find_all(class_="journal-article-item")
        
        if not items:
            break
            
        for item in items:
            try:
                title_el = item.find(class_="article-title")
                # 对抓取到的 HTML 文本进行深度乱码自清洗
                title = clean_text_noise(title_el.get_text()) if title_el else ""
                link = "https://www.yiigle.com" + title_el.find("a")["href"] if title_el and title_el.find("a") else ""
                
                author_el = item.find(class_="article-author")
                author = clean_text_noise(author_el.get_text()) if author_el else "未知作者"
                
                desc_el = item.find(class_="article-abstract")
                description = clean_text_noise(desc_el.get_text()) if desc_el else ""
                
                if not title or not link:
                    continue
                    
                pub_date_str = datetime.now(timezone(timedelta(hours=8))).strftime('%a, %d %b %Y %H:%M:%S +0800')
                
                item_xml = f"""        <item>
            <title><![CDATA[{title}]]></title>
            <link>{link}</link>
            <guid isPermaLink="true">{link}</guid>
            <pubDate>{pub_date_str}</pubDate>
            <description><![CDATA[<b>作者:</b> {author}<br><br><b>摘要:</b> {description}]]></description>
        </item>"""
                rss_items.append(item_xml)
            except Exception:
                continue
                
        page_num += 1
        if page_num > 1: # 默认抓取前1页即可满足高频增量提纯
            break
            
    page.close()
    
    if not rss_items:
        print(f"  └─ ❌ {journal_name} 本次未捕获到任何有效文献。")
        return False
        
    pub_date_str = datetime.now(timezone(timedelta(hours=8))).strftime('%a, %d %b %Y %H:%M:%S +0800')
    rss_xml = f"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
    <channel>
        <title>CMA - {journal_name}</title>
        <link>{base_url}</link>
        <description>{journal_name} - 自动高精度去噪聚合源</description>
        <lastBuildDate>{pub_date_str}</lastBuildDate>
        {"".join(rss_items)}
    </channel>
</rss>"""

    output_path = os.path.abspath(os.path.join(os.path.dirname(__file__), output_filename))
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(rss_xml)
    print(f"  └─ ✅ 成功存盘: {output_path}")
    return True

if __name__ == "__main__":
    print("=" * 55)
    print(f"🚀 启动 CMA 中华医学会抓取管线 [v{__version__}]")
    print("=" * 55)
    
    targets = [
        {"name": "中华整形外科杂志", "url": "https://www.yiigle.com/Journal/ZHZXWKZZ", "filename": "cma_plastics.xml"},
        {"name": "中华皮肤科杂志", "url": "https://www.yiigle.com/Journal/ZHPFKZZ", "filename": "cma_dermatology.xml"},
        {"name": "中华医学美学美容杂志", "url": "https://www.yiigle.com/Journal/ZHYXMXMRZZ", "filename": "cma_aesthetics.xml"}
    ]
    
    updated_any = False
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=False)
        context = browser.new_context()
        
        for t in targets:
            success = fetch_cma_journal(context, t["url"], t["name"], t["filename"])
            if success:
                updated_any = True
                
        context.close()
        browser.close()
        
    if updated_any:
        push_to_github()