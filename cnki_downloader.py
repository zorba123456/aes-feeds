#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=============================================================================
Project: lit_auto_pipeline (aes-intel platform)
File: aes-feeds/cnki_downloader.py
Version: V1.4.2 (全量路径像素对齐固化版)
Description:
    1. 100% 继承 V1.3.1 现行版的高精度清洗去噪与增量哈希去重逻辑。
    2. 彻底修正创世版（da0b950）残留的相对路径缺陷，全量校准为绝对物理路径，根除 Git Pathspec 子模块报错。
    3. 固化补齐 __version__ 全局变量，确保版本流水输出无损。
    4. 深度隔离：抓取阶段 100% 绕过代理国内直连中转，Git 推送阶段独立挂载 29758 代理上云。
=============================================================================
"""

import os
import xml.etree.ElementTree as ET
import requests
import feedparser
import json
import time
import hashlib
import re
import subprocess
from datetime import datetime, timezone

# 🟢 固化补齐全局版本定义，消除 NameError
__version__ = "1.4.2-全量路径像素对齐固化版"

# 🟢 严格使用物理绝对路径定位，确保在外层 Shell 跨目录调用时永不错位
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))  # /Users/.../lit_auto_pipeline/aes-feeds
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)               # /Users/.../lit_auto_pipeline

TARGETS_JSON_PATH = os.path.join(CURRENT_DIR, "cnki_targets.json")
LOG_FILE_PATH = os.path.join(CURRENT_DIR, "cnki_dedup_log.json")
DEDUP_EXPIRE_DAYS = 90

# 🟢 严格环境隔离变量
PROXIES = {"http": None, "https": None}
PROXY_SERVER = "http://127.0.0.1:29758"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/rss+xml, text/xml, */*"
}

def clean_text_noise(text):
    if not text:
        return ""
    cleaned = text.replace('\ufffd', '').replace('\u0000', '')
    cleaned = re.sub(r'\?{2,}', '', cleaned)
    return re.sub(r'\s+', ' ', cleaned).strip()

def load_targets():
    if os.path.exists(TARGETS_JSON_PATH):
        with open(TARGETS_JSON_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

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
        now = time.time()
        expire_sec = DEDUP_EXPIRE_DAYS * 86400
        clean_log = {k: v for k, v in log_data.items() if now - v.get('ts', 0) < expire_sec}
        with open(LOG_FILE_PATH, 'w', encoding='utf-8') as f:
            json.dump(clean_log, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"⚠️ 保存去重日志异常: {e}")

def generate_rss_xml(articles, j_code, j_name):
    pub_date_str = datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S GMT')
    rss_items = []
    
    for art in articles:
        item_xml = f"""        <item>
            <title><![CDATA[{art['title']}]]></title>
            <link>{art['url']}</link>
            <guid isPermaLink="false">{art['fingerprint']}</guid>
            <pubDate>{art['pubDate']}</pubDate>
            <description><![CDATA[<b>作者:</b> {art['author']}<br><br><b>分类信息:</b> {art['description']}]]></description>
        </item>"""
        rss_items.append(item_xml)

    rss_xml = f"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
    <channel>
        <title>CNKI - {j_name}</title>
        <link>https://navi.cnki.net/knavi/journals/{j_code}/detail</link>
        <description>{j_name} - 知网增量高精度去噪提纯 RSS</description>
        <lastBuildDate>{pub_date_str}</lastBuildDate>
        {"".join(rss_items)}
    </channel>
</rss>"""

    # 🟢 确保盘片精准落地在 aes-feeds 物理文件夹下
    filename = f"cnki_{j_code.lower()}.xml"
    output_path = os.path.join(CURRENT_DIR, filename)
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(rss_xml)
    return filename

def git_push_feeds(updated_files):
    print("\n📤 启动 GitHub 发布管线 (CNKI Feeds)...")
    
    # 🟢 环境深度隔离：仅在推送阶段挂载 29758 代理环境
    custom_env = os.environ.copy()
    custom_env["HTTP_PROXY"] = PROXY_SERVER
    custom_env["HTTPS_PROXY"] = PROXY_SERVER
    
    try:
        # 🟢 像素级对齐修复：回到项目根目录执行 Git 动作，使用相对根目录的精准路径，彻底破除 Pathspec 报错
        for f in updated_files:
            subprocess.run(["git", "add", f"aes-feeds/{f}"], cwd=PROJECT_ROOT, check=True)
        subprocess.run(["git", "add", "aes-feeds/cnki_dedup_log.json"], cwd=PROJECT_ROOT, check=True)
        
        commit_msg = f"Auto-Update CNKI Feeds: {time.strftime('%Y-%m-%d %H:%M:%S')}"
        subprocess.run(["git", "commit", "-m", commit_msg], cwd=PROJECT_ROOT, check=True)
        subprocess.run(["git", "push"], cwd=PROJECT_ROOT, env=custom_env, check=True)
        print("🚀 [SUCCESS] 知网中转提纯盘片已成功安全推送至 GitHub 远端仓库。")
    except subprocess.CalledProcessError as e:
        print(f"ℹ️ 发布管线返回: 无变更或推送被跳过 ({e})")

def main():
    print("=" * 55)
    print(f"🚀 开始全量执行知网心跳 ({__version__})...")
    print("=" * 55)

    targets = load_targets()
    dedup_log = load_dedup_log()
    current_time_stamp = time.time()
    total_new_count = 0
    new_articles_by_journal = {}

    for j_code, j_info in targets.items():
        j_name = j_info['name']
        rss_url = j_info['rss_url']
        print(f"🌐 正在请求: {j_name} ...")
        new_articles_by_journal[j_code] = []
        
        try:
            response = requests.get(rss_url, headers=HEADERS, proxies=PROXIES, timeout=30)
            if response.status_code != 200:
                print(f"[REPORT] CHANNEL=CNKI ITEM={j_name} COUNT=0 STATUS=FAIL")
                continue
                
            feed = feedparser.parse(response.text)
            journal_new_count = 0
            
            for entry in feed.entries:
                try:
                    title = clean_text_noise(entry.get('title', ''))
                    link = entry.get('link', '')
                    author = clean_text_noise(entry.get('author', '未知作者'))
                    description = clean_text_noise(entry.get('description', ''))
                    pub_date = entry.get('published', datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S GMT'))
                    
                    if not title or not link:
                        continue
                        
                    fp_str = f"{title}{link}".replace(" ", "")
                    fp = hashlib.md5(fp_str.encode('utf-8')).hexdigest()
                    
                    if fp not in dedup_log:
                        dedup_log[fp] = {"title": title, "ts": current_time_stamp}
                        new_articles_by_journal[j_code].append({
                            "fingerprint": fp, "title": title, "url": link,
                            "author": author, "description": description, "pubDate": pub_date
                        })
                        journal_new_count += 1
                        total_new_count += 1
                except Exception:
                    continue
            print(f"  ✅ {j_name} 解析完成，发现新增: {journal_new_count} 篇")
            print(f"[REPORT] CHANNEL=CNKI ITEM={j_name} COUNT={journal_new_count} STATUS=SUCCESS")
        except Exception as e:
            print(f"  ⚠️ {j_name} 请求异常: {e}")
            print(f"[REPORT] CHANNEL=CNKI ITEM={j_name} COUNT=0 STATUS=FAIL")

    print(f"\n✨ 全部遍历完成。共发现增量文献: {total_new_count} 篇")

    updated_files = []
    for j_code, articles in new_articles_by_journal.items():
        if articles:
            j_name = targets[j_code]['name']
            filename = generate_rss_xml(articles, j_code, j_name)
            updated_files.append(filename)

    if updated_files:
        save_dedup_log(dedup_log)
        git_push_feeds(updated_files)
    else:
        print("⏸️ 没有新的文献增量，跳过推送。")

if __name__ == "__main__":
    main()