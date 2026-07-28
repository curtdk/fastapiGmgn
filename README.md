# FastapiGmgn 项目

## 项目位置
```
/Users/curtdk/.openclaw/workspace/fastapiGmgn
```

## 说明
这是 ding 的一套代码项目。

## 创建时间
2026-06-01

uvicorn main:app --host 0.0.0.0 --port 2000 > /tmp/server.log 2>&1


curl -s -X POST 'http://127.0.0.1:8000/admin/api/start' -d 'mint=HpZfpQEMwSuuN9zUJrMMr7QfuZ9tEjQGM6hBdNKqpump'



mkdir -p logs && uvicorn main:app --host 0.0.0.0 --port 2000 > logs/server.log 2>&1

kill -9 $(lsof -t -i:2000)

ps aux | grep uvicorn


pkill -f "uvicorn main:app --host 0.0.0.0 --port 2000"



source venv/bin/activate && nohup uvicorn main:app --host 0.0.0.0 --port 2000 > logs/nohup.out 2>&1 &
disown
echo "启动中... PID=$!"
sleep 3
echo "--- 进程 ---"
ps -ef | grep "uvicorn.*2000" | grep -v grep
echo "--- 端口 ---"
ss -tlnp 2>/dev/null | grep :2000
echo "--- 直连测试 ---"
curl -sS -o /dev/null -w "本机 :2000  HTTP=%{http_code}  time=%{time_total}s\n" --max-time 5 http://127.0.0.1:2000/

kill -9 $(lsof -t -i:2000)

sudo systemctl stop fastapiGmgn.service
sudo systemctl start fastapiGmgn.service
sudo systemctl status fastapiGmgn.service


sudo systemctl cat fastapiGmgn.service

