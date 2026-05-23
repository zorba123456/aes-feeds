#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import time
import hashlib
import requests
import feedparser
import subprocess
from playwright.sync_api import sync_playwright

# 配置区域
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROXY = "http://127.0.0.1:29758"
VERSION = "V4.0.5-GatedWAF"  # 版本号严格递增推进

def fetch_with_browser(url):
    """高级浏览器强攻模式：通过行为模拟与特征检测穿透 Cloudflare WAF"""
    print(f"🚀 [{VERSION}] 正在启动图形化强攻模式访问: {url}")
    
    p = None
    browser = None
    context = None
    page = None
    content = ""
    
    try:
        p = sync_playwright().start()
        browser = p.chromium.launch(
            channel="msedge", 
            headless=False, 
            proxy={"server": PROXY}
        )
        
        # 伪装常用浏览器凭证，降低被拦截概率
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0"
        )
        page = context.new_page()
        
        # 1. 尝试首次加载
        response = page.goto(url, timeout=60000)
        
        # 2. 行为模拟与动态反爬盾对抗
        print("⏳ 正在进行 WAF 盾对抗与页面渲染机制判读...")
        page.wait_for_timeout(5000)  # 强制硬等待，给 Cloudflare 挑战页面执行 JS 留出时间
        
        # 模拟轻量滚动，使浏览器环境在 WAF 探测中更趋近于真实人类
        try:
            page.evaluate("window.scrollTo(0, 100);")
            time.sleep(1)
            page.evaluate("window.scrollTo(0, 0);")
        except Exception:
            pass

        content = page.content()
        
        # 3. 校验获取的内容是否为真实文献
        if content and not any(k in content for k in ["<rss", "<feed", "<?xml"]):
            print("⚠️ 警告: 检测到当前页面可能被 Cloudflare 拦截 (非标准 XML)，尝试延长等待...")
            page.wait_for_timeout(10000)  # 追加等待
            content = page.content()
        
    except Exception as e:
        print(f"⚠️ 强攻异常: {e}")
        content = ""
        
    finally:
        # 安全断开，防死锁清场机制保持不动
        try:
            if page and not page.is_closed():
                page.close()
        except Exception:
            pass
        try:
            if context:
                context.close()
        except Exception:
            pass
        try:
            if browser:
                browser.close()
        except Exception:
            pass
        try:
            if p:
                p.stop()
        except Exception:
            pass
            
    return content

def main():
    print(f"=== [LWW] Start ({VERSION}): {time.ctime()} ===")
    
    targets = [
        "https://journals.lww.com/aswc/_layouts/15/oai/feed.aspx?feed=currentissue",
        "https://journals.lww.com/aswc/_layouts/15/oai/feed.aspx?feed=latestarticles",
        "https://journals.lww.com/annalsofplasticsurgery/_layouts/15/oai/feed.aspx?feed=currentissue",
        "https://journals.lww.com/annalsofplasticsurgery/_layouts/15/oai/feed.aspx?feed=latestarticles"
    ]
    
    names = [
        "aswc_current_issue",
        "aswc_latest_articles",
        "annals_plast_surg_current",
        "annals_plast_surg_latest"
    ]
    
    for url, name in zip(targets, names):
        print(f"\n📡 正在抓取: {name}")
        html_content = fetch_with_browser(url)
        
        if html_content and ("<rss" in html_content or "<feed" in html_content or "<?xml" in html_content):
            feed = feedparser.parse(html_content)
            items_count = len(feed.entries)
            print(f"📥 成功获取 XML 内容，共包含 {items_count} 条原始文献")
            
            cache_dir = os.path.join(BASE_DIR, "cache")
            os.makedirs(cache_dir, exist_ok=True)
            
            for entry in feed.entries:
                title = entry.get("title", "No Title")
                link = entry.get("link", "")
                guid = entry.get("id", link)
                
                guid_hash = hashlib.md5(guid.encode("utf-8")).hexdigest()
                cache_file = os.path.join(cache_dir, f"{guid_hash}.xml")
                
                if not os.path.exists(cache_file):
                    with open(cache_file, "w", encoding="utf-8") as f:
                        f.write(f"<item><title>{title}</title><link>{link}</link></item>")
                    
                    try:
                        cmd = ["python3", "push_to_wechat.py", title, link]
                        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    except Exception:
                        pass
            
            print(f"[REPORT] CHANNEL=LWW ITEM={name} COUNT={items_count} STATUS=SUCCESS")
        else:
            print(f"❌ 无法请求 {name} 或内容非标准 XML (可能遭遇 Cloudflare 403 挑战拦截)")
            print(f"[REPORT] CHANNEL=LWW ITEM={name} COUNT=0 STATUS=FAIL")
            
    print(f"=== [LWW] End ({VERSION}): {time.ctime()} ===")

if __name__ == "__main__":
    main()