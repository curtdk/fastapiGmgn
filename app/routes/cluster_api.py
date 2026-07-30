"""簇组管理 API 路由"""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
import json
import logging
logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["簇组管理"])


@router.get("/api/clusters/summary")
async def api_get_clusters_summary(mint: str = ""):
    """获取当前 mint 的簇组摘要（按庄家/散户/未定义分组，user_count 和 holding_qty 由扫描 user:{mint}:* 动态计算）"""
    from app.services.cluster.manager import create_manager
    from app.utils.database import SessionLocal
    from app.services.cluster.redis_keys import _get_sync_redis
    
    db = SessionLocal()
    try:
        manager = create_manager(db)
        clusters = await manager.get_all_clusters()
        r = _get_sync_redis()
        
        # per-mint: 扫描 user:{mint}:* 统计每个簇组的活跃用户数 + 持仓量
        cluster_user_counts = {}
        cluster_holding_qty = {}
        if mint:
            cursor = 0
            while True:
                cursor, keys = r.scan(cursor=cursor, match=f"user:{mint}:*", count=100)
                for key in keys:
                    addr = key.replace(f"user:{mint}:", "")
                    gdata = r.hgetall(f"user:{addr}")
                    if gdata:
                        cn = gdata.get("cluster_name", "")
                        if cn:
                            cluster_user_counts[cn] = cluster_user_counts.get(cn, 0) + 1
                            mdata = r.hgetall(key)
                            holding = float((mdata or {}).get("holdingQty", "0"))
                            cluster_holding_qty[cn] = cluster_holding_qty.get(cn, 0) + holding
                if cursor == 0:
                    break
        
        # 读取全局总持仓量（C008 分母）
        total_holding_qty = 0.0
        if mint:
            total_holding_qty = float(r.hget(f"metrics:{mint}", "total_holdingQty") or "0")
        
        groups = {"dealer": [], "retail": [], "unknown": []}
        for c in clusters:
            ct = c.cluster_type
            if ct not in groups:
                ct = "unknown"

            active_count = cluster_user_counts.get(c.name, 0)

            groups[ct].append({
                "name": c.name,
                "user_count": active_count,
                "tx_count": 0,
                "holding_qty": cluster_holding_qty.get(c.name, 0.0),
                "cluster_type": c.cluster_type,
                "judgment_type": c.judgment_type,
            })

        result = {}
        for key in ("dealer", "retail", "unknown"):
            items = groups[key]
            total_users = sum(item["user_count"] for item in items)
            items.sort(key=lambda x: x["user_count"], reverse=True)
            result[key] = {
                "count": len(items),
                "total_users": total_users,
                "clusters": items,
            }
        result["total_holding_qty"] = total_holding_qty
        
        return JSONResponse(result)
    finally:
        db.close()


@router.get("/api/clusters")
async def api_get_clusters(
    request: Request,
    search: str = "",
    sort_by: str = "created_at",
    sort_order: str = "desc",
    page: int = 1,
    page_size: int = 20,
):
    """获取所有簇组（支持搜索、排序、分页）"""
    from app.services.cluster.manager import create_manager
    from app.utils.database import SessionLocal
    
    # 参数校验
    page = max(1, page)
    page_size = max(1, min(100, page_size))
    sort_order = "asc" if sort_order == "asc" else "desc"
    
    # 允许排序的字段
    allowed_sort_fields = {
        "user_count", "created_at"
    }
    if sort_by not in allowed_sort_fields:
        sort_by = "created_at"

    db = SessionLocal()
    try:
        manager = create_manager(db)
        clusters = await manager.get_all_clusters()

        # 搜索过滤（匹配簇组名称）
        if search:
            search_lower = search.lower()
            clusters = [c for c in clusters if search_lower in c.name.lower()]

        # 排序
        reverse = sort_order == "desc"
        def sort_key(c):
            if sort_by == "user_count":
                return c.get_user_count()
            else:  # created_at
                return c.created_at
        
        clusters = sorted(clusters, key=sort_key, reverse=reverse)
        
        # 分页
        total = len(clusters)
        total_pages = (total + page_size - 1) // page_size if total > 0 else 1
        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size
        paginated = clusters[start_idx:end_idx]
        
        return JSONResponse({
            "clusters": [c.to_dict() for c in paginated],
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            # 统计数据
            "stats": {
                "total_clusters": len(clusters),
                "dealer_clusters": sum(1 for c in clusters if c.cluster_type == "dealer"),
                "retail_clusters": sum(1 for c in clusters if c.cluster_type == "retail"),
                "total_users": sum(c.get_user_count() for c in clusters),
            }
        })
    finally:
        db.close()


@router.delete("/api/clusters/{name}")
async def api_delete_cluster(name: str):
    """删除簇组"""
    from app.services.cluster.manager import create_manager
    from app.utils.database import SessionLocal
    from urllib.parse import unquote
    name = unquote(name)
    db = SessionLocal()
    try:
        manager = create_manager(db)
        await manager.delete_cluster(name)
        return JSONResponse({"message": "已删除"})
    finally:
        db.close()


@router.put("/api/clusters/{name}/enabled")
async def api_update_cluster_enabled(name: str, request: Request):
    """启用/禁用簇组"""
    from app.services.cluster.manager import create_manager
    from app.utils.database import SessionLocal
    from urllib.parse import unquote
    name = unquote(name)
    db = SessionLocal()
    try:
        body = await request.json()
        enabled = body.get("enabled", True)
        manager = create_manager(db)
        await manager.set_cluster_enabled(name, enabled)
        return JSONResponse({"message": "已更新"})
    finally:
        db.close()


@router.get("/api/clusters/{name}/users-detail")
async def api_get_cluster_users_detail(name: str, mint: str = ""):
    """获取簇组用户详情（按状态分组），通过扫描 user:{mint}:* 过滤 cluster_name"""
    from app.services.cluster.redis_keys import get_cluster
    from app.services.trade_processor import _get_redis
    from urllib.parse import unquote
    
    name = unquote(name)
    cluster = await get_cluster(name)
    if not cluster:
        return JSONResponse({"error": "簇组不存在"}, status_code=404)
    
    redis = await _get_redis()
    
    dealer_users = []
    retail_users = []
    unknown_users = []
    
    if redis:
        # 扫描 per-mint 用户，按 cluster_name 过滤
        if mint:
            cursor = 0
            while True:
                cursor, keys = await redis.scan(cursor=cursor, match=f"user:{mint}:*", count=100)
                for key in keys:
                    key_str = key.decode() if isinstance(key, bytes) else key
                    addr = key_str.replace(f"user:{mint}:", "")
                    
                    gdata = await redis.hgetall(f"user:{addr}")
                    if not gdata:
                        continue
                    decoded = {}
                    for k, v in gdata.items():
                        k_str = k.decode() if isinstance(k, bytes) else k
                        v_str = v.decode() if isinstance(v, bytes) else v
                        decoded[k_str] = v_str
                    
                    if decoded.get("cluster_name") != name:
                        continue
                    
                    status = decoded.get("status", "unknown")
                    conditions = []
                    try:
                        conditions = json.loads(decoded.get("conditions", "[]"))
                    except:
                        pass
                    
                    user_info = {
                        "address": addr,
                        "status": status,
                        "status_source": decoded.get("status_source", "system"),
                        "conditions": conditions,
                    }
                    
                    if status == "dealer":
                        dealer_users.append(user_info)
                    elif status == "retail":
                        retail_users.append(user_info)
                    else:
                        unknown_users.append(user_info)
                if cursor == 0:
                    break
    
    return JSONResponse({
        "cluster_name": cluster.name,
        "cluster_type": cluster.cluster_type,
        "judgment_type": cluster.judgment_type,
        "tx_count": 0,
        "user_count": len(dealer_users) + len(retail_users) + len(unknown_users),
        "dealer_users": dealer_users,
        "dealer_count": len(dealer_users),
        "retail_users": retail_users,
        "retail_count": len(retail_users),
        "unknown_users": unknown_users,
        "unknown_count": len(unknown_users),
    })


@router.put("/api/users/{address}/status")
async def api_set_user_status(address: str, request: Request):
    """手动修改用户状态（标记为 manual，不受簇组自动覆盖）

    如果传 mint，会调 exclude_dealer / include_retail 调整指标并广播 WS。
    """
    from app.services.trade_processor import (
        save_trader_state, user_key, exclude_dealer, include_retail,
        _get_redis as _tp_get_redis, _get_metrics_key,
    )
    from app.services.cluster.redis_keys import _get_redis, user_mint_key
    from app.websocket.manager import ws_manager
    from urllib.parse import unquote
    import json

    address = unquote(address)
    body = await request.json()
    new_status = body.get("status", "")
    new_status_source = body.get("status_source", "manual")  # 默认手动锁定
    mint = body.get("mint", "") or ""

    if new_status not in ("dealer", "retail", "unknown"):
        return JSONResponse({"error": "无效状态，可选: dealer/retail/unknown"}, status_code=400)

    redis = await _get_redis()
    key = user_key(address)
    state = await redis.hgetall(key) or {}
    old_status = state.get("status", "unknown")

    state["status"] = new_status
    state["status_source"] = new_status_source
    state["conditions"] = state.get("conditions", "[]")

    await redis.hset(key, mapping={
        "status": new_status,
        "status_source": new_status_source,
        "conditions": state.get("conditions", "[]"),
    })

    # 如果传了 mint，根据状态切换调 exclude_dealer / include_retail 调整指标
    if mint:
        try:
            if new_status == "dealer" and old_status != "dealer":
                # retail/unknown → dealer：排除
                await exclude_dealer(mint, address)
            elif old_status == "dealer" and new_status != "dealer":
                # dealer → retail/unknown：恢复
                await include_retail(mint, address)
        except Exception as e:
            logger.error(f"[用户状态] 调整指标失败: {e}", exc_info=True)

        # 发 WS user_status 推送（带 metrics）让前端顶部指数同步刷新
        try:
            redis_async = await _tp_get_redis()
            metrics_data = await redis_async.hgetall(await _get_metrics_key(mint)) if redis_async else {}
            current_bet = float(metrics_data.get("total_bet", "0") or 0)
            realized_profit = float(metrics_data.get("realized_profit", "0") or 0)
            await ws_manager.broadcast(mint, {
                "type": "user_status",
                "data": {
                    "address": address,
                    "status": new_status,
                    "status_source": new_status_source,
                    "conditions": json.loads(state.get("conditions", "[]")) if state.get("conditions") else [],
                    "cluster_name": state.get("cluster_name", ""),
                    "cluster_type": state.get("cluster_type", "unknown"),
                    "cluster_tx_count": 0,
                    "cluster_user_count": 0,
                },
                "metrics": {
                    "current_bet": current_bet,
                    "realized_profit": realized_profit,
                    "current_cost": current_bet - realized_profit,
                    "trade_count": int(float(metrics_data.get("trade_count", "0") or 0)),
                }
            })
        except Exception as e:
            logger.warning(f"[用户状态] WS 广播失败（可忽略）: {e}")

    logger.info(f"[用户状态] {address[:8]}... 修改为 {new_status} (source={new_status_source})")
    return JSONResponse({"message": "已更新", "address": address, "status": new_status, "status_source": new_status_source})


@router.get("/api/clusters/{name}")
async def api_get_cluster_detail(name: str):
    """获取单个簇组完整详情"""
    from app.services.cluster.redis_keys import get_cluster
    from urllib.parse import unquote
    name = unquote(name)
    cluster = await get_cluster(name)
    if not cluster:
        return JSONResponse({"error": "簇组不存在"}, status_code=404)
    return JSONResponse(cluster.to_dict())


@router.put("/api/clusters/{name}/folder")
async def api_update_cluster_folder(name: str, request: Request):
    """修改簇组文件夹"""
    from app.services.cluster.manager import create_manager
    from app.utils.database import SessionLocal
    from urllib.parse import unquote
    name = unquote(name)
    db = SessionLocal()
    try:
        body = await request.json()
        folder = body.get("folder", "")
        manager = create_manager(db)
        await manager.set_cluster_folder(name, folder)
        return JSONResponse({"message": "已更新"})
    finally:
        db.close()


@router.get("/api/clusters/{name}/txs")
async def api_get_cluster_txs(name: str):
    """获取簇组内的所有Tx列表"""
    from app.services.cluster.redis_keys import get_cluster
    from urllib.parse import unquote
    name = unquote(name)
    cluster = await get_cluster(name)
    if not cluster:
        return JSONResponse({"error": "簇组不存在"}, status_code=404)
    return JSONResponse({
        "txs": getattr(cluster, 'txs', []),
        "user_count": cluster.get_user_count(),
    })


@router.put("/api/clusters/{name}/type")
async def api_update_cluster_type(name: str, request: Request):
    """修改簇组类型（手动锁定）"""
    from app.services.cluster.manager import create_manager
    from app.services.cluster.redis_keys import get_cluster
    from app.utils.database import SessionLocal
    from urllib.parse import unquote
    name = unquote(name)
    db = SessionLocal()
    try:
        body = await request.json()
        new_cluster_type = body.get("cluster_type", "unknown")
        # 只允许 dealer/retail/unknown，拒绝 undefined 等无效值
        if new_cluster_type not in ("dealer", "retail", "unknown"):
            return JSONResponse({"error": "无效簇组类型，可选: dealer/retail/unknown"}, status_code=400)
        judgment_type = body.get("judgment_type", "manual")
        mint = body.get("mint", "")  # 需要传入 mint 才能调用 include_retail
        
        # 获取旧簇组信息
        old_cluster = await get_cluster(name)
        old_type = old_cluster.cluster_type if old_cluster else None
        
        manager = create_manager(db)
        await manager.set_cluster_type(name, new_cluster_type, judgment_type)

        # 传播簇类型到所有 system 判定的用户
        if mint:
            await propagate_cluster_type_to_users(
                mint=mint,
                cluster_name=name,
                old_type=old_type or "unknown",
                new_type=new_cluster_type,
                ws_broadcast=True,
            )

        return JSONResponse({"message": "已更新"})
    finally:
        db.close()


async def propagate_cluster_type_to_users(
    mint: str,
    cluster_name: str,
    old_type: str,
    new_type: str,
    ws_broadcast: bool = True,
) -> int:
    """遍历某簇组下所有 system 判定的用户，根据 new_type 调整状态。

    - old_type=dealer, new_type=dealer：跳过
    - new_type=dealer：调 exclude_dealer 把 dealer_excluded 标记移除（恢复 dealer 状态）
    - old_type=dealer, new_type≠dealer：调 include_retail 标记为 retail
    - 其他情况不调整状态

    返回：处理的用户数。
    """
    from app.services.cluster.redis_keys import _get_sync_redis
    from app.services.trade_processor import include_retail, exclude_dealer
    from app.websocket.manager import ws_manager

    sync_redis = _get_sync_redis()
    processed = 0

    try:
        # SCAN 所有 user:* key，找出 cluster_name == target 的
        for key in sync_redis.scan_iter(match="user:*", count=500):
            data = sync_redis.hgetall(key)
            if data.get("cluster_name") != cluster_name:
                continue
            # 跳过 per-mint key（用 : 分隔的 key 中，segment > 2 的不是 global user key）
            if key.count(":") > 1:
                continue
            # 提取 address
            address = key.split(":", 1)[1] if ":" in key else key.replace("user:", "")

            try:
                # 簇组类型传播：不管原 status_source 是 system 还是 manual
                # 都强制跟随 cluster（簇组是 source of truth）
                # 因为 exclude_dealer / include_retail 内部会重新写 status + status_source

                # 新簇类型是 dealer → 排除 dealer_excluded 标记
                if new_type == "dealer":
                    await exclude_dealer(mint, address)
                    logger.info(f"[簇类型传播] {address[:8]}... → dealer")

                # 旧类型是 dealer，新类型非 dealer → 标记为 retail
                elif old_type == "dealer" and new_type != "dealer":
                    await include_retail(mint, address)
                    logger.info(f"[簇类型传播] {address[:8]}... → {new_type}")

                # 推 user_status 给前端
                if ws_broadcast:
                    from app.services.trade_processor import user_key, _get_redis as _tp_get_redis, _get_metrics_key
                    from app.services.cluster.redis_keys import user_mint_key
                    redis_async = await _tp_get_redis()
                    new_state = await redis_async.hgetall(user_key(address)) if redis_async else {}
                    mint_data = await redis_async.hgetall(user_mint_key(mint, address)) if redis_async else {}
                    if new_state:
                        # conditions 是 JSON 字符串，需反序列化为列表
                        try:
                            conditions_list = json.loads(new_state.get("conditions", "[]"))
                        except Exception:
                            conditions_list = []
                        # 读最新 metrics（让前端顶部指数立刻刷新）
                        metrics_data = await redis_async.hgetall(await _get_metrics_key(mint)) if redis_async else {}
                        try:
                            current_bet = float(metrics_data.get("total_bet", "0") or 0)
                            realized_profit = float(metrics_data.get("realized_profit", "0") or 0)
                            current_cost = current_bet - realized_profit
                            trade_count = int(float(metrics_data.get("trade_count", "0") or 0))
                        except Exception:
                            current_bet = realized_profit = current_cost = trade_count = 0
                        # 持仓字段在 per-mint key 里，要从 mint_data 取
                        await ws_manager.broadcast(mint, {
                            "type": "user_status",
                            "data": {
                                "address": address,
                                "status": new_state.get("status", "unknown"),
                                "status_source": new_state.get("status_source", "system"),
                                "conditions": conditions_list,
                                "cluster_name": cluster_name,
                                "cluster_type": new_type,
                                "cluster_tx_count": 0,
                                "cluster_user_count": 0,
                                "holding_qty": float(mint_data.get("holdingQty", "0") or 0),
                                "holding_cost": float(mint_data.get("holdingCost", "0") or 0),
                                "total_buy_amount": float(mint_data.get("totalBuyAmount", "0") or 0),
                                "total_sell_amount": float(mint_data.get("totalSellAmount", "0") or 0),
                                "total_sell_principal": float(mint_data.get("totalSellPrincipal", "0") or 0),
                            },
                            # 顶部指数同步推送（前端可立即刷新）
                            "metrics": {
                                "current_bet": current_bet,
                                "realized_profit": realized_profit,
                                "current_cost": current_cost,
                                "trade_count": trade_count,
                            }
                        })

                processed += 1
            except Exception as e:
                logger.error(f"[簇类型传播] {address[:8]}... 失败: {e}")

        logger.info(f"[簇类型传播] 簇组 {cluster_name[:8]}... ({old_type}→{new_type}) 共处理 {processed} 个用户")
        return processed
    except Exception as e:
        logger.error(f"[簇类型传播] 失败: {e}", exc_info=True)
        return 0


@router.get("/api/settings/backfill-mode")
async def api_get_backfill_mode():
    """获取当前 backfill 模式（0=正式，2=测试）

    前端页面用来显示当前模式。
    """
    from app.utils.database import SessionLocal
    from app.services.settings_service import get_setting
    db = SessionLocal()
    try:
        val = get_setting(db, "backfill_skip_ws_wait") or "0"
        try:
            mode = int(val)
        except (ValueError, TypeError):
            mode = 0
        return JSONResponse({
            "mode": mode,
            "is_test_mode": mode == 2,
            "description": "测试模式 (backfill 期间也广播)" if mode == 2 else "正式模式 (backfill 期间不广播)",
        })
    finally:
        db.close()


@router.put("/api/settings/backfill-mode")
async def api_set_backfill_mode(request: Request):
    """切换 backfill 模式（0=正式，2=测试）

    注意：模式只对**下一次** backfill 生效（设置在 DB，启动时由 trade_backfill 读）。
    """
    from app.utils.database import SessionLocal
    from app.services.settings_service import update_setting
    db = SessionLocal()
    try:
        body = await request.json()
        new_mode = int(body.get("mode", 0))
        if new_mode not in (0, 2):
            return JSONResponse({"error": "mode 只能是 0 或 2"}, status_code=400)
        update_setting(db, "backfill_skip_ws_wait", str(new_mode))
        # 同步更新进程内缓存（仅影响后续 backfill）
        try:
            from app.services.trade_processor import set_backfill_broadcast_mode
            set_backfill_broadcast_mode(new_mode)
        except Exception:
            pass
        return JSONResponse({
            "mode": new_mode,
            "is_test_mode": new_mode == 2,
            "description": "测试模式 (backfill 期间也广播)" if new_mode == 2 else "正式模式 (backfill 期间不广播)",
            "message": f"已切换到{'测试' if new_mode == 2 else '正式'}模式（下次启动 mint 时生效）",
        })
    except Exception as e:
        logger.error(f"[backfill 模式] 切换失败: {e}", exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=500)
    finally:
        db.close()
