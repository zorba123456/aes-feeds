#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=============================================================================
Project: lit_auto_pipeline (aes-intel platform)
File: aes-feeds/ktn_downloader.py
Version: V2.2.1 (OPML 路径修复版)
Description:
    100% 保持现状抓取逻辑与 Edge 图形弹窗行为。
    从 KTN 邮件原文中逆向提取 Google Scholar 原始订阅词，落地为 ktn_[关键词].xml。
    每次运行结束后，自动生成包含精确 GitHub 根目录路径的总目录 OPML 文件。
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
KTN_RSS_URL = "https://kill-the-newsletter.com/feeds/uwgwyb1cnivki39x.xml"
LOCAL_BACKUP_XML = "aes-feeds/uwgwyb1cnivki39x.xml"

PROXIES = {
    "http": "http://127.0.0.1:29758",
    "https": "http://127.0.0.1:29758"
}
# ======================================================

def clean_text_noise(text):
    """清理 Unicode 替换字符及多余空格"""
    if not text:
        return ""
    cleaned = text.replace('\ufffd', '').replace('\u0000', '')
    cleaned = re.sub(r'\?{2,}', '', cleaned)
    return re.sub(r'\s+', ' ', cleaned).strip()

def sanitize_filename(name):
    """将关键词安全转换为物理磁盘文件名"""
    if not name:
        return "unknown"
    s = name.replace('"', '').replace("'", '').strip()
    s = re.sub(r'[^a-zA-Z0-9\u4e00-\u9fa5]+', '_', s)
    return s.lower().strip('_')

def extract_scholar_keyword(html_body):
    """从邮件全文中精准剥离出 Google Scholar 的原始订阅关键词"""
    text = html_body.get_text()
    
    zh_match = re.search(r'因为您关注了\s*\[(.*?)\]\s*的新搜索结果', text)
    if zh_match:
        return zh_match.group(1).strip()
        
    en_match = re.search(r'following new results for\s*\[(.*?)\]', text)
    if en_match:
        return en_match.group(1).strip()
        
    return None

def parse_single_mail(html_content):
    """解析单封邮件，提取该邮件所属的唯一细分源及其包含的全部文献"""
    soup = BeautifulSoup(html_content, 'html.parser')
    
    keyword = extract_scholar_keyword(soup)
    source_type = "Google Scholar"
    
    if not keyword:
        keyword = "Unknown_Source"
        source_type = "External"

    articles = []
    links = soup.find_all('a', href=True)
    
    for link in links:
        href = link['href']
        if "scholar.google.com/scholar_url" in href or "scholar.google.com/scholar?" in href:
            try:
                title_text = clean_text_noise(link.get_text())
                if not title_text or title_text.lower() in ["[pdf]", "[html]", "获取全文", "cites"]:
                    continue
                
                raw_url = href
                if "scholar_url?" in href:
                    parsed_url = urlparse(href)
                    qs = parse_qs(