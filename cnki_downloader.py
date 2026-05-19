#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=============================================================================
Project: lit_auto_pipeline (aes-intel platform)
File: aes-feeds/cnki_downloader.py
Version: V1.3.0
Last Updated: 2026-05-19
Author: zorba

Description:
    知网 (CNKI) 核心医学期刊直连抓取模块。
    
Features:
    1. 读取 cnki_targets.json，批量遍历。
    2. 网络物理隔离：强制脱离系统代理，使用国内 IP 直连。
    3. 独立分发：为有更新的每个期刊生成独立的 XML 订阅源。
    4. 增加 pubDate 时间戳支持，便于 RSS 阅读器时间线排序。
=============================================================================
"""

import os
import xml.etree.ElementTree as ET
import requests
import feedparser
import json
import time
import hashlib
import subprocess
from datetime import datetime, timezone

TARGETS_JSON_PATH = "aes-feeds/cnki_targets.json"
LOG_FILE_PATH = "aes-feeds/cnki_dedup_log.json"
DEDUP_EXPIRE_DAYS = 90

PROXIES = {
    "http": None,
    "https": None
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"
}

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
    now = time.time()
    expire_sec = DEDUP_EXPIRE_DAYS * 24 * 3600
    cleaned_log = {k: v for k, v in log_data.items() if (now - v.get('ts', 0)) < expire_sec}
    with open(LOG_FILE_PATH, 'w', encoding='utf-8') as f:
        json.dump(cleaned_log, f, ensure_ascii=False, indent=2)

def generate_rss_xml(articles_list, j_code, j_name):
    output_filename = f"cnki_{j_code}_cleaned.xml"
    output_path = os.path.join("aes-feeds", output_filename)
    
    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")
    
    ET.SubElement(channel, "title").text = f"{j_name} - CNKI Feeds"
    ET.SubElement(channel, "link").text = "https://github.com/zorba123456/aes-feeds"
    ET.SubElement(channel, "description").text = f"知网结构化订阅源 - {j_name}"
    
    current_utc_time = datetime.now(timezone.utc)
    ET.SubElement(channel, "lastBuildDate").text = current_utc_time.strftime('%a, %d %b %Y %H:%M:%S GMT')

    for art in articles_list:
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = art['title']
        ET.SubElement(item, "link").text = art['url']
        ET.SubElement(item, "guid", isPermaLink="false").text = art['fingerprint']
        
        # 注入标准的时间标签
        pub_date = art.get('pubDate', '')
        if pub_date:
            ET.SubElement(item, "pubDate").text = pub_date
        
        # 同时将时间写入正文区域作为双保险
        desc_content = f"<b>时间:</b> {pub_date}<br/><b>作者:</b> {art['author']}<br/><br/>{art['description']}"
        ET.SubElement(item, "description").text = desc_content
        
    tree = ET.ElementTree(rss)
    ET.indent(tree, space="  ", level=0)
    tree.write(output_path, encoding="utf-8", xml_declaration=True)
    return output_filename

def git_push_feeds(updated_files):
    print("📤 启动发布管线，正在自动推送到 GitHub...")
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        files_to_add = ["cnki_dedup_log.json"] + updated_files
        subprocess.run(["git", "add"] + files_to_add, cwd=current_dir, check=True)
        commit_msg = f"Auto-Update CNKI Feeds (Add pubDate): {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        subprocess.run(["git", "commit", "-m", commit_msg], cwd=current_dir, check=True)
        subprocess.run(["git", "push"], cwd=current_dir, check=True)
        print("🚀 GitHub 仓库发布成功！带有时间戳的 XML 现已生效。")
    except subprocess.CalledProcessError as e:
        print(f"⚠️ Git 推送提示（无文件变动）: {e}")
    except Exception as e:
        print(f"❌ 自动化发布失败: {e}")

def main():
    print("🚀 开始直连拉取知网 (CNKI) 数据 (V1.3.0 时间戳版)...")
    targets = load_targets()
    if not targets:
        print(f"❌ 未找到配置文件 {TARGETS_JSON_PATH}，流程终止。")
        return
        
    dedup_log = load_dedup_log()
    current_time_stamp = time.time()
    
    new_articles_by_journal = {j_code: [] for j_code in targets.keys()}
    total_new_count = 0

    for j_code, j_info in targets.items():
        j_name = j_info.get('name', j_code)
        rss_url = j_info.get('rss_url')
        if not rss_url:
            continue
            
        print(f"🌐 正在请求: {j_name} ...")
        try:
            response = requests.get(rss_url, headers=HEADERS, proxies=PROXIES, timeout=15)
            if response.status_code != 200:
                print(f"  ⚠️ {j_name} 请求失败，状态码: {response.status_code}")
                continue
            
            feed = feedparser.parse(response.text)
            journal_new_count = 0
            for entry in feed.entries:
                try:
                    title = entry.title.strip()
                    link = entry.link.strip()
                    author = entry.author.strip() if 'author' in entry else "Unknown"
                    description = entry.description.strip() if 'description' in entry else ""
                    
                    # 尝试获取 feed 中的官方发表时间，若无则使用当前时间兜底
                    pub_date = entry.get('published', entry.get('updated', datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S GMT')))
                    
                    fp_str = f"{title}_{author}".replace(" ", "")
                    fp = hashlib.md5(fp_str.encode('utf-8')).hexdigest()
                    
                    if fp not in dedup_log:
                        dedup_log[fp] = {
                            "title": title,
                            "ts": current_time_stamp
                        }
                        new_articles_by_journal[j_code].append({
                            "fingerprint": fp,
                            "title": title,
                            "url": link,
                            "author": author,
                            "description": description,
                            "pubDate": pub_date
                        })
                        journal_new_count += 1
                        total_new_count += 1
                except Exception:
                    continue
            print(f"  ✅ {j_name} 解析完成，发现新增: {journal_new_count} 篇")
            
        except Exception as e:
            print(f"  ⚠️ {j_name} 请求异常: {e}")

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