"""Debug: 勾选系统判定保存为未定义后，userData 里 status 是什么"""
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

    # 默认就是 retail_unknown
    # 找 CMjRBjRW 用户行
    def inspect():
        return page.evaluate("""
            () => {
                const rows = document.querySelectorAll('#userBody tr');
                let cmj = null;
                let totalRows = rows.length;
                for (const row of rows) {
                    const addrCell = row.cells[0];
                    const dataAddr = addrCell?.getAttribute('data-addr') || '';
                    if (dataAddr.startsWith('CMjRBjRW')) {
                        const statusBadge = row.querySelector('td:nth-child(2) .badge');
                        cmj = {status: statusBadge?.textContent};
                        break;
                    }
                }
                // 直接看 userData
                const ud = window.userData || {};
                const udCmj = ud['CMjRBjRW2P3mDkZr4HAK9RmEtTVmwj5EAsev1wkogH9g'];
                return {
                    visible_rows: totalRows,
                    cmj_in_dom: cmj,
                    cmj_in_userData: udCmj ? {status: udCmj.status, status_source: udCmj.status_source} : null,
                };
            }
        """)

    print("=== 当前过滤 retail_unknown ===")
    print(inspect())

    # 模拟你描述的场景：勾选系统判定 + 保存为未定义
    print("\n=== 改 CMjRBjRW 为 unknown + 系统判定 ===")
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
    print("after save:", inspect())

    # 切换过滤器
    print("\n=== 切到 all ===")
    page.select_option('#userStatusFilter', 'all')
    time.sleep(1)
    print(inspect())

    # 再切回 retail_unknown
    print("\n=== 切回 retail_unknown ===")
    page.select_option('#userStatusFilter', 'retail_unknown')
    time.sleep(1)
    print(inspect())

    # 切到 unknown
    print("\n=== 切到 unknown ===")
    page.select_option('#userStatusFilter', 'unknown')
    time.sleep(1)
    print(inspect())

    print("\n=== console 日志 ===")
    for log in logs[-15:]:
        print(log[:200])

    browser.close()