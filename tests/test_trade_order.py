"""测试 trade 列表顺序：最新应该在最上面"""
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
    time.sleep(10)

    # 看 tradeBody 前几行的顺序（time 字段）
    def get_top_trades(n=5):
        return page.evaluate(f"""
            () => {{
                const rows = document.querySelectorAll('#tradeBody tr');
                const result = [];
                for (let i = 0; i < Math.min({n}, rows.length); i++) {{
                    const row = rows[i];
                    const cells = row.cells;
                    // 时间一般是某一列，找包含时间的
                    const all = Array.from(cells).map(c => c.textContent.trim().substring(0, 30));
                    result.push(all);
                }}
                return result;
            }}
        """)

    print("=== tradeBody 前 5 行（最新的应该在前）===")
    rows = get_top_trades(5)
    for i, r in enumerate(rows):
        print(f"  第 {i+1} 行: {r}")

    browser.close()