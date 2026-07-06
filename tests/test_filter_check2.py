"""Debug 2: 直接从全局查找"""
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

    # 看 userBody 所有行的 status
    all_rows = page.evaluate("""
        () => {
            const rows = document.querySelectorAll('#userBody tr');
            return Array.from(rows).map(r => {
                const addr = r.cells[0]?.getAttribute('data-addr')?.substring(0, 12) || '?';
                const status = r.querySelector('td:nth-child(2) .badge')?.textContent || '';
                return {addr, status};
            });
        }
    """)
    print("=== userBody 所有行 ===")
    for r in all_rows:
        print(r)

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

    # 看改后 userBody
    all_rows_after = page.evaluate("""
        () => {
            const rows = document.querySelectorAll('#userBody tr');
            return Array.from(rows).map(r => {
                const addr = r.cells[0]?.getAttribute('data-addr')?.substring(0, 12) || '?';
                const status = r.querySelector('td:nth-child(2) .badge')?.textContent || '';
                return {addr, status};
            });
        }
    """)
    print("\n=== 改 unknown 后 userBody ===")
    for r in all_rows_after:
        print(r)

    # 切到 unknown 过滤
    print("\n=== 切到 unknown 过滤 ===")
    page.select_option('#userStatusFilter', 'unknown')
    time.sleep(1)
    unknown_rows = page.evaluate("""
        () => {
            const rows = document.querySelectorAll('#userBody tr');
            return Array.from(rows).map(r => {
                const addr = r.cells[0]?.getAttribute('data-addr')?.substring(0, 12) || '?';
                const status = r.querySelector('td:nth-child(2) .badge')?.textContent || '';
                return {addr, status};
            });
        }
    """)
    for r in unknown_rows:
        print(r)

    # 切到 retail_unknown
    print("\n=== 切到 retail_unknown ===")
    page.select_option('#userStatusFilter', 'retail_unknown')
    time.sleep(1)
    ru_rows = page.evaluate("""
        () => {
            const rows = document.querySelectorAll('#userBody tr');
            return Array.from(rows).map(r => {
                const addr = r.cells[0]?.getAttribute('data-addr')?.substring(0, 12) || '?';
                const status = r.querySelector('td:nth-child(2) .badge')?.textContent || '';
                return {addr, status};
            });
        }
    """)
    for r in ru_rows:
        print(r)

    print("\n=== console 日志 ===")
    for log in logs[-10:]:
        print(log[:200])

    browser.close()