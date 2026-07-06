"""Debug: 找 CMjRBjRW 在哪"""
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

    # 等 backfill + 用户列表
    time.sleep(15)

    # 列出 userBody 所有地址
    list_info = page.evaluate("""
        () => {
            const rows = document.querySelectorAll('#userBody tr');
            const result = [];
            for (const row of rows) {
                const addrCell = row.cells[0];
                const dataAddr = addrCell?.getAttribute('data-addr') || '';
                const statusBadge = row.querySelector('td:nth-child(2) .badge');
                const clusterTypeBadge = row.querySelector('td:nth-child(5) .badge');
                result.push({
                    addr: dataAddr.substring(0, 12),
                    status: statusBadge?.textContent,
                    cluster_type: clusterTypeBadge?.textContent,
                });
            }
            return {rows: rows.length, list: result};
        }
    """)
    print(f"=== userBody: {list_info} ===")

    # 调 /admin/api/users 看后端返回什么
    api_users = page.evaluate("""
        async () => {
            const resp = await fetch('/admin/api/users?mint=75ckspwxGRmXT8iKJ2H98mdm8ndQ5RNnwYYPV5t2pump');
            const data = await resp.json();
            return data.users ? data.users.map(u => ({
                addr: u.address?.substring(0, 12),
                status: u.status,
                cluster_name: u.cluster_name?.substring(0, 12),
                cluster_type: u.cluster_type,
            })) : null;
        }
    """)
    print(f"\n=== API /admin/api/users 返回: {api_users} ===")

    # 查 DB 里 CMjRBjRW 的 status
    redis_status = page.evaluate("""
        async () => {
            // 用页面 fetch /admin/api/users 然后 filter
            const resp = await fetch('/admin/api/users?mint=75ckspwxGRmXT8iKJ2H98mdm8ndQ5RNnwYYPV5t2pump');
            const data = await resp.json();
            const cmj = (data.users || []).find(u => u.address?.startsWith('CMjRBjRW'));
            return cmj ? {
                addr: cmj.address?.substring(0, 12),
                status: cmj.status,
                cluster_name: cmj.cluster_name?.substring(0, 12),
                cluster_type: cmj.cluster_type,
            } : 'NOT FOUND IN API RESPONSE';
        }
    """)
    print(f"\n=== API 返回中 CMjRBjRW: {redis_status} ===")

    # 看 console 日志
    print("\n=== console 日志 ===")
    for log in logs[-30:]:
        print(log[:250])

    browser.close()