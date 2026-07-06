"""Debug: 看更详细的 console + 触发后状态"""
from playwright.sync_api import sync_playwright
import time

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context()
    page = context.new_page()

    logs = []
    page.on("console", lambda msg: logs.append(f"[{msg.type}] {msg.text}"))
    page.on("pageerror", lambda e: logs.append(f"[ERROR] {e}"))

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

    # 等更长时间让 backfill + fetchUsers
    time.sleep(15)

    # 触发 cluster → dealer
    print("=== 触发 cluster → dealer ===")
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
            return {status: resp.status, text: await resp.text()};
        }
    """)
    print(f"API: {response}")

    time.sleep(3)

    # 找 CMjRBjRW 行
    info = page.evaluate("""
        () => {
            const rows = document.querySelectorAll('#userBody tr');
            const all = [];
            for (const row of rows) {
                const addrCell = row.cells[0];
                const dataAddr = addrCell?.getAttribute('data-addr') || '';
                if (dataAddr) {
                    all.push(dataAddr.substring(0, 12));
                }
            }
            // 找 CMjRBjRW
            let cmj = null;
            for (const row of rows) {
                const addrCell = row.cells[0];
                const dataAddr = addrCell?.getAttribute('data-addr') || '';
                if (dataAddr.startsWith('CMjRBjRW')) {
                    const statusBadge = row.querySelector('td:nth-child(2) .badge');
                    const clusterTypeBadge = row.querySelector('td:nth-child(5) .badge');
                    cmj = {
                        addr: dataAddr.substring(0, 12),
                        status_text: statusBadge?.textContent,
                        status_class: statusBadge?.className,
                        cluster_type_text: clusterTypeBadge?.textContent,
                        cluster_type_class: clusterTypeBadge?.className,
                    };
                    break;
                }
            }
            // 顶部指数
            const topBet = document.getElementById('metricBet')?.textContent;
            const topProfit = document.getElementById('metricProfit')?.textContent;
            return {totalRows: rows.length, allAddrs: all, cmj, topBet, topProfit};
        }
    """)
    print(f"\n=== UI 状态: {info} ===")

    print("\n=== Console 日志（按时间，含 user_status） ===")
    for log in logs:
        if 'user_status' in log or '簇类型' in log or 'dealer' in log.lower() or '持仓' in log or '买入' in log or '卖出' in log:
            print(log[:200])

    browser.close()