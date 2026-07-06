"""测试 cluster_api 改簇组状态后，前端是否正确刷新"""
from playwright.sync_api import sync_playwright
import time

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context()
    page = context.new_page()

    # 捕获 console
    console_logs = []
    page.on("console", lambda msg: console_logs.append(f"[{msg.type}] {msg.text}"))

    # 1. 登录
    page.goto("http://localhost:8000/admin/login")
    page.wait_for_load_state("networkidle")
    page.fill('input[name="username"]', 'admin')
    page.fill('input[name="password"]', 'admin123')
    page.click('button[type="submit"]')
    page.wait_for_load_state("networkidle")
    print(f"登录后 URL: {page.url}")

    # 2. 访问 /admin/trade
    page.goto("http://localhost:8000/admin/trade")
    page.wait_for_load_state("networkidle")
    print(f"trade URL: {page.url}")

    # 3. 输入 mint
    page.fill("#mintInput", "75ckspwxGRmXT8iKJ2H98mdm8ndQ5RNnwYYPV5t2pump")

    # 4. 点击开始
    page.click("#startBtn")

    # 5. 等 backfill 完成
    time.sleep(8)

    # 抓 userData 当前状态
    user_data_before = page.evaluate("""
        () => {
            const users = Object.values(window.userData || {});
            return users.map(u => ({
                address: (u.address || '').substring(0, 8),
                status: u.status,
                cluster_type: u.cluster_type,
                cluster_name: (u.cluster_name || '').substring(0, 12),
            })).slice(0, 8);
        }
    """)
    print("\n=== 修改前 userData（部分）===")
    for u in user_data_before:
        print(f"  {u}")

    # 抓顶部指数
    metrics_before = page.evaluate("""
        () => ({
            total_bet: document.getElementById('totalBet')?.textContent,
            realized: document.getElementById('realizedProfit')?.textContent,
        })
    """)
    print(f"=== 修改前指数: {metrics_before} ===")

    # 6. 调 cluster_api 改 cluster → dealer
    print("\n=== 触发 cluster → dealer ===")
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
    print(f"API 响应: {response}")

    # 7. 等 3 秒让 WS 推送
    time.sleep(3)

    # 抓 userData 当前状态
    user_data_after = page.evaluate("""
        () => {
            const users = Object.values(window.userData || {});
            return users.map(u => ({
                address: (u.address || '').substring(0, 8),
                status: u.status,
                cluster_type: u.cluster_type,
                cluster_name: (u.cluster_name || '').substring(0, 12),
            })).slice(0, 8);
        }
    """)
    print("\n=== 修改后 userData（部分）===")
    for u in user_data_after:
        print(f"  {u}")

    # 抓顶部指数
    metrics_after = page.evaluate("""
        () => ({
            total_bet: document.getElementById('totalBet')?.textContent,
            realized: document.getElementById('realizedProfit')?.textContent,
        })
    """)
    print(f"=== 修改后指数: {metrics_after} ===")

    # 8. 用户列表里 CMjRBjRW 的 status 显示
    cmj_row = page.evaluate("""
        () => {
            const rows = document.querySelectorAll('#userBody tr');
            for (const row of rows) {
                const addrCell = row.cells[0];
                const dataAddr = addrCell?.getAttribute('data-addr') || '';
                if (dataAddr.startsWith('CMjRBjRW')) {
                    const statusBadge = row.querySelector('td:nth-child(2) .badge');
                    return {
                        addr: dataAddr.substring(0, 12),
                        status_text: statusBadge?.textContent,
                        status_class: statusBadge?.className,
                        row_text: row.textContent.substring(0, 200)
                    };
                }
            }
            return null;
        }
    """)
    print(f"\n=== CMjRBjRW 用户行: {cmj_row} ===")

    # 9. 关键问题：userData 里的 status 是什么？
    cmj_userdata = page.evaluate("""
        () => {
            const u = window.userData?.['CMjRBjRW2P3mDkZr4HAK9RmEtTVmwj5EAsev1wkogH9g'];
            return u ? {status: u.status, cluster_type: u.cluster_type, holding_qty: u.holding_qty, holding_cost: u.holding_cost} : null;
        }
    """)
    print(f"=== CMjRBjRW userData: {cmj_userdata} ===")

    print("\n=== Console logs (最后 10 条) ===")
    for log in console_logs[-10:]:
        print(log)

    browser.close()