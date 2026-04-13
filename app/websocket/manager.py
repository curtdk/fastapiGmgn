"""WebSocket 连接管理器"""
import asyncio
import json
import logging
from datetime import datetime
from typing import Dict, Set, Any
from fastapi import WebSocket

logger = logging.getLogger(__name__)


def _serialize(obj: Any) -> Any:
    """递归将 datetime 等对象转为 JSON 可序列化格式"""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_serialize(i) for i in obj]
    return obj


class WebSocketManager:
    """管理所有 WebSocket 连接"""

    def __init__(self):
        # mint -> set of websocket connections
        self.active_connections: Dict[str, Set[WebSocket]] = {}
        # websocket -> mint
        self.connection_mint: Dict[WebSocket, str] = {}

    async def connect(self, websocket: WebSocket, mint: str):
        """建立连接"""
        await websocket.accept()
        if mint not in self.active_connections:
            self.active_connections[mint] = set()
        self.active_connections[mint].add(websocket)
        self.connection_mint[websocket] = mint
        logger.info(f"[WS] 新连接: mint={mint}, 当前连接数={len(self.active_connections[mint])}")

    async def disconnect(self, websocket: WebSocket):
        """断开连接"""
        mint = self.connection_mint.pop(websocket, None)
        if mint and mint in self.active_connections:
            self.active_connections[mint].discard(websocket)
            if not self.active_connections[mint]:
                del self.active_connections[mint]
            logger.info(f"[WS] 断开连接: mint={mint}")

    async def send_personal(self, websocket: WebSocket, message: dict):
        """发送消息给单个连接"""
        try:
            await websocket.send_text(json.dumps(_serialize(message)))
        except Exception as e:
            logger.warning(f"[WS] 发送失败: {e}")

    async def broadcast(self, mint: str, message: dict):
        """广播消息给某个 mint 的所有连接"""
        if mint not in self.active_connections:
            return
        disconnected = set()
        for ws in self.active_connections[mint]:
            try:
                await ws.send_text(json.dumps(_serialize(message)))
            except Exception as e:
                logger.warning(f"[WS] 广播失败: {e}")
                disconnected.add(ws)
        # 清理失效连接
        for ws in disconnected:
            await self.disconnect(ws)

    def get_connection_count(self, mint: str) -> int:
        """获取某 mint 的连接数"""
        return len(self.active_connections.get(mint, set()))


# 全局单例
ws_manager = WebSocketManager()
