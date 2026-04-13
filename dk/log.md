(venv) curtdk@curtdkdeMac-mini fastapiGmgn % uvicorn main:app --host 0.0.0.0 --port 8000 --reload                   

INFO:     Will watch for changes in these directories: ['/Users/curtdk/.openclaw/workspace/fastapiGmgn']
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
INFO:     Started reloader process [56305] using WatchFiles
2026-04-13 19:27:49,128 - main - INFO - Admin user already exists
2026-04-13 19:27:49,131 - app.services.settings_service - INFO - 默认设置初始化完成
INFO:     Started server process [56307]
INFO:     Waiting for application startup.
2026-04-13 19:27:49,133 - app.services.settings_service - INFO - 默认设置初始化完成
INFO:     Application startup complete.
INFO:     127.0.0.1:65377 - "GET /admin/trade-monitor HTTP/1.1" 200 OK
INFO:     127.0.0.1:65378 - "POST /admin/api/start HTTP/1.1" 200 OK
INFO:     ('127.0.0.1', 65417) - "WebSocket /ws/trades/DQXTFfmqnHmewRXkeAnwXMtNZxVBocbNKPababEPpump" [accepted]
2026-04-13 19:28:24,818 - app.websocket.manager - INFO - [WS] 新连接: mint=DQXTFfmqnHmewRXkeAnwXMtNZxVBocbNKPababEPpump, 当前连接数=1
INFO:     connection open
INFO:     127.0.0.1:65378 - "GET /admin/api/trades?mint=DQXTFfmqnHmewRXkeAnwXMtNZxVBocbNKPababEPpump&page=1&page_size=50 HTTP/1.1" 200 OK
2026-04-13 19:28:25,362 - httpx - INFO - HTTP Request: POST https://mainnet.helius-rpc.com/?api-key=f43f1a35-863c-4c55-9a13-d00092f0ff2d "HTTP/1.1 200 OK"
2026-04-13 19:28:25,364 - app.services.trade_backfill - INFO - [回填] 起始 slot: 412934995
2026-04-13 19:28:26,002 - httpx - INFO - HTTP Request: POST https://mainnet.helius-rpc.com/?api-key=f43f1a35-863c-4c55-9a13-d00092f0ff2d "HTTP/1.1 200 OK"
2026-04-13 19:28:26,005 - app.services.trade_backfill - INFO - [回填] 共获取 17 条签名
2026-04-13 19:28:26,589 - httpx - INFO - HTTP Request: POST https://mainnet.helius-rpc.com/?api-key=f43f1a35-863c-4c55-9a13-d00092f0ff2d "HTTP/1.1 200 OK"
2026-04-13 19:28:26,612 - httpx - INFO - HTTP Request: POST https://mainnet.helius-rpc.com/?api-key=f43f1a35-863c-4c55-9a13-d00092f0ff2d "HTTP/1.1 200 OK"
2026-04-13 19:28:26,639 - httpx - INFO - HTTP Request: POST https://mainnet.helius-rpc.com/?api-key=f43f1a35-863c-4c55-9a13-d00092f0ff2d "HTTP/1.1 200 OK"
2026-04-13 19:28:27,130 - httpx - INFO - HTTP Request: POST https://mainnet.helius-rpc.com/?api-key=f43f1a35-863c-4c55-9a13-d00092f0ff2d "HTTP/1.1 200 OK"
2026-04-13 19:28:27,143 - httpx - INFO - HTTP Request: POST https://mainnet.helius-rpc.com/?api-key=f43f1a35-863c-4c55-9a13-d00092f0ff2d "HTTP/1.1 200 OK"
2026-04-13 19:28:27,271 - httpx - INFO - HTTP Request: POST https://mainnet.helius-rpc.com/?api-key=f43f1a35-863c-4c55-9a13-d00092f0ff2d "HTTP/1.1 200 OK"
2026-04-13 19:28:27,778 - httpx - INFO - HTTP Request: POST https://mainnet.helius-rpc.com/?api-key=f43f1a35-863c-4c55-9a13-d00092f0ff2d "HTTP/1.1 200 OK"
2026-04-13 19:28:27,798 - httpx - INFO - HTTP Request: POST https://mainnet.helius-rpc.com/?api-key=f43f1a35-863c-4c55-9a13-d00092f0ff2d "HTTP/1.1 200 OK"
2026-04-13 19:28:27,936 - httpx - INFO - HTTP Request: POST https://mainnet.helius-rpc.com/?api-key=f43f1a35-863c-4c55-9a13-d00092f0ff2d "HTTP/1.1 200 OK"
2026-04-13 19:28:28,424 - httpx - INFO - HTTP Request: POST https://mainnet.helius-rpc.com/?api-key=f43f1a35-863c-4c55-9a13-d00092f0ff2d "HTTP/1.1 200 OK"
2026-04-13 19:28:28,461 - httpx - INFO - HTTP Request: POST https://mainnet.helius-rpc.com/?api-key=f43f1a35-863c-4c55-9a13-d00092f0ff2d "HTTP/1.1 200 OK"
2026-04-13 19:28:28,607 - httpx - INFO - HTTP Request: POST https://mainnet.helius-rpc.com/?api-key=f43f1a35-863c-4c55-9a13-d00092f0ff2d "HTTP/1.1 200 OK"
2026-04-13 19:28:29,156 - httpx - INFO - HTTP Request: POST https://mainnet.helius-rpc.com/?api-key=f43f1a35-863c-4c55-9a13-d00092f0ff2d "HTTP/1.1 200 OK"
2026-04-13 19:28:29,157 - httpx - INFO - HTTP Request: POST https://mainnet.helius-rpc.com/?api-key=f43f1a35-863c-4c55-9a13-d00092f0ff2d "HTTP/1.1 200 OK"
2026-04-13 19:28:29,346 - httpx - INFO - HTTP Request: POST https://mainnet.helius-rpc.com/?api-key=f43f1a35-863c-4c55-9a13-d00092f0ff2d "HTTP/1.1 200 OK"
2026-04-13 19:28:29,759 - httpx - INFO - HTTP Request: POST https://mainnet.helius-rpc.com/?api-key=f43f1a35-863c-4c55-9a13-d00092f0ff2d "HTTP/1.1 200 OK"
2026-04-13 19:28:29,830 - httpx - INFO - HTTP Request: POST https://mainnet.helius-rpc.com/?api-key=f43f1a35-863c-4c55-9a13-d00092f0ff2d "HTTP/1.1 200 OK"
2026-04-13 19:28:29,862 - app.services.trade_backfill - INFO - [回填] 批量入库 13 条
2026-04-13 19:28:29,865 - app.websocket.manager - WARNING - [WS] 广播失败: Object of type datetime is not JSON serializable
2026-04-13 19:28:29,866 - app.websocket.manager - INFO - [WS] 断开连接: mint=DQXTFfmqnHmewRXkeAnwXMtNZxVBocbNKPababEPpump
2026-04-13 19:28:30,566 - httpx - INFO - HTTP Request: POST https://mainnet.helius-rpc.com/?api-key=f43f1a35-863c-4c55-9a13-d00092f0ff2d "HTTP/1.1 200 OK"
2026-04-13 19:28:30,830 - app.services.trade_stream - INFO - [实时流] 启动: DQXTFfmqnHmewRXkeAnwXMtNZxVBocbNKPababEPpump
2026-04-13 19:28:31,467 - app.services.trade_stream - INFO - [实时流] 已发送订阅请求: DQXTFfmqnHmewRXkeAnwXMtNZxVBocbNKPababEPpump
2026-04-13 19:28:31,550 - app.services.trade_stream - INFO - [实时流] 订阅确认: {"jsonrpc":"2.0","id":1,"result":6578243}
2026-04-13 19:28:59,558 - app.services.trade_backfill - INFO - [回填] 收到停止信号
2026-04-13 19:28:59,652 - app.services.trade_stream - WARNING - [实时流] 连接断开，尝试重连...
2026-04-13 19:28:59,653 - app.services.trade_stream - INFO - [实时流] 停止: DQXTFfmqnHmewRXkeAnwXMtNZxVBocbNKPababEPpump