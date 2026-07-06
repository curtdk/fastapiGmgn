"""精确测：fetchUsers 返回的 userData 内容"""
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

    # 1. 第一次启动
    page.fill("#mintInput", "75ckspwxGRmXT8iKJ2H98mdm8ndQ5RNnwYYPV5t2pump")
    page.click("#startBtn")
    time.sleep(12)

    # 2. 改 CMjRBjRW 为 unknown + 系统判定
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

    # 3. 关闭
    page.click("#stopBtn")
    time.sleep(3)

    # 4. 重新开始
    page.click("#startBtn")
    time.sleep(15)

    # 5. 直接调 fetchUsers 拿原始数据
    api_data = page.evaluate("""
        async () => {
            const resp = await fetch('/admin/api/users?mint=75ckspwxGRmXT8iKJ2H98mdm8ndQ5RNnwYYPV5t2pump');
            const data = await resp.json();
            return data.users.map(u => ({addr: u.address.substring(0, 8), status: u.status, cluster_type: u.cluster_type}));
        }
    """)
    print("=== API /admin/api/users 返回 ===")
    for u in api_data:
        print(f"  {u}")

    # 6. 改默认 filter 为 all 看真实数据
    page.select_option('#userStatusFilter', 'all')
    time.sleep(1)

    # 7. 用 userData 直接查（通过 evaluate 调 fetchUsers 然后遍历）
    # 先调 fetchUsers 让 userData 重置
    user_data_dump = page.evaluate("""
        async () => {
            await fetchUsers();
            const ud = window.userData || {};
            const result = [];
            for (const addr in ud) {
                const u = ud[addr];
                result.push({addr: addr.substring(0, 8), status: u.status});
            }
            return result;
        }
    """)
    print("\n=== userData 直接查（all filter）===")
    for u in user_data_dump:
        print(f"  {u}")

    # 8. 现在切到 retail_unknown 看哪些被过滤
    page.select_option('#userStatusFilter', 'retail_unknown')
    time.sleep(1)
    retail_unknown_rows = page.evaluate("""
        () => {
            const rows = document.querySelectorAll('#userBody tr');
            return Array.from(rows).map(r => ({
                addr: r.cells[0]?.getAttribute('data-addr')?.substring(0, 8),
                status: r.querySelector('td:nth-child(2) .badge')?.textContent,
            }));
        }
    """)
    print("\n=== retail_unknown 显示 ===")
    for r in retail_unknown_rows:
        print(f"  {r}")

    print("\n=== console 日志 ===")
    for log in logs[-10:]:
        print(log[:200])

    browser.close()