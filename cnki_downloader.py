#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=============================================================================
Project: lit_auto_pipeline (aes-intel platform)
File: aes-feeds/cnki_downloader.py
Version: V2.0.0 (DUAL-TRACK HYBRID SYSTEM)
Description:
    1. --mode rss: 快速静默的 RSS 提取逻辑。
    2. --mode web: 使用 Playwright 有头模式提取“当期目录”与“网络首发”。
       遇到滑块验证码时，发出提示音并给予长达 10 分钟的人工滑动容错时间。
    3. 支持全局基于 Hash 的去重机制。
=============================================================================
"""

import os
import xml.etree.ElementTree as ET
import json
import time
import hashlib
import re
import argparse
import subprocess
from datetime import datetime, timezone

from bs4 import BeautifulSoup
import requests

__version__ = "V2.0.0"

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)

TARGETS_JSON_PATH = os.path.join(CURRENT_DIR, "cnki_targets.json")
LOG_FILE_PATH = os.path.join(CURRENT_DIR, "cnki_dedup_log.json")
DEDUP_EXPIRE_DAYS = 90
USER_DATA_DIR = os.path.join(PROJECT_ROOT, "cnki_playwright_profile")

def clean_text_noise(text):
    if not text:
        return ""
    text = text.strip()
    text = re.sub(r'[\r\n\t]+', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text

def generate_hash(title, url):
    """基于标题和URL生成唯一哈希"""
    raw = f"{title}_{url}".encode('utf-8')
    return hashlib.md5(raw).hexdigest()

def load_dedup_log():
    """加载去重记录"""
    if os.path.exists(LOG_FILE_PATH):
        try:
            with open(LOG_FILE_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data
        except Exception:
            return {}
    return {}

def save_dedup_log(log_data):
    """保存去重记录并清理过期的Hash"""
    now = time.time()
    expire_secs = DEDUP_EXPIRE_DAYS * 24 * 3600
    cleaned_data = {
        k: v for k, v in log_data.items()
        if (now - v.get("timestamp", 0)) < expire_secs
    }
    with open(LOG_FILE_PATH, 'w', encoding='utf-8') as f:
        json.dump(cleaned_data, f, ensure_ascii=False, indent=2)

def generate_rss_xml(items, journal_code, journal_name):
    """生成标准 RSS 2.0 XML 并写入文件"""
    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")
    
    ET.SubElement(channel, "title").text = f"CNKI - {journal_name}"
    ET.SubElement(channel, "link").text = f"https://navi.cnki.net/knavi/journals/{journal_code}/detail"
    ET.SubElement(channel, "description").text = f"知网文献推送: {journal_name}"
    ET.SubElement(channel, "pubDate").text = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
    ET.SubElement(channel, "generator").text = f"Lit Auto Pipeline {__version__}"

    for item in items:
        item_el = ET.SubElement(channel, "item")
        ET.SubElement(item_el, "title").text = item.get("title", "")
        ET.SubElement(item_el, "link").text = item.get("link", "")
        ET.SubElement(item_el, "description").text = item.get("description", "")
        ET.SubElement(item_el, "guid").text = item.get("link", "")
        
        pub_date = item.get("pubDate")
        if pub_date:
            ET.SubElement(item_el, "pubDate").text = pub_date

    tree = ET.ElementTree(rss)
    ET.indent(tree, space="  ", level=0)
    
    out_file = os.path.join(PROJECT_ROOT, f"cnki_{journal_code.lower()}.xml")
    tree.write(out_file, encoding="utf-8", xml_declaration=True)
    return out_file

def run_rss_mode(targets):
    """静默抓取 RSS 模式"""
    print("[RSS Mode] 开始执行静默 RSS 抓取...")
    dedup_log = load_dedup_log()
    headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}

    for code, info in targets.items():
        name = info.get("name", code)
        rss_url = info.get("rss_url")
        if not rss_url:
            continue
            
        print(f"正在抓取 {name} ({code})...")
        try:
            r = requests.get(rss_url, headers=headers, timeout=15)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, 'xml')
            
            new_items = []
            for item in soup.find_all('item'):
                title = clean_text_noise(item.find('title').get_text(strip=True)) if item.find('title') else ''
                link = clean_text_noise(item.find('link').get_text(strip=True)) if item.find('link') else ''
                desc = item.find('description').get_text(strip=True) if item.find('description') else ''
                pubdate = item.find('pubDate').get_text(strip=True) if item.find('pubDate') else ''
                
                h = generate_hash(title, link)
                if h in dedup_log:
                    continue
                
                new_items.append({
                    "title": title,
                    "link": link,
                    "description": desc,
                    "pubDate": pubdate,
                    "hash": h
                })
            
            if new_items:
                print(f"  -> 发现 {len(new_items)} 篇新文献")
                # 写入本地去重日志
                for item in new_items:
                    dedup_log[item['hash']] = {"title": item['title'], "timestamp": time.time()}
                # 生成 XML
                generate_rss_xml(new_items, code, name)
            else:
                print("  -> 无新文献")
                
        except Exception as e:
            print(f"  ❌ 抓取失败: {e}")

    save_dedup_log(dedup_log)
    print("[RSS Mode] 执行完成！")

def wait_for_captcha(page, code, name):
    """当出现验证码时，发出提示音，并陷入长达10分钟的等待循环，等待人工滑动"""
    print(f"⚠️ 触发安全验证: {name} ({code})")
    
    # 播放 macOS 提示音 3 次
    try:
        subprocess.run(["osascript", "-e", 'beep 3'], check=False)
    except Exception:
        pass
        
    print("⏳ 等待人工滑过验证码 (最长等待 10 分钟)...")
    wait_start = time.time()
    
    while time.time() - wait_start < 600:  # 10分钟
        try:
            # 检查页面是否仍然包含"安全验证"
            if "安全验证" not in page.content():
                print("✅ 验证码已通过！继续执行...")
                time.sleep(2)  # 等待重定向完成
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=15000)
                    page.wait_for_selector(".yearissue-m", timeout=15000)
                except Exception:
                    pass
                time.sleep(3)
                return True
        except Exception:
            pass
        
        time.sleep(2)
        
    print("❌ 超时！10 分钟内未完成人工验证，跳过该期刊。")
    return False

def run_web_mode(targets):
    """深度网页抓取模式 (Playwright)"""
    print("[Web Mode] 开始执行深度网页抓取...")
    from playwright.sync_api import sync_playwright
    
    dedup_log = load_dedup_log()
    
    with sync_playwright() as p:
        # 必须是有头模式 headless=False，以便人工介入
        ctx = p.chromium.launch_persistent_context(
            USER_DATA_DIR, headless=False,
            args=['--disable-blink-features=AutomationControlled']
        )
        page = ctx.new_page()
        page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        for code, info in targets.items():
            name = info.get("name", code)
            
            # 只有设置了 web_scrape 为 True 的才进行网页抓取
            if not info.get("web_scrape", False):
                print(f"跳过 {name} (未开启 web_scrape)")
                continue
                
            url = f'https://navi.cnki.net/knavi/journals/{code}/detail?uniplatform=NZKPT'
            print(f"\n正在深度抓取 {name} ({code})...")
            
            try:
                page.goto(url, wait_until='domcontentloaded', timeout=30000)
                
                # 检查是否遇到验证码
                if "安全验证" in page.content():
                    success = wait_for_captcha(page, code, name)
                    if not success:
                        continue
                
                # 等待 AJAX 渲染期刊期数和目录
                try:
                    page.wait_for_selector('.yearissue-m', timeout=15000)
                except Exception:
                    print("  ⚠️ 等待 .yearissue-m 超时，可能暂无当期数据")
                time.sleep(3) # 额外等待渲染完成
                
                html = page.content()
                soup = BeautifulSoup(html, 'html.parser')
                
                # 提取期数
                issue = soup.select_one('.yearissue-m .yearissue')
                issue_txt = issue.get_text(strip=True) if issue else '未知期数'
                pub_date = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
                
                # 提取当期目录与网络首发
                new_items = []
                sections = {
                    "当期目录": '#CataLogContent dd',
                    "网络首发": '#NetFirst dd'
                }
                
                for section_name, selector in sections.items():
                    elements = soup.select(selector)
                    print(f"  -> {section_name}: 发现 {len(elements)} 篇")
                    for el in elements:
                        a_tag = el.select_one('span.name a')
                        if not a_tag: continue
                        
                        raw_title = clean_text_noise(a_tag.get_text(strip=True))
                        # 组装加强版 Title
                        enhanced_title = f"[{section_name}] [{issue_txt}] {raw_title}"
                        link_href = a_tag.get('href', '')
                        if link_href.startswith('/'):
                            link = f"https://navi.cnki.net{link_href}"
                        else:
                            link = link_href
                            
                        # 摘要等信息
                        author_tag = el.select_one('.author')
                        author = clean_text_noise(author_tag.get_text(strip=True)) if author_tag else ''
                        desc = f"作者: {author}" if author else ""
                        
                        h = generate_hash(raw_title, link)
                        if h in dedup_log:
                            continue
                            
                        new_items.append({
                            "title": enhanced_title,
                            "link": link,
                            "description": desc,
                            "pubDate": pub_date,
                            "hash": h
                        })
                
                if new_items:
                    print(f"  => 汇总提取到 {len(new_items)} 篇新文献")
                    # 写入去重日志
                    for item in new_items:
                        dedup_log[item['hash']] = {"title": item['title'], "timestamp": time.time()}
                    # 写入 XML，如果 RSS 模式跑过，这里会覆盖 XML，因为 Web 抓取的信息更全
                    generate_rss_xml(new_items, code, name)
                else:
                    print("  => 网页上无新文献")
                    
            except Exception as e:
                print(f"  ❌ 网页抓取异常: {e}")
                
        ctx.close()
        
    save_dedup_log(dedup_log)
    print("\n[Web Mode] 深度抓取完成！")

def main():
    parser = argparse.ArgumentParser(description="CNKI Downloader (Dual-Track)")
    parser.add_argument("--mode", choices=["rss", "web"], required=True, help="运行模式: rss (静默) 或 web (带弹窗)")
    args = parser.parse_args()
    
    if not os.path.exists(TARGETS_JSON_PATH):
        print(f"配置文件缺失: {TARGETS_JSON_PATH}")
        return
        
    with open(TARGETS_JSON_PATH, 'r', encoding='utf-8') as f:
        targets = json.load(f)
        
    if args.mode == 'rss':
        run_rss_mode(targets)
    elif args.mode == 'web':
        run_web_mode(targets)

if __name__ == "__main__":
    main()
