"""测试 cluster_api 改簇后 userBody 状态（用 all 过滤器）"""
from playwright.sync_api import sync_playwright
import time

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context()
    page = context.new_page()

    logs = []
    page.on("console", lambda msg: logs.append(f"[{msg.type}] {msg.text}"))

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

    # 设置过滤器为 all
    page.select_option('#userStatusFilter', 'all')
    time.sleep(1)

    def get_cmj_state():
        return page.evaluate("""
            () => {
                const rows = document.querySelectorAll('#userBody tr');
                for (const row of rows) {
                    const addrCell = row.cells[0];
                    const dataAddr = addrCell?.getAttribute('data-addr') || '';
                    if (dataAddr.startsWith('CMjRBjRW')) {
                        const statusBadge = row.querySelector('td:nth-child(2) .badge');
                        const clusterTypeBadge = row.querySelector('td:nth-child(5) .badge');
                        return {
                            status: statusBadge?.textContent,
                            cluster_type: clusterTypeBadge?.textContent,
                        };
                    }
                }
                return null;
            }
        """)

    print(f"=== 修改前 CMjRBjRW 行: {get_cmj_state()} ===")
    print(f"=== 修改前 metricBet: {page.locator('#metricBet').text_content()} ===")
    print(f"=== 修改前 metricProfit: {page.locator('#metricProfit').text_content()} ===")

    # 触发 cluster → dealer
    response = page.evaluate("""
        async () => {
            const resp = await fetch('/admin/api/clusters/CMjRBjRW2P3mDkZr4HAK9RmEtTVmwj5EAsev1wkogH9g_BUY/type', {
                method: 'PUT',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    cluster_type: 'dealer',
                    judgment_type: 'manual',
                    mint: '75ckspwxGRmXT8iKJ2H98mdm8ndQ5RNnwYYPV5t2pump'
                })
            });
            return await resp.text();
        }
    """)
    print(f"\n=== API: {response} ===")
    time.sleep(3)

    print(f"\n=== 修改后 CMjRBjRW 行: {get_cmj_state()} ===")
    print(f"=== 修改后 metricBet: {page.locator('#metricBet').text_content()} ===")
    print(f"=== 修改后 metricProfit: {page.locator('#metricProfit').text_content()} ===")

    # 触发 cluster → retail
    response = page.evaluate("""
        async () => {
            const resp = await fetch('/admin/api/clusters/CMjRBjRW2P3mDkZr4HAK9RmEtTVmwj5EAsev1wkogH9g_BUY/type', {
                method: 'PUT',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    cluster_type: 'retail',
                    judgment_type: 'manual',
                    mint: '75ckspwxGRmXT8iKJ2H98mdm8ndQ5RNnwYYPV5t2pump'
                })
            });
            return await resp.text();
        }
    """)
    print(f"\n=== API: {response} ===")
    time.sleep(3)

    print(f"\n=== 再修改后 CMjRBjRW 行: {get_cmj_state()} ===")
    print(f"=== 再修改后 metricBet: {page.locator('#metricBet').text_content()} ===")
    print(f"=== 再修改后 metricProfit: {page.locator('#metricProfit').text_content()} ===")

    # console 日志
    print("\n=== console 日志 (user_status / cluster_type / 状态) ===")
    for log in logs:
        if 'user_status' in log or '簇类型' in log or 'updateUserRow' in log:
            print(log[:250])

    browser.close()