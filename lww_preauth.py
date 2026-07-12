from DrissionPage import ChromiumPage, ChromiumOptions
import os

def main():
    co = ChromiumOptions()
    co.set_argument('--no-sandbox')
    co.set_argument('--disable-gpu')
    co.set_browser_path('/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge')
    co.set_argument('--remote-debugging-port=9222')
    
    # 🟢 严格复用爬虫主程序的本地浏览器缓存目录
    base_dir = os.path.dirname(os.path.abspath(__file__))
    profile_dir = os.path.join(base_dir, "lww_browser_profile")
    co.set_user_data_path(profile_dir)
    
    print("=" * 60)
    print("      LWW / Ovid 爬虫本地缓存预授权工具 (Cloudflare Pre-Auth)")
    print("=" * 60)
    print(f"📂 本地缓存目录: {profile_dir}\n")
    print("👉 我们将开启浏览器，请在弹出的浏览器窗口中：")
    print("   1. 如果遇到 Cloudflare [Just a moment...] 验证，请【手动点击验证框】通过验证。")
    print("   2. 通过后，让页面完全加载出学术目录内容。")
    print("   3. 随后回到本终端按回车，程序会自动记录下 clearance 授权 Cookie，免去日后重复验证。")
    print("=" * 60)
    
    page = ChromiumPage(co)
    
    # 1. 预热 Ovid 域名
    print("\n🌐 [第一步] 正在打开 Ovid 平台目标页...")
    page.get('https://www.ovid.com/jnls/aswcjournal/toc/current')
    input("👉 [Ovid] 请在 Edge 窗口中确认已成功加载页面内容。完成后在此终端按 [回车/Enter] 继续...")
    
    # 2. 预热 journals.lww.com 域名
    print("\n🌐 [第二步] 正在打开 LWW 旧版官网目标页...")
    page.get('https://journals.lww.com/plasreconsurg/toc/current')
    input("👉 [LWW] 请在 Edge 窗口中确认已成功加载页面内容。完成后在此终端按 [回车/Enter] 继续...")
    
    page.quit()
    print("\n✨ 预授权成功！Cookie 及 clearance 凭证已保存在本地缓存。")
    print("🚀 现在重新运行爬虫或通过 Cron 定时任务，即可在后台静默通过验证。")

if __name__ == '__main__':
    main()
