"""快速验证 playwright 是否能启动"""
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    try:
        browser = p.chromium.launch(headless=True)
        print("Browser launched OK")
        browser.close()
    except Exception as e:
        print(f"Error: {e}")