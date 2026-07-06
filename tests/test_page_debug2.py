"""Debug: 看 trade_live.html 实际有哪些 ID"""
from playwright.sync_api import sync_playwright
import time

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context()
    page = context.new_page()

    page.on("console", lambda msg: print(f"[CONSOLE] {msg.text}"))

    page.goto("http://localhost:8000/admin/login")
    page.wait_for_load_state("networkidle")
    page.fill('input[name="username"]', 'admin')
    page.fill('input[name="password"]', 'admin123')
    page.click('button[type="submit"]')
    page.wait_for_load_state("networkidle")

    page.goto("http://localhost:8000/admin/trade")
    page.wait_for_load_state("networkidle")
    print(f"URL: {page.url}")

    # 列出所有 id
    ids = page.evaluate("""
        () => {
            const ids = [];
            document.querySelectorAll('[id]').forEach(el => ids.push(el.id));
            return ids;
        }
    """)
    print(f"页面 IDs: {ids[:30]}")

    # 触发一次 fetchUsers + backfill
    page.fill("#mintInput", "75ckspwxGRmXT8iKJ2H98mdm8ndQ5RNnwYYPV5t2pump")
    page.click("#startBtn")

    # 等 backfill
    time.sleep(10)

    # 找 CMjRBjRW 在 userBody 里的状态
    cmj_state = page.evaluate("""
        () => {
            const rows = document.querySelectorAll('#userBody tr');
            for (const row of rows) {
                const addrCell = row.cells[0];
                const dataAddr = addrCell?.getAttribute('data-addr') || '';
                if (dataAddr.startsWith('CMjRBjRW')) {
                    const statusBadge = row.querySelector('td:nth-child(2) .badge');
                    const clusterTypeBadge = row.querySelector('td:nth-child(5) .badge');
                    return {
                        addr: dataAddr.substring(0, 12),
                        status: statusBadge?.textContent,
                        cluster_type: clusterTypeBadge?.textContent,
                        full: row.textContent.replace(/\\s+/g, ' ').trim().substring(0, 250)
                    };
                }
            }
            return null;
        }
    """)
    print(f"\n=== CMjRBjRW 行: {cmj_state} ===")

    # 触发 cluster_api
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

    # 等 WS 推送
    time.sleep(3)

    cmj_state_after = page.evaluate("""
        () => {
            const rows = document.querySelectorAll('#userBody tr');
            for (const row of rows) {
                const addrCell = row.cells[0];
                const dataAddr = addrCell?.getAttribute('data-addr') || '';
                if (dataAddr.startsWith('CMjRBjRW')) {
                    const statusBadge = row.querySelector('td:nth-child(2) .badge');
                    const clusterTypeBadge = row.querySelector('td:nth-child(5) .badge');
                    return {
                        addr: dataAddr.substring(0, 12),
                        status: statusBadge?.textContent,
                        cluster_type: clusterTypeBadge?.textContent,
                        full: row.textContent.replace(/\\s+/g, ' ').trim().substring(0, 250)
                    };
                }
            }
            return null;
        }
    """)
    print(f"\n=== 修改后 CMjRBjRW 行: {cmj_state_after} ===")

    # 检查顶部指数文本
    top_text = page.evaluate("""
        () => {
            const all = document.body.innerText;
            // 找顶部 132.7288 这种数字
            const matches = all.match(/(?:本轮下注|总投入|总成本|已落袋|已实现|\\d+\\.\\d{3,8})/g);
            return matches?.slice(0, 20);
        }
    """)
    print(f"\n=== 页面数字/标签: {top_text} ===")

    browser.close()