from DrissionPage import ChromiumPage, ChromiumOptions
import os
import sys
import subprocess
import time

def force_kill_edge():
    """多重手段彻底关闭所有 Edge 浏览器进程"""
    try:
        subprocess.run(['osascript', '-e', 'tell application "Microsoft Edge" to quit'], capture_output=True, timeout=5)
        time.sleep(1)
    except:
        pass
    try:
        subprocess.run(["pkill", "-9", "-f", "Microsoft Edge"], capture_output=True)
    except:
        pass
    try:
        subprocess.run(["killall", "-9", "Microsoft Edge"], capture_output=True)
    except:
        pass
    try:
        subprocess.run(["killall", "-9", "msedge_crashpad_handler"], capture_output=True)
    except:
        pass

def show_alert(msg, title):
    applescript = f'display dialog "{msg}" buttons {{"取消", "继续"}} default button "继续" with title "{title}" with icon note'
    res = subprocess.run(['osascript', '-e', applescript], capture_output=True, text=True)
    if "User canceled" in res.stderr or "button returned:取消" in res.stdout:
        return False
    return True

def main():
    co = ChromiumOptions()
    co.set_argument('--no-sandbox')
    co.set_argument('--disable-gpu')
    co.set_browser_path('/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge')
    co.set_argument('--remote-debugging-port=9222')
    
    base_dir = os.path.dirname(os.path.abspath(__file__))
    profile_dir = os.path.join(base_dir, "lww_browser_profile")
    co.set_user_data_path(profile_dir)
    
    print("=" * 60)
    print("      LWW / Ovid 爬虫本地缓存预授权工具 (GUI)")
    print("=" * 60)
    
    page = ChromiumPage(co)
    
    try:
        # 1. 预热 Ovid 域名
        print("🌐 [第一步] 正在打开 Ovid 平台目标页...")
        page.get('https://www.ovid.com/jnls/aswcjournal/toc/current')
        
        ok = show_alert(
            "👉【第一步：Ovid】\n\n请在弹出的 Edge 窗口中确认已成功加载页面内容（如遇验证码请手动勾选通过）。\n\n确认加载出文献目录后，点击下方【继续】切换到下一域名。",
            "LWW/Ovid 预授权 - 步骤 1/2"
        )
        if not ok:
            print("❌ 用户取消了授权流程")
            return
            
        # 2. 预热 journals.lww.com 域名
        print("🌐 [第二步] 正在打开 LWW 旧版官网目标页...")
        page.get('https://journals.lww.com/plasreconsurg/toc/current')
        
        ok = show_alert(
            "👉【第二步：LWW / PRS】\n\n请在 Edge 窗口中确认已成功加载 PRS 期刊页面（如遇验证码请手动勾选通过）。\n\n确认加载出文献目录后，点击下方【继续】完成授权流程。",
            "LWW/Ovid 预授权 - 步骤 2/2"
        )
        if not ok:
            print("❌ 用户取消了授权流程")
            return
            
    finally:
        # 多重清理，确保 Edge 不残留
        try:
            page.quit()
        except:
            pass
        time.sleep(1)
        force_kill_edge()
            
    # 弹出成功提示
    subprocess.run([
        'osascript', '-e',
        'display dialog "✨ 预授权成功！\n\nCookie 通行证已安全保存在本地。爬虫管线现在可以后台静默工作了。" buttons {"确定"} default button "确定" with title "LWW/Ovid 预授权完成" with icon note'
    ])
    print("✨ 预授权全部成功完成！")

if __name__ == '__main__':
    main()
