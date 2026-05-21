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

def process_lww_feed(feed_key, url):
    """请求并提纯单个 LWW RSS 馈送通道"""
    print(f"\n📡 正在抓取: {feed_key}")
    try:
        response = requests.get(url, proxies=PROXIES, timeout=30)
        if response.status_code != 200:
            print(f"❌ 无法请求 {feed_key}, HTTP 状态码: {response.status_code}")
            return None
        
        feed = feedparser.parse(response.text)
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
        output_path = os.path.join(BASE_DIR, filename)
        
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
        return filename

    except Exception as e:
        print(f"❌ 强攻 {feed_key} 发生物理异常: {e}")
        return None

def main():
    print("=============================================")
    print("🚀 启动 LWW 强攻与提纯管线 [v3.4.2-语法修复版]")
    print(f"📂 工作目录: {BASE_DIR}")
    print("=============================================")
    
    # 保持原有的 Edge 环境清理行为
    clean_environment()
    
    updated_files = []
    for feed_key, url in LWW_FEEDS.items():
        res = process_lww_feed(feed_key, url)
        if res:
            updated_files.append(res)
            
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