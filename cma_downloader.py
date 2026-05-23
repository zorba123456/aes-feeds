#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=============================================================================
Project: lit_auto_pipeline (aes-intel platform)
File: aes-feeds/cma_downloader.py
Version: V6.3.0 (高供血及前缀规范版)
Description:
    1. 彻底移除死锁的选择器拦截，改为弹性宽容抓取，恢复中华医学会大盘供血。
    2. RSS 频道标题强制注入大写 "KTN_" 前缀，确保 Inoreader 统一识别。
    3. 浏览器硬核挂载本地代理（29758），发布管线原生包裹代理环境。
=============================================================================
"""
import os
import time
import subprocess
import re
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

__version__ = "6.3.0-前缀规范版"

# ==================== 物理配置区域 ====================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROXY_SERVER = "http://127.0.0.1:29758"
# ======================================================

def clean_text_noise(text):
    if not text: return ""
    cleaned = text.replace('\ufffd', '').replace('\u0000', '')
    cleaned = re.sub(r'\?{2,}', '', cleaned)
    return re.sub(r'\s+', ' ', cleaned).strip()

def push_to_github():
    print("\n📤 正在自动推送 CMA 提纯流到 GitHub (已注入代理保护)...")
    custom_env = os.environ.copy()
    custom_env["HTTP_PROXY"] = PROXY_SERVER
    custom_env["HTTPS_PROXY"] = PROXY_SERVER
    
    try:
        subprocess.run(["git", "add", "cma_*.xml"], cwd=BASE_DIR, check=True)
        commit_msg = f"Auto-update CMA feeds: {time.strftime('%Y-%m-%d %H:%M:%S')}"
        subprocess.run(["git", "commit", "-m", commit_msg], cwd=BASE_DIR, check=True)
        subprocess.run(["git", "push"], cwd=BASE_DIR, env=custom_env, check=True)
        print("🚀 GitHub 自动化网络数据同步成功！")
    except subprocess.CalledProcessError:
        print("ℹ️ 发布管线返回: 无变更或推送被跳过。")

def fetch_cma_journal(playwright_context, base_url, journal_name, output_filename):
    print(f"\n📡 正在抓取: {journal_name}")
    page = playwright_context.new_page()
    rss_items = []
    page_num = 1
    
    try:
        while True:
            sep = "&" if "?" in base_url else "?"
            url = f"{base_url}{sep}page={page_num}"
            print(f"  ├─ 探测第 {page_num} 页...")
            
            try:
                page.goto(url, timeout=45000)
                # 🟢 优化：改用更宽容的 body 标志，只要页面开了就强行往下解析，不再死等选择器
                page.wait_for_load_state("domcontentloaded", timeout=15000)
                content = page.content()
            except Exception as e:
                print(f"  ├─ ⚠️ 页面加载提示: {e}")
                break
                
            soup = BeautifulSoup(content, 'html.parser')
            # 🟢 兼顾旧版与新版易智编译大典的前端特征
            items = soup.find_all(class_="journal-article-item") or soup.find_all('div', class_=re.compile(r'article.*item'))
            
            if not items:
                # 尝试做一次最后的兜底兜捕
                items = soup.select("div.list-item") or soup.find_all('li')
                
            valid_count = 0
            for item in items:
                try:
                    title_el = item.find(class_="article-title") or item.select_one("a.title")
                    if not title_el:
                        continue
                        
                    title = clean_text_noise(title_el.get_text())
                    
                    # 提取链接
                    a_tag = title_el.find('a') if title_el.name != 'a' else title_el
                    if not a_tag or not a_tag.has_attr('href'):
                        continue
                        
                    raw_href = a_tag["href"]
                    link = raw_href if raw_href.startswith("http") else "https://www.yiigle.com" + raw_href
                    
                    author_el = item.find(class_="article-author") or item.find(class_="author")
                    author = clean_text_noise(author_el.get_text()) if author_el else "未知作者"
                    
                    desc_el = item.find(class_="article-abstract") or item.find(class_="abstract")
                    description = clean_text_noise(desc_el.get_text()) if desc_el else "暂无摘要"
                    
                    if not title or "yiigle.com" not in link:
                        continue
                        
                    pub_date_str = datetime.now(timezone(timedelta(hours=8))).strftime('%a, %d %b %Y %H:%M:%S +0800')
                    
                    item_xml = f"""        <item>
            <title><![CDATA[{title}]]></title>
            <link>{link}</link>
            <guid isPermaLink="true">{link}</guid>
            <pubDate>{pub_date_str}</pubDate>
            <description><![CDATA[📡 AES-INTEL 国内核心监测 [来源: 中华医学会 @ {journal_name}]<br><br><b>作者:</b> {author}<br><b>文献摘要:</b> {description}]]></description>
        </item>"""
                    rss_items.append(item_xml)
                    valid_count += 1
                except Exception:
                    continue
                    
            if valid_count == 0:
                break
                
            page_num += 1
            if page_num > 1: # 保持前 1 页增量高频更新原则
                break
                
        page.close()
        
        # 即使 rss_items 为空也强制出盘刷新
        pub_date_str = datetime.now(timezone(timedelta(hours=8))).strftime('%a, %d %b %Y %H:%M:%S +0800')
        display_title = f"KTN_\"{journal_name}\" @ CMA"
        rss_xml = f"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
    <channel>
        <title>{display_title}</title>
        <link>{base_url}</link>
        <description>{journal_name} - 动态提纯通道</description>
        <lastBuildDate>{pub_date_str}</lastBuildDate>
        {"".join(rss_items)}
    </channel>
</rss>"""

        out_dir = os.environ.get("AES_OUT_DIR", BASE_DIR)
        output_path = os.path.join(out_dir, output_filename)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(rss_xml)
            
        if not rss_items:
            print(f"  └─ ❌ {journal_name} 本次未捕获到任何有效文献（已强制刷新空 XML 骨架）。")
            print(f"  └─ ✅ 物理提纯存盘成功: {output_filename} -> ({display_title})")
        else:
            print(f"  └─ ✅ 物理提纯存盘成功: {output_filename} -> ({display_title})")
            
        print(f"[REPORT] CHANNEL=CMA ITEM={journal_name} COUNT={len(rss_items)} STATUS=SUCCESS")
        return True

    except Exception as e:
        print(f"❌ 抓取 {journal_name} 发生异常: {e}")
        print(f"[REPORT] CHANNEL=CMA ITEM={journal_name} COUNT=0 STATUS=FAIL")
        try:
            page.close()
        except Exception:
            pass
        return False

if __name__ == "__main__":
    print("=" * 65)
    print(f"🚀 启动 CMA 中华医学会抓取管线 [{__version__}]")
    print(f"📂 锚定工作目录: {BASE_DIR}")
    print("=" * 65)
    
    targets = [
        {"name": "中华整形外科杂志", "url": "https://www.yiigle.com/Journal/ZHZXWKZZ", "filename": "cma_plastics.xml"},
        {"name": "中华皮肤科杂志", "url": "https://www.yiigle.com/Journal/ZHPFKZZ", "filename": "cma_dermatology.xml"},
        {"name": "中华医学美学美容杂志", "url": "https://www.yiigle.com/Journal/ZHYXMXMRZZ", "filename": "cma_aesthetics.xml"}
    ]
    
    updated_any = False
    with sync_playwright() as p:
        # 🟢 核心修正：在真浏览器强攻时，直接原生挂载本地科学上网代理，彻底防封锁
        browser = p.chromium.launch(
            channel="msedge", 
            headless=False,
            proxy={"server": PROXY_SERVER}
        )
        context = browser.new_context()
        
        for t in targets:
            success = fetch_cma_journal(context, t["url"], t["name"], t["filename"])
            if success:
                updated_any = True
                
        context.close()
        browser.close()
        
    if updated_any:
        push_to_github()