"""复现: 关闭后重新开始的状态筛选问题"""
from playwright.sync_api import sync_playwright
import time

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context()
    page = context.new_page()

    logs = []
    page.on("console", lambda msg: logs.append(f"[{msg.type}] {msg.text}"))
    page.on("dialog", lambda d: d.accept())

    page.goto("http://localhost:8000/admin/login")
    page.wait_for_load_state("networkidle")
    page.fill('input[name="username"]', 'admin')
    page.fill('input[name="password"]', 'admin123')
    page.click('button[type="submit"]')
    page.wait_for_load_state("networkidle")
    page.goto("http://localhost:8000/admin/trade")
    page.wait_for_load_state("networkidle")

    # 1. 第一次启动
    page.fill("#mintInput", "75ckspwxGRmXT8iKJ2H98mdm8ndQ5RNnwYYPV5t2pump")
    page.click("#startBtn")
    time.sleep(12)

    # 2. 把 CMjRBjRW 改为 unknown + 系统判定
    page.evaluate("""
        () => {
            editUserStatus('CMjRBjRW2P3mDkZr4HAK9RmEtTVmwj5EAsev1wkogH9g', 'retail');
        }
    """)
    time.sleep(2)
    page.evaluate("""
        () => {
            document.getElementById('userStatusSelect').value = 'unknown';
            document.getElementById('userStatusSystemCheck').checked = true;
            saveUserStatus();
        }
    """)
    time.sleep(4)

    # 3. 关闭
    page.click("#stopBtn")
    time.sleep(3)

    # 4. 重新开始
    page.click("#startBtn")
    time.sleep(15)

    # 5. 看默认 filter 是 retail_unknown
    default_filter = page.evaluate("() => document.getElementById('userStatusFilter').value")
    print(f"默认 filter: {default_filter}")

    # 6. 看用户列表
    rows_after_restart = page.evaluate("""
        () => {
            const rows = document.querySelectorAll('#userBody tr');
            return Array.from(rows).map(r => ({
                addr: r.cells[0]?.getAttribute('data-addr')?.substring(0, 12),
                status: r.querySelector('td:nth-child(2) .badge')?.textContent,
            }));
        }
    """)
    print("\n=== 关闭重启后 userBody ===")
    for r in rows_after_restart:
        print(f"  {r}")

    # 7. 切到 all 看 CMjRBjRW
    page.select_option('#userStatusFilter', 'all')
    time.sleep(1)
    rows_all = page.evaluate("""
        () => {
            const rows = document.querySelectorAll('#userBody tr');
            return Array.from(rows).map(r => ({
                addr: r.cells[0]?.getAttribute('data-addr')?.substring(0, 12),
                status: r.querySelector('td:nth-child(2) .badge')?.textContent,
            }));
        }
    """)
    print("\n=== 切到 all ===")
    for r in rows_all:
        print(f"  {r}")

    # 8. 切到 unknown
    page.select_option('#userStatusFilter', 'unknown')
    time.sleep(1)
    rows_unk = page.evaluate("""
        () => {
            const rows = document.querySelectorAll('#userBody tr');
            return Array.from(rows).map(r => ({
                addr: r.cells[0]?.getAttribute('data-addr')?.substring(0, 12),
                status: r.querySelector('td:nth-child(2) .badge')?.textContent,
            }));
        }
    """)
    print("\n=== 切到 unknown ===")
    for r in rows_unk:
        print(f"  {r}")

    print("\n=== console 日志 ===")
    for log in logs[-10:]:
        print(log[:200])

    browser.close()