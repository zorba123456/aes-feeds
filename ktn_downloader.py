#!/usr/bin/env python3
# -*- coding: utf-8 - -*-
"""
=============================================================================
Project: lit_auto_pipeline (aes-intel platform)
File: aes-feeds/ktn_downloader.py
Version: V1.0.8 (方案 B 兼容 暨 标题乱码双重强力清洗版)
Description:
    Kill The Newsletter 邮件流数据提纯模块。
    已完美咬合极简锁架构，并针对特定 Unicode 乱码进行物理抹除。
=============================================================================
"""

import os
import sys
import xml.etree.ElementTree as ET
import requests
import feedparser
import json
import time
import hashlib
import re
import subprocess
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs
from bs4 import BeautifulSoup

# ==================== 配置区域 ====================
KTN_RSS_URL = "https://kill-the-newsletter.com/feeds/uwgwyb1cnivki39x.xml"
OUTPUT_XML_PATH = "aes-feeds/ktn_cleaned_articles.xml"  
LOG_FILE_PATH = "aes-feeds/ktn_dedup_log.json"           
DEDUP_EXPIRE_DAYS = 60                         

PROXIES = {
    "http": "http://127.0.0.1:29758",
    "https": "http://127.0.0.1:29758"
}

LOCAL_BACKUP_XML = "aes-feeds/uwgwyb1cnivki39x.xml"
# ==================================================

def load_dedup_log():
    if os.path.exists(LOG_FILE_PATH):
        try:
            with open(LOG_FILE_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_dedup_log(log_data):
    try:
        # 清理过期（超过 DEDUP_EXPIRE_DAYS）的去重缓存
        now = time.time()
        expire_sec = DEDUP_EXPIRE_DAYS * 86400
        clean_log = {k: v for k, v in log_data.items() if now - v.get('ts', 0) < expire_sec}
        
        with open(LOG_FILE_PATH, 'w', encoding='utf-8') as f:
            json.dump(clean_log, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"⚠️ 保存去重日志异常: {e}")

def parse_mail_content(html_body):
    """解析 KTN 原始邮件 HTML 提纯出真实的谷歌学术文献条目"""
    soup = BeautifulSoup(html_body, 'html.parser')
    articles = []
    
    # 锁定谷歌学术条目的标准锚点
    links = soup.find_all('a', href=True)
    
    for link in links:
        href = link['href']
        if "scholar.google.com/scholar_url" in href or "scholar.google.com/scholar?" in href:
            try:
                # --- 强力清洗 KTN 标题乱码段落开始 ---
                # 1. 过滤未识别的实体字节（如 \ufffd）
                raw_title = link.get_text().replace('\ufffd', '')
                raw_title = raw_title.replace('', '')
                
                # 2. 物理斩断连续出现的问号乱码（如 ???）
                raw_title = re.sub(r'\?{2,}', '', raw_title)
                
                # 3. 规范化空白字符
                title_text = re.sub(r'\s+', ' ', raw_title).strip()
                # --- 强力清洗 KTN 标题乱码段落结束 ---
                
                if not title_text or title_text.lower() in ["[pdf]", "[html]", "获取全文", "cites"]:
                    continue
                
                raw_url = href
                if "scholar_url?" in href:
                    parsed_url = urlparse(href)
                    qs = parse_qs(parsed_url.query)
                    if 'url' in qs:
                        raw_url = qs['url'][0]
                
                # 基于标题和链接计算唯一的排重指纹
                fp_str = f"{title_text}{raw_url}".replace(" ", "")
                fingerprint = hashlib.md5(fp_str.encode('utf-8')).hexdigest()
                
                articles.append({
                    "title": title_text,
                    "url": raw_url,
                    "fingerprint": fingerprint
                })
            except Exception:
                continue
    return articles

def generate_rss_xml(articles):
    """动态生成符合标准 RSS 2.0 规范的提纯文件"""
    pub_date_str = datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S GMT')
    
    rss_items = []
    for art in articles:
        item_xml = f"""        <item>
            <title><![CDATA[{art['title']}]]></title>
            <link>{art['url']}</link>
            <guid isPermaLink="false">{art['fingerprint']}</guid>
            <pubDate>{art.get('pubDate', pub_date_str)}</pubDate>
            <description><![CDATA[📡 AES-INTEL 谷歌学术邮件提纯流<br><br><b>文献标题:</b> {art['title']}<br><b>源链接:</b> <a href="{art['url']}">点击跳转物理原文</a>]]></description>
        </item>"""
        rss_items.append(item_xml)

    rss_xml = f"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
    <channel>
        <title>AES-INTEL KTN 谷歌学术增量提纯</title>
        <link>https://github.com/zorba123456/aes-feeds</link>
        <description>Google Scholar Alert 邮件流高精度去噪提纯 RSS</description>
        <lastBuildDate>{pub_date_str}</lastBuildDate>
        {"".join(rss_items)}
    </channel>
</rss>"""

    current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    output_path = os.path.join(current_dir, OUTPUT_XML_PATH)
    
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(rss_xml)
    print(f"✅ KTN 提纯数据成功存盘: {output_path}")

def git_push_feeds():
    print("\n📤 启动发布管线，正在自动推送到 GitHub...")
    current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        subprocess.run(["git", "add", OUTPUT_XML_PATH, LOG_FILE_PATH], cwd=current_dir, check=True)
        commit_msg = f"Auto-Update KTN Feeds (Clean Title): {time.strftime('%Y-%m-%d %H:%M:%S')}"
        subprocess.run(["git", "commit", "-m", commit_msg], cwd=current_dir, check=True)
        subprocess.run(["git", "push"], cwd=current_dir, check=True)
        print("🚀 GitHub 仓库发布成功！带有时间戳的 KTN XML 现已生效。")
    except subprocess.CalledProcessError:
        print("⏸️ 没有检测到新的变更，跳过 GitHub 推送。")

def main():
    print("=" * 55)
    print("🚀 开始解析并提纯 KTN 邮件流 (V1.0.8 乱码深锁双改版)...")
    print("=" * 55)

    dedup_log = load_dedup_log()
    feed_text = ""
    
    print("🌐 正在请求 KTN 服务器...")
    try:
        response = requests.get(KTN_RSS_URL, proxies=PROXIES, timeout=30)
        if response.status_code == 200:
            feed_text = response.text
            with open(LOCAL_BACKUP_XML, 'w', encoding='utf-8') as f:
                f.write(feed_text)
        else:
            print(f"⚠️ 线上请求返回非200状态码: {response.status_code}，尝试加载本地备份...")
    except Exception as e:
        print(f"⚠️ 线上请求失败（网络异常）: {e}")
    
    if not feed_text:
        if os.path.exists(LOCAL_BACKUP_XML):
            with open(LOCAL_BACKUP_XML, 'r', encoding='utf-8') as f:
                feed_text = f.read()
        else:
            print("❌ 物理异常：无线上数据且无本地备份，KTN 退出。")
            return

    feed = feedparser.parse(feed_text)
    all_valid_articles = []
    current_time_stamp = time.time()

    for entry in feed.entries:
        html_content = ""
        if 'content' in entry:
            html_content = entry.content[0].value
        elif 'summary' in entry:
            html_content = entry.summary
            
        if not html_content:
            continue
            
        mail_date = entry.get('published', entry.get('updated', datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S GMT')))

        extracted_articles = parse_mail_content(html_content)
        for art in extracted_articles:
            art['pubDate'] = mail_date 
            fp = art['fingerprint']
            if fp not in dedup_log and fp not in [a['fingerprint'] for a in all_valid_articles]:
                dedup_log[fp] = {
                    "title": art['title'],
                    "ts": current_time_stamp
                }
                all_valid_articles.append(art)

    print(f"✨ 提纯完成。本次发现全新增量文献数: {len(all_valid_articles)} 篇")

    if all_valid_articles:
        generate_rss_xml(all_valid_articles)
        save_dedup_log(dedup_log)
        git_push_feeds()
    else:
        print("⏸️ 没有任何新增文献，跳过更新。")

if __name__ == "__main__":
    main()