"""测试 saveUserStatus modal 修改用户状态后指数刷新"""
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

    # all 过滤器
    page.select_option('#userStatusFilter', 'all')
    time.sleep(1)

    def state():
        return page.evaluate("""
            () => {
                const rows = document.querySelectorAll('#userBody tr');
                let cmj = null;
                for (const row of rows) {
                    const addrCell = row.cells[0];
                    const dataAddr = addrCell?.getAttribute('data-addr') || '';
                    if (dataAddr.startsWith('CMjRBjRW')) {
                        const statusBadge = row.querySelector('td:nth-child(2) .badge');
                        cmj = {status: statusBadge?.textContent};
                        break;
                    }
                }
                return {
                    cmj,
                    bet: document.getElementById('metricBet')?.textContent,
                    profit: document.getElementById('metricProfit')?.textContent,
                };
            }
        """)

    print("=== 修改前 ===")
    print(state())

    # 测试 1: editUserStatus modal 改 CMjRBjRW 状态为 dealer
    # 先确保它是 retail
    print("\n=== 测试 1: retail → dealer (editUserStatus modal) ===")

    # 调 editUserStatus 打开 modal
    page.evaluate("""
        () => {
            editUserStatus('CMjRBjRW2P3mDkZr4HAK9RmEtTVmwj5EAsev1wkogH9g', 'retail');
        }
    """)
    time.sleep(2)

    # 设置 status 为 dealer（这个测试用 manual source）
    page.evaluate("""
        () => {
            document.getElementById('userStatusSelect').value = 'dealer';
            document.getElementById('userStatusSystemCheck').checked = false;
            saveUserStatus();
        }
    """)
    time.sleep(3)
    print("after dealer:", state())

    # 测试 2: 改回 retail
    print("\n=== 测试 2: dealer → retail (editUserStatus modal) ===")
    page.evaluate("""
        () => {
            editUserStatus('CMjRBjRW2P3mDkZr4HAK9RmEtTVmwj5EAsev1wkogH9g', 'dealer');
        }
    """)
    time.sleep(2)
    page.evaluate("""
        () => {
            document.getElementById('userStatusSelect').value = 'retail';
            document.getElementById('userStatusSystemCheck').checked = false;
            saveUserStatus();
        }
    """)
    time.sleep(3)
    print("after retail:", state())

    print("\n=== console 日志 ===")
    for log in logs[-10:]:
        print(log[:200])

    browser.close()