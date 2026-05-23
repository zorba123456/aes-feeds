#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=============================================================================
Project: lit_auto_pipeline (aes-intel platform)
File: aes-feeds/summary_reporter.py
Version: V1.0.0
Description:
    数据大盘汇总简报生成器。
    从日志文件末尾逆序向前检索最新一轮 === Start 标记作为边界，
    精准解析 [REPORT] 标签并输出对齐美观的 Unicode 表格。
=============================================================================
"""

import os
import re
import glob

# ==================== 物理配置区域 ====================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.environ.get("AES_OUT_DIR", os.path.join(os.path.dirname(BASE_DIR), "logs"))
# ======================================================

def get_visual_width(s):
    """计算包含中英文字符串的终端视觉宽度"""
    width = 0
    for char in s:
        if ord(char) > 0x7F:
            width += 2
        else:
            width += 1
    return width

def visual_ljust(s, width, fillchar=' '):
    """根据视觉宽度进行左对齐填充"""
    curr = get_visual_width(s)
    if curr >= width:
        return s
    return s + fillchar * (width - curr)

def visual_rjust(s, width, fillchar=' '):
    """根据视觉宽度进行右对齐填充"""
    curr = get_visual_width(s)
    if curr >= width:
        return s
    return fillchar * (width - curr) + s

def parse_latest_run_reports(log_path):
    """解析单个日志文件中最后一轮运行的 [REPORT] 记录"""
    if not os.path.exists(log_path):
        return []
        
    try:
        with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
    except Exception as e:
        print(f"⚠️ 无法读取日志文件 {os.path.basename(log_path)}: {e}")
        return []

    # 从后往前寻找最新一轮的 Start 标志
    start_line_idx = -1
    for idx in range(len(lines) - 1, -1, -1):
        line = lines[idx]
        if "===" in line and "Start" in line:
            start_line_idx = idx
            break

    # 如果未找到 Start 标志，说明本轮未正常启动或日志不完整，返回空以保证数据绝对精准
    if start_line_idx == -1:
        return []
    latest_lines = lines[start_line_idx:]

    # 正则提取标签数据
    pattern = re.compile(r'\[REPORT\] CHANNEL=(\S+)\s+ITEM=(.*?)\s+COUNT=(\d+)\s+STATUS=(\S+)')
    reports = []
    
    for line in latest_lines:
        match = pattern.search(line)
        if match:
            channel, item, count, status = match.groups()
            reports.append({
                "channel": channel,
                "item": item,
                "count": int(count),
                "status": status
            })
            
    return reports

def print_summary_table(reports):
    """输出美观的 Unicode 大盘汇总表格"""
    # 定义列视觉宽度
    w_channel = 10
    w_item = 40
    w_count = 10
    w_status = 12

    # 计算总宽度
    total_width = w_channel + w_item + w_count + w_status + 5 # 加上边框分割线数量

    # 打印顶部边框
    print("┌" + "─" * total_width + "┐")
    title_text = "AES-INTEL 文献大盘更新简报"
    title_padding = (total_width - get_visual_width(title_text)) // 2
    print("│" + " " * title_padding + title_text + " " * (total_width - title_padding - get_visual_width(title_text)) + "│")
    print("├" + "─" * w_channel + "┬" + "─" * w_item + "┬" + "─" * w_count + "┬" + "─" * w_status + "┤")
    
    # 打印表头
    h_chan = visual_ljust(" 监测通道", w_channel)
    h_item = visual_ljust(" 子源 / 订阅项", w_item)
    h_coun = visual_ljust(" 新增数", w_count)
    h_stat = visual_ljust(" 运行状态", w_status)
    print(f"│{h_chan}│{h_item}│{h_coun}│{h_stat}│")
    print("├" + "─" * w_channel + "┼" + "─" * w_item + "┼" + "─" * w_count + "┼" + "─" * w_status + "┤")

    # 打印各行数据
    total_new_docs = 0
    total_channels = set()
    total_items = 0

    # 绿色表示 SUCCESS，红色表示 FAIL (如果终端支持 ANSI 逃逸字符，这里做简单适配)
    for rep in reports:
        total_new_docs += rep["count"]
        total_channels.add(rep["channel"])
        total_items += 1

        channel_str = f" {rep['channel']}"
        item_str = f" {rep['item']}"
        count_str = f" {rep['count']}"
        
        # 针对状态加上简单的 ANSI 颜色标识
        if rep["status"] == "SUCCESS":
            status_str = " \033[92mSUCCESS\033[0m"
            padded_status = visual_ljust(status_str, w_status + 9) # 9 是 ANSI 颜色字符长度
        else:
            status_str = " \033[91mFAIL\033[0m"
            padded_status = visual_ljust(status_str, w_status + 9)

        p_chan = visual_ljust(channel_str, w_channel)
        p_item = visual_ljust(item_str, w_item)
        p_coun = visual_ljust(count_str, w_count)
        
        print(f"│{p_chan}│{p_item}│{p_coun}│{padded_status}│")

    print("├" + "─" * w_channel + "┴" + "─" * w_item + "┴" + "─" * w_count + "┴" + "─" * w_status + "┤")
    
    # 打印底部统计数据
    stats_text = f" 汇总: {len(total_channels)} 个大通道, {total_items} 个细分订阅项, 共新增文献 {total_new_docs} 篇"
    print("│" + visual_ljust(stats_text, total_width) + "│")
    print("└" + "─" * total_width + "┘")

def main():
    log_files = glob.glob(os.path.join(LOG_DIR, "*_new.log"))
    all_reports = []
    
    # 按照 LWW, CMA, KTN, CNKI 顺序对通道排序，使用户看起来更有层次感
    channel_priority = {"lww_new.log": 1, "cma_new.log": 2, "ktn_new.log": 3, "cnki_new.log": 4}
    log_files.sort(key=lambda x: channel_priority.get(os.path.basename(x), 99))

    for log_path in log_files:
        reports = parse_latest_run_reports(log_path)
        all_reports.extend(reports)

    if not all_reports:
        print("\n⚠️  未在 logs/ 中检测到任何有效的本轮 [REPORT] 数据。")
        return

    print("\n")
    print_summary_table(all_reports)
    print("\n")

if __name__ == "__main__":
    main()
