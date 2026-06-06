"""
策略消费者 - 从消息队列获取交易数据，交给策略处理

使用方式：
    from app.taskcl.consumer import start_strategy_consumer
    
    # 启动消费者
    await start_strategy_consumer()
"""
import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# 模块级变量
_strategy_queue: Optional[asyncio.Queue] = None
_consumer_task: Optional[asyncio.Task] = None
_strategy_params: dict = {}


def get_strategy_queue() -> asyncio.Queue:
    """获取策略消息队列"""
    global _strategy_queue
    if _strategy_queue is None:
        _strategy_queue = asyncio.Queue()
    return _strategy_queue


def get_strategy_params() -> dict:
    """获取策略参数缓存（trade_processor 用）"""
    global _strategy_params
    return _strategy_params


def set_strategy_params(params: dict):
    """设置策略参数缓存（策略启动/关闭时调用）"""
    global _strategy_params
    _strategy_params = params or {}
    logger.info(f"[策略缓存] 参数已更新: {_strategy_params}")


async def enqueue_trade_for_strategy(tx_detail: dict):
    """
    数据进入策略队列（由 trade_processor 按需调用，受 ifNeedDealer 控制）
    """
    queue = get_strategy_queue()
    await queue.put(tx_detail)
    logger.debug(f"[策略队列] 入队 sig={tx_detail.get('sig', '')[:8]}...")


async def start_strategy_consumer():
    """启动策略消费者 Task"""
    global _consumer_task
    
    if _consumer_task is not None and not _consumer_task.done():
        logger.warning("[策略消费者] 消费者已在运行中")
        return
    
    _consumer_task = asyncio.create_task(_consumer_loop())
    logger.info("[策略消费者] 已启动")


async def stop_strategy_consumer():
    """停止策略消费者 Task"""
    global _consumer_task
    
    if _consumer_task is None:
        return
    
    # 发送毒丸信号
    queue = get_strategy_queue()
    await queue.put(None)
    
    try:
        await asyncio.wait_for(_consumer_task, timeout=3.0)
    except asyncio.TimeoutError:
        _consumer_task.cancel()
        try:
            await _consumer_task
        except asyncio.CancelledError:
            pass
    
    _consumer_task = None
    logger.info("[策略消费者] 已停止")


async def _consumer_loop():
    """消费者循环 - 持续从队列获取交易数据"""
    from app.taskcl.manager import get_strategy_manager
    
    logger.info("[策略消费者] 开始消费")
    
    while True:
        try:
            queue = get_strategy_queue()
            
            # 从队列获取数据（阻塞等待）
            tx_detail = await queue.get()
            
            if tx_detail is None:  # 毒丸信号，结束
                logger.info("[策略消费者] 收到停止信号，退出")
                break
            
            sig = tx_detail.get("sig", "")
            if not sig:
                continue
            
            try:
                # 获取策略管理器
                manager = get_strategy_manager()
                
                # 检查是否启用
                if not manager.is_enabled():
                    logger.debug(f"[策略消费者] 策略未启用，跳过 sig={sig[:8]}...")
                    continue
                
                # 获取当前策略
                strategy = manager.get_current_strategy()
                if strategy is None:
                    logger.debug(f"[策略消费者] 无选中策略，跳过 sig={sig[:8]}...")
                    continue
                
                # 调用策略的 on_trade
                from app.services.trade_tracer import trace
                trace(strategy.mint, sig, "⑦ 策略处理", f"策略={strategy.name}, state={strategy.get_current_state()}")
                await strategy.on_trade(tx_detail)
                
                logger.debug(f"[策略消费者] 处理完成 sig={sig[:8]}...")
                
            except Exception as e:
                logger.error(f"[策略消费者] 处理异常 sig={sig[:8]}...: {e}", exc_info=True)
                
        except asyncio.CancelledError:
            logger.info("[策略消费者] 被取消，退出")
            break
        except Exception as e:
            logger.error(f"[策略消费者] 循环异常: {e}", exc_info=True)
            await asyncio.sleep(1)


async def clear_strategy_queue():
    """清空策略队列"""
    global _strategy_queue
    
    if _strategy_queue is None:
        return
    
    cleared = 0
    while not _strategy_queue.empty():
        try:
            _strategy_queue.get_nowait()
            cleared += 1
        except asyncio.QueueEmpty:
            break
    
    _strategy_queue = asyncio.Queue()
    logger.info(f"[策略消费者] 队列已清空，丢弃 {cleared} 条消息")