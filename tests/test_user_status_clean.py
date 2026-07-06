"""测试 saveUserStatus modal: retail → dealer → retail"""
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

    # 步骤 0: 确认初始状态
    print("=== 初始状态 ===")
    s = state()
    print(s)
    initial_status = s['cmj']['status'] if s['cmj'] else None
    initial_bet = float(s['bet'])

    # 步骤 1: 切换到 dealer（如果是 retail）
    print("\n=== 测试 1: 改 status 为 dealer ===")
    page.evaluate(f"""
        () => {{
            editUserStatus('CMjRBjRW2P3mDkZr4HAK9RmEtTVmwj5EAsev1wkogH9g', '{initial_status}');
        }}
    """)
    time.sleep(2)
    page.evaluate("""
        () => {
            document.getElementById('userStatusSelect').value = 'dealer';
            document.getElementById('userStatusSystemCheck').checked = false;
            saveUserStatus();
        }
    """)
    time.sleep(3)
    s_dealer = state()
    print(s_dealer)
    dealer_bet = float(s_dealer['bet'])

    # 步骤 2: 切回 retail
    print("\n=== 测试 2: 改回 retail ===")
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
    s_retail = state()
    print(s_retail)
    retail_bet = float(s_retail['bet'])

    # 总结
    print("\n=== 总结 ===")
    print(f"初始: status={initial_status}, bet={initial_bet}")
    print(f"dealer 后: bet={dealer_bet}, diff={dealer_bet - initial_bet:.6f}")
    print(f"retail 后: bet={retail_bet}, diff={retail_bet - dealer_bet:.6f}")
    print(f"往返误差: {abs(retail_bet - initial_bet):.10f}")

    if initial_status == '零售' or initial_status == '散户':
        # 应该是 dealer 后 bet 减少，retail 后恢复
        if dealer_bet < initial_bet and abs(retail_bet - initial_bet) < 0.0001:
            print("✅ PASS: 指数正确调整")
        else:
            print(f"❌ FAIL: 期望 dealer 后减少，实际 {dealer_bet - initial_bet:+.6f}")
    else:
        print(f"⚠️ 初始状态不是 retail ({initial_status})，无法验证")

    print("\n=== console 日志 ===")
    for log in logs[-15:]:
        print(log[:200])

    browser.close()