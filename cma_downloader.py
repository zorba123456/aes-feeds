#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import time
import subprocess
import re
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

__version__ = "6.2.1-精准时间戳修复版"

def push_to_github():
    print("\n📤 启动 GitHub 自动同步 (CMA Feeds)...")
    current_dir = os.path.dirname(os.path.abspath(__file__))
    try:
        subprocess.run(["git", "add", "cma_*.xml"], cwd=current_dir, check=True)
        commit_msg = f"Auto-update CMA feeds: {time.strftime('%Y-%m-%d %H:%M:%S')}"
        subprocess.run(["git", "commit", "-m", commit_msg], cwd=current_dir, check=True)
        subprocess.run(["git", "push"], cwd=current_dir, check=True)
        print("✅ 同步成功！CMA 数据已成功推送至 aes-feeds 独立仓库。")
    except subprocess.CalledProcessError:
        print("ℹ️ 未检测到新文献或同步无变动，跳过推送。")

def fetch_cma_journal(playwright_context, base_url, journal_name, output_filename):
    print(f"\n📡 正在抓取: {journal_name}")
    
    page = playwright_context.new_page()
    rss_items = []
    page_num = 1
    
    while True:
        sep = "&" if "?" in base_url else "?"
        page_url = f"{base_url}{sep}pageNo={page_num}"
        print(f"  ├─ 探测第 {page_num} 页... ", end="")
        
        try:
            page.goto(page_url, wait_until="networkidle", timeout=30000)
            time.sleep(3.0) 
            html_content = page.content()
            soup = BeautifulSoup(html_content, 'html.parser')
        except Exception as e:
            print(f"❌ 页面加载或网络渲染超时: {e}")
            break

        blocks = soup.select('div.s_searchResult_li, li.s_searchResult_li')
        valid_count = 0
        seen_links = set()

        if blocks:
            for node in blocks:
                title = ""
                link = ""
                
                # 遍历块内所有链接，跳过图标/空标签，锁定标题
                a_tags = node.select('a[href*="/cmaid/"], a[href*="/article/"]')
                for a in a_tags:
                    t = a.get('title') or a.get_text(" ", strip=True)
                    if t and len(t) > 2 and not any(kw in t for kw in ["下载全文", "阅读全文", "PDF下载", "在线客服"]):
                        title = t
                        link = a.get('href', '')
                        break  
                
                if not title or not link:
                    continue
                    
                if link.startswith('/'):
                    link = "https://www.yiigle.com" + link
                elif not link.startswith('http'):
                    link = base_url

                if link in seen_links:
                    continue

                node_text = node.get_text(" ", strip=True)
                
                # 1. 提取出版日期并转为标准 RSS 时间格式 (放宽正则限制)
                pub_date_xml = ""
                display_date = "未知时间"
                date_match = re.search(r'(\d{4}-\d{2}-\d{2})', node_text)
                if date_match:
                    display_date = date_match.group(1)
                    try:
                        dt = datetime.strptime(display_date, "%Y-%m-%d")
                        pub_date_xml = f"<pubDate>{dt.strftime('%a, %d %b %Y 00:00:00 GMT')}</pubDate>"
                    except ValueError:
                        pass
                
                # 2. 提取期数 (匹配 "2026年42卷04期" 或 "2026, 42(04)")
                issue_info = "最新优先发表"
                issue_match = re.search(r'(\d{4}年\d+卷\d+期)', node_text)
                if not issue_match:
                    issue_match = re.search(r'(\d{4},\s*\d+\(\d+\))', node_text)
                if issue_match:
                    issue_info = issue_match.group(1)

                # 提取作者
                authors = "本刊编辑部"
                author_tags = node.select('.author_sec a.linkuser') or node.select('a.linkuser')
                if author_tags:
                    authors = ", ".join([auth.get_text(strip=True) for auth in author_tags if auth.get_text(strip=True)])
                
                # 提取摘要
                abstract = "无摘要"
                abs_tag = node.select_one('.s_searchResult_li_info')
                if abs_tag:
                    abstract = abs_tag.get_text(strip=True)

                # 拼接 XML
                item_xml = f"""
        <item>
            <title><![CDATA[{title}]]></title>
            <link>{link}</link>
            <guid isPermaLink="false">{link}</guid>
            <author><![CDATA[{authors}]]></author>
            {pub_date_xml}
            <description><![CDATA[<b>期数：</b>{issue_info}<br><b>出版日期：</b>{display_date}<br><b>作者：</b>{authors}<br><br><b>摘要：</b>{abstract}]]></description>
        </item>"""
                rss_items.append(item_xml)
                seen_links.add(link)
                valid_count += 1
        else:
            # 兜底模式
            all_a_tags = soup.select('a[href*="/cmaid/"], a[href*="/article/"]')
            for a_tag in all_a_tags:
                title = a_tag.get('title') or a_tag.get_text(" ", strip=True)
                link = a_tag.get('href', '') or ''
                
                if not title or len(title.strip()) < 2 or any(kw in title for kw in ["下载全文", "阅读全文", "PDF下载", "在线客服"]):
                    continue
                    
                if link.startswith('/'):
                    link = "https://www.yiigle.com" + link
                elif not link.startswith('http'):
                    link = base_url

                if link in seen_links:
                    continue

                item_xml = f"""
        <item>
            <title><![CDATA[{title}]]></title>
            <link>{link}</link>
            <guid isPermaLink="false">{link}</guid>
            <author><![CDATA[本刊编辑部]]></author>
            <description><![CDATA[<b>期数：</b>最新捕获<br><b>作者：</b>本刊编辑部<br><br><b>摘要：</b>无摘要]]></description>
        </item>"""
                rss_items.append(item_xml)
                seen_links.add(link)
                valid_count += 1

        print(f"成功捕获 {valid_count} 篇真实文献")
        break 

    page.close()

    if not rss_items:
        return False

    tz = timezone(timedelta(hours=8))
    pub_date_str = datetime.now(tz).strftime("%a, %d %b %Y %H:%M:%S +0800")
    
    rss_xml = f"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
    <channel>
        <title>{journal_name} - 最新文献</title>
        <link>{base_url}</link>
        <description>{journal_name} - 自动聚合源</description>
        <lastBuildDate>{pub_date_str}</lastBuildDate>
        {"".join(rss_items)}
    </channel>
</rss>"""

    output_path = os.path.abspath(os.path.join(os.path.dirname(__file__), output_filename))
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(rss_xml)
    
    print(f"  └─ ✅ 成功存盘: {output_path}，RSS 结构闭合。")
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
        browser = p.chromium.launch(channel="chrome", headless=True) 
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
        for target in targets:
            if fetch_cma_journal(context, target['url'], target['name'], target['filename']):
                updated_any = True
        browser.close()
        
    if updated_any:
        push_to_github()
        
    print("\n" + "=" * 55)