"""Debug: 看 userData 里的 status 字段实际值"""
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

    page.fill("#mintInput", "75ckspwxGRmXT8iKJ2H98mdm8ndQ5RNnwYYPV5t2pump")
    page.click("#startBtn")
    time.sleep(12)

    # 改 CMjRBjRW 为 unknown
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

    # 检查所有用户的 status 字段
    print("=== 所有用户的 userData.status ===")
    all_status = page.evaluate("""
        () => {
            const ud = window.userData || {};
            const result = [];
            for (const addr in ud) {
                const u = ud[addr];
                result.push({addr: addr.substring(0, 8), status: u.status, status_source: u.status_source});
            }
            return result;
        }
    """)
    for u in all_status:
        print(f"  {u}")

    # 检查 userStatusFilter 的当前值
    filter_val = page.evaluate("() => document.getElementById('userStatusFilter').value")
    print(f"\n当前 filter: {filter_val}")

    # 用 unknown 过滤
    page.select_option('#userStatusFilter', 'unknown')
    time.sleep(1)

    # 再次检查 userData 不变
    print("\n=== 切到 unknown 后 ===")
    all_status2 = page.evaluate("""
        () => {
            const ud = window.userData || {};
            const result = [];
            for (const addr in ud) {
                const u = ud[addr];
                result.push({addr: addr.substring(0, 8), status: JSON.stringify(u.status), status_source: u.status_source});
            }
            return result;
        }
    """)
    for u in all_status2:
        print(f"  {u}")

    rows_count = page.evaluate("() => document.querySelectorAll('#userBody tr').length")
    print(f"\nuserBody 行数: {rows_count}")

    # 模拟 renderUserTable 的过滤逻辑
    expected = page.evaluate("""
        () => {
            const ud = window.userData || {};
            const userStatusFilter = 'unknown';
            const passed = [];
            for (const addr in ud) {
                const u = ud[addr];
                if (userStatusFilter === 'retail_unknown') {
                    if (u.status !== 'retail' && u.status !== 'unknown') continue;
                } else if (userStatusFilter !== 'all' && u.status !== userStatusFilter) {
                    continue;
                }
                passed.push({addr: addr.substring(0, 8), status: u.status});
            }
            return passed;
        }
    """)
    print(f"\n=== 模拟 filter=unknown 应通过的: {expected} ===")

    browser.close()