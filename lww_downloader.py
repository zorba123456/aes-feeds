#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=============================================================================
Project: lit_auto_pipeline (aes-intel platform)
File: aes-feeds/lww_downloader.py
Version: V3.4.2 (语法修复版)
Description:
    100% 保持现状抓取逻辑与 Edge 图形弹窗行为。
    修复了写盘时由于单引号未闭合导致的 SyntaxError 阻断性 Bug。
    强攻海外权威期刊（如 PRS 杂志），通过本地代理（29758）并自动同步至 GitHub。
=============================================================================
"""

import os
import sys
import requests
import feedparser
import time
import re
import subprocess
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

# ==================== 物理配置区域 ====================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 强攻海外 LWW 期刊的原始 RSS 源名册
LWW_FEEDS = {
    "aswc_current_issue": "https://journals.lww.com/aswcjournal/_layouts/15/oai/feed.aspx?feed=currentissue",
    "aswc_latest_articles": "https://journals.lww.com/aswcjournal/_layouts/15/oai/feed.aspx?feed=latestarticles",
    "annals_plast_surg_current": "https://journals.lww.com/annalsplasticsurgery/_layouts/15/oai/feed.aspx?feed=currentissue",
    "annals_plast_surg_latest": "https://journals.lww.com/annalsplasticsurgery/_layouts/15/oai/feed.aspx?feed=latestarticles"
}

PROXY_SERVER = "http://127.0.0.1:29758"
PROXIES = {
    "http": PROXY_SERVER,
    "https": PROXY_SERVER
}
# ======================================================

def clean_environment():
    """环境大扫除：强杀可能残留的 Edge 及 WebDriver 进程，防止死锁"""
    print("🧹 正在执行环境大扫除 (强杀 Edge 残留进程)...")
    try:
        subprocess.run(["pkill", "-f", "Edge"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["pkill", "-f", "msedgedriver"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

def clean_text_noise(text):
    if not text: return ""
    cleaned = text.replace('\ufffd', '').replace('\u0000', '')
    cleaned = re.sub(r'\?{2,}', '', cleaned)
    return re.sub(r'\s+', ' ', cleaned).strip()

def fetch_lww_feed_via_playwright(playwright_context, url):
    page = playwright_context.new_page()
    xml_text = None
    try:
        response = page.goto(url, timeout=45000)
        page.wait_for_load_state("domcontentloaded", timeout=15000)
        time.sleep(3) # 给页面一定的响应时间
        
        # 方式 1：从 Playwright 的响应中直接读取原始文本
        if response:
            try:
                xml_text = response.text()
            except Exception:
                pass
                
        # 方式 2：使用 DOM 序列化
        if not xml_text or not xml_text.strip().startswith("<?xml"):
            try:
                xml_text = page.evaluate("() => new XMLSerializer().serializeToString(document)")
            except Exception as e:
                print(f"      ├─ ⚠️ DOM 序列化失败: {e}")
                
        # 方式 3：读取 body 的 innerText 兜底
        if not xml_text or not xml_text.strip().startswith("<?xml"):
            try:
                body_text = page.evaluate("() => document.body.innerText")
                if body_text and body_text.strip().startswith("<?xml"):
                    xml_text = body_text
            except Exception:
                pass
                
    except Exception as e:
        print(f"  ├─ ⚠️ Playwright 请求异常: {e}")
    finally:
        page.close()
    return xml_text

def process_lww_feed(playwright_context, feed_key, url):
    """请求并提纯单个 LWW RSS 馈送通道"""
    print(f"\n📡 正在抓取: {feed_key}")
    try:
        xml_text = fetch_lww_feed_via_playwright(playwright_context, url)
        if not xml_text or not xml_text.strip().startswith("<?xml"):
            print(f"❌ 无法请求 {feed_key} 或内容非标准 XML")
            print(f"[REPORT] CHANNEL=LWW ITEM={feed_key} COUNT=0 STATUS=FAIL")
            return None
        
        feed = feedparser.parse(xml_text)
        pub_date_str = datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S GMT')
        rss_items = []
        
        for entry in feed.entries:
            title = clean_text_noise(entry.get('title', 'Untitled'))
            link = entry.get('link', '')
            desc = clean_text_noise(entry.get('summary', entry.get('description', 'No description available.')))
            date = entry.get('published', pub_date_str)
            
            item_xml = f"""        <item>
            <title><![CDATA[{title}]]></title>
            <link>{link}</link>
            <guid isPermaLink="true">{link}</guid>
            <pubDate>{date}</pubDate>
            <description><![CDATA[📡 AES-INTEL 海外权威监测 [源: {feed_key}]<br><br>{desc}]]></description>
        </item>"""
            rss_items.append(item_xml)
            
        filename = f"{feed_key}.xml"
        out_dir = os.environ.get("AES_OUT_DIR", BASE_DIR)
        output_path = os.path.join(out_dir, filename)
        
        rss_xml = f"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
    <channel>
        <title>LWW_{feed_key}</title>
        <link>https://github.com/zorba123456/aes-feeds</link>
        <description>海外强攻提纯通道: {feed_key}</description>
        <lastBuildDate>{pub_date_str}</lastBuildDate>
        {"".join(rss_items)}
    </channel>
</rss>"""

        # 🟢 核心修复：单引号与括号完全闭合，彻底解决 SyntaxError
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(rss_xml)
        print(f"✅ 成功完美提纯存盘: {output_path}")
        print(f"[REPORT] CHANNEL=LWW ITEM={feed_key} COUNT={len(rss_items)} STATUS=SUCCESS")
        return filename

    except Exception as e:
        print(f"❌ 强攻 {feed_key} 发生物理异常: {e}")
        print(f"[REPORT] CHANNEL=LWW ITEM={feed_key} COUNT=0 STATUS=FAIL")
        return None

def main():
    print("=============================================")
    print("🚀 启动 LWW 强攻与提纯管线 [v4.0.0-Playwright 强攻版]")
    print(f"📂 工作目录: {BASE_DIR}")
    print("=============================================")
    
    # 保持原有的 Edge 环境清理行为
    clean_environment()
    
    updated_files = []
    with sync_playwright() as p:
        # 使用 Edge 图形化代理通道启动
        browser = p.chromium.launch(
            channel="msedge",
            headless=False,
            proxy={"server": PROXY_SERVER}
        )
        context = browser.new_context()
        
        for feed_key, url in LWW_FEEDS.items():
            res = process_lww_feed(context, feed_key, url)
            if res:
                updated_files.append(res)
                
        context.close()
        browser.close()
            
    if updated_files:
        print("\n📤 正在自动推送 LWW 提纯流到 GitHub...")
        custom_env = os.environ.copy()
        custom_env["HTTP_PROXY"] = PROXY_SERVER
        custom_env["HTTPS_PROXY"] = PROXY_SERVER
        
        try:
            subprocess.run(["git", "add", "-A"], cwd=BASE_DIR, check=True)
            commit_msg = f"Auto-update LWW feeds: {time.strftime('%Y-%m-%d %H:%M:%S')}"
            subprocess.run(["git", "commit", "-m", commit_msg], cwd=BASE_DIR, check=True)
            subprocess.run(["git", "push"], cwd=BASE_DIR, env=custom_env, check=True)
            print("🚀 GitHub 海外数据同步成功！")
        except subprocess.CalledProcessError:
            print("ℹ️ 发布管线返回: 无变更或推送被跳过。")

if __name__ == "__main__":
    main()