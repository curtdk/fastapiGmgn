"""完整 modal 流程测试"""
from playwright.sync_api import sync_playwright
import time

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context()
    page = context.new_page()

    logs = []
    page.on("console", lambda msg: logs.append(f"[{msg.type}] {msg.text}"))
    # 处理 alert 自动确认
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

    # 设置 all 过滤器
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
                        const clusterTypeBadge = row.querySelector('td:nth-child(5) .badge');
                        cmj = {status: statusBadge?.textContent, cluster_type: clusterTypeBadge?.textContent};
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

    # 通过 modal 修改 cluster 类型
    print("\n=== 点 cluster 名打开 modal ===")
    cmj_link = page.locator('tr:has(td[data-addr^="CMjRBjRW"]) a').first
    cmj_link.click()
    time.sleep(3)

    modal_state = page.evaluate("""
        () => ({
            visible: document.getElementById('clusterDetailModal')?.classList.contains('show'),
            selectValue: document.getElementById('cdmClusterTypeSelect')?.value,
        })
    """)
    print(f"Modal: {modal_state}")

    # 用 JS 调保存函数（避免处理 save button 选择器）
    print("\n=== 改 cluster → dealer (通过 modal) ===")
    page.evaluate("""
        () => {
            document.getElementById('cdmClusterTypeSelect').value = 'dealer';
            document.getElementById('cdmClusterSystemJudgment').checked = true;
            saveClusterDetailType();
        }
    """)
    time.sleep(4)

    print("\n=== dealer 后 ===")
    print(state())

    # 再改回 retail
    print("\n=== 改回 retail (通过 modal) ===")
    page.evaluate("""
        () => {
            document.getElementById('cdmClusterTypeSelect').value = 'retail';
            document.getElementById('cdmClusterSystemJudgment').checked = true;
            saveClusterDetailType();
        }
    """)
    time.sleep(4)

    print("\n=== retail 后 ===")
    print(state())

    print("\n=== console 日志（user_status / cluster_type / cluster_matched） ===")
    for log in logs[-20:]:
        if '簇类型' in log or 'dealer' in log.lower() or 'user_status' in log.lower():
            print(log[:250])

    browser.close()