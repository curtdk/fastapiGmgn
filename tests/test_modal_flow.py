"""测试 簇组详情 modal 修改类型后，前端是否正确刷新"""
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

    # 用 all 过滤器
    page.select_option('#userStatusFilter', 'all')
    time.sleep(1)

    def get_state():
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
                    metricBet: document.getElementById('metricBet')?.textContent,
                    metricProfit: document.getElementById('metricProfit')?.textContent,
                };
            }
        """)

    print("=== 修改前 ===")
    print(get_state())

    # 模拟 簇组详情 modal 流程：用户点击 cluster 名称链接
    print("\n=== 点击 cluster 名称打开簇组详情 modal ===")
    # 找 CMjRBjRW 行的 cluster_name 链接
    cmj_cluster_link = page.locator('tr:has(td[data-addr^="CMjRBjRW"]) a').first
    cmj_cluster_link.click()
    time.sleep(3)

    # 现在 modal 应该打开了
    modal_visible = page.evaluate("""
        () => {
            const modal = document.getElementById('clusterDetailModal');
            return {
                exists: !!modal,
                classList: modal?.className,
                currentSelectValue: document.getElementById('cdmClusterTypeSelect')?.value,
            };
        }
    """)
    print(f"Modal 状态: {modal_visible}")

    # 改类型为 dealer
    page.select_option('#cdmClusterTypeSelect', 'dealer')
    time.sleep(0.5)

    # 点保存按钮
    save_btn = page.locator('#clusterDetailModal button:has-text("保存")').first
    if save_btn.count() == 0:
        save_btn = page.locator('#clusterDetailModal button:has-text("更新")').first
    print(f"找到保存按钮: count={save_btn.count()}")
    # 处理 alert
    page.once("dialog", lambda dialog: dialog.accept())
    save_btn.click()
    time.sleep(4)

    print("\n=== 修改后 ===")
    print(get_state())

    # 改回 retail
    cmj_cluster_link.click()
    time.sleep(2)
    page.select_option('#cdmClusterTypeSelect', 'retail')
    time.sleep(0.5)
    page.once("dialog", lambda dialog: dialog.accept())
    save_btn = page.locator('#clusterDetailModal button:has-text("保存")').first
    if save_btn.count() == 0:
        save_btn = page.locator('#clusterDetailModal button:has-text("更新")').first
    save_btn.click()
    time.sleep(4)

    print("\n=== 再改回 retail 后 ===")
    print(get_state())

    print("\n=== console 日志 ===")
    for log in logs[-15:]:
        print(log[:200])

    browser.close()