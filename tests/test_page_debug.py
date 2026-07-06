"""完整测试：登录后操作"""
from playwright.sync_api import sync_playwright
import time

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context()
    page = context.new_page()

    page.goto("http://localhost:8000/admin/login")
    page.wait_for_load_state("networkidle")
    print("URL:", page.url)
    print("Title:", page.title())

    # 看页面内容
    html = page.content()[:2000]
    print("HTML[:2000]:", html)

    browser.close()