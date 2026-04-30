#!/bin/bash

# 停止旧服务
echo "Stopping old service..."
lsof -ti :8000 | xargs kill -9 2>/dev/null

# 等待端口释放
sleep 2

