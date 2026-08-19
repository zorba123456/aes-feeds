#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""定向探针: 抓取 4 个异常的 LWW 页面, 用真实 card_text 验证 parse_card_metadata 修复。
headful + 复用 lww_browser_profile(带 Cloudflare cookie)。不写任何 XML。"""
import os, sys, json, time
PROJ = os.path.dirname(os.path.abspath(__file__))  # aes-feeds
sys.path.insert(0, PROJ)
import lww_downloader as L

PROFILE = os.path.join(PROJ, "lww_browser_profile")

TARGETS = [
    ("jcranio_current", "https://journals.lww.com/jcraniofacialsurgery/toc/current", "Journal of Craniofacial Surgery"),
    ("jcso_current",    "https://www.ovid.com/jnls/jcso/toc/current",                    "Journal of Craniofacial Surgery Open"),
    ("jcso_latest",     "https://www.ovid.com/jnls/jcso/toc/latest",                     "Journal of Craniofacial Surgery Open"),
    ("prs_go_latest",   "https://journals.lww.com/prsgo/toc/latest",                     "Plastic and Reconstructive Surgery Global Open"),
    ("prs_go_current",  "https://journals.lww.com/prsgo/toc/current",                    "Plastic and Reconstructive Surgery Global Open"),
]

from playwright.sync_api import sync_playwright

def wait_cloudflare(page, name):
    # 轻量等 Cloudflare 挑战过去(最多 40s)
    for _ in range(16):
        try:
            t = page.title()
            if "Just a moment" in t or "cf" in t.lower() and "challenge" in t.lower():
                time.sleep(2.5); continue
            # 有没有文章 marker
            if page.locator(".js-omni-hydrate-marker").count() > 0:
                return True
        except Exception:
            pass
        time.sleep(2.5)
    return page.locator(".js-omni-hydrate-marker").count() > 0

def main():
    pw = sync_playwright().start()
    ctx = pw.chromium.launch_persistent_context(
        user_data_dir=PROFILE, channel="msedge", headless=False,
        args=['--no-sandbox','--disable-gpu','--disable-blink-features=AutomationControlled','--disable-automation'])
    ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
    page = ctx.new_page()
    for name, url, jname in TARGETS:
        print(f"\n===== {name} : {url} =====")
        try:
            page.goto(url, timeout=60000)
            if not wait_cloudflare(page, name):
                print("  !! 未通过 Cloudflare / 无 marker"); 
                # 仍尝试 dump 文本做诊断
            time.sleep(4)
            n = page.locator(".js-omni-hydrate-marker").count()
            print(f"   markers={n}")
            from lww_downloader import _extract_articles_from_page
            seen, arts = set(), []
            _extract_articles_from_page(page, jname, seen, arts)
            print(f"   extracted={len(arts)}")
            # 统计 pub_date 分布
            from collections import Counter
            c = Counter(a['pub_date'] for a in arts)
            print(f"   pub_date分布: {dict(c.most_common(6))}")
            # 抽 3 条打印
            for a in arts[:3]:
                print(f"    - [{a['pub_date']}] {a['title'][:40]!r} issue={a['issue']!r}")
        except Exception as e:
            print(f"  !! EXC {type(e).__name__}: {e}")
    ctx.close(); pw.stop()
    print("\nDONE")

if __name__ == "__main__":
    main()
