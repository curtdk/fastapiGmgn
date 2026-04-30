"""Redis 管理相关类"""
import json
from sqladmin import BaseView, expose
from fastapi import Request
from fastapi.responses import JSONResponse


class RedisViewerMenu(BaseView):
    """Redis 信息菜单项（只用于显示页面菜单）"""
    name = "Redis 信息"
    icon = "fa-solid fa-database"

    @expose("/admin/redis-viewer", methods=["GET"], identity="redis-viewer")
    async def redis_viewer_page(self, request: Request):
        return await self.templates.TemplateResponse(request, "redis_viewer.html")


class RedisKeysMenu(BaseView):
    """Redis Keys 管理菜单项"""
    name = "Redis 信息管理"
    icon = "fa-solid fa-key"

    @expose("/admin/redis-keys", methods=["GET"], identity="redis-keys")
    async def redis_keys_page(self, request: Request):
        return await self.templates.TemplateResponse(request, "redis_keys.html")


class RedisApiView(BaseView):
    """Redis API 端点（单独一个 @expose，identity 固定）"""
    name = ""
    icon = ""

    @expose("/api/redis/mints", methods=["GET"])
    async def api_get_mints(self, request: Request):
        from app.services.dealer_detector import _redis
        mints = []
        if _redis:
            seen = set()
            cursor = 0
            while True:
                cursor, keys = await _redis.scan(cursor, match="metrics:*", count=200)
                for key in keys:
                    mint = key.replace("metrics:", "")
                    if mint not in seen:
                        seen.add(mint)
                        metrics = await _redis.hgetall(key)
                        mints.append({
                            "mint": mint,
                            "total_bet": float(metrics.get("total_bet", 0)),
                            "realized_profit": float(metrics.get("realized_profit", 0)),
                            "dealer_count": int(metrics.get("dealer_count", 0)),
                        })
                cursor, keys = await _redis.scan(cursor, match="txlist:*", count=200)
                for key in keys:
                    parts = key.split(":")
                    if len(parts) >= 3 and ":counter" not in key:
                        mint = ":".join(parts[2:])
                        if mint not in seen:
                            seen.add(mint)
                if cursor == 0:
                    break
        return JSONResponse({"mints": sorted(mints, key=lambda x: x["total_bet"], reverse=True), "count": len(mints)})

    @expose("/api/redis/txlist", methods=["GET"])
    async def api_get_txlist(self, request: Request):
        mint = request.query_params.get("mint", "")
        source = request.query_params.get("source", "rpc_fill")
        page = int(request.query_params.get("page", 1))
        page_size = int(request.query_params.get("page_size", 500))
        if not mint:
            return JSONResponse({"sigs": [], "count": 0})
        from app.services import tx_redis
        sigs = await tx_redis.get_tx_list(mint, source)
        count = await tx_redis.get_tx_count(mint, source)
        start = (page - 1) * page_size
        end = start + page_size
        paginated = sigs[start:end]
        return JSONResponse({"sigs": paginated, "count": count, "page": page, "page_size": page_size, "has_more": end < count})

    @expose("/api/redis/tx-detail", methods=["GET"])
    async def api_get_tx_detail(self, request: Request):
        sig = request.query_params.get("sig", "")
        if not sig:
            return JSONResponse({"error": "sig is required"}, status_code=400)
        from app.services import tx_redis
        tx = await tx_redis.get_tx(sig)
        return JSONResponse({"tx": tx})

    @expose("/api/redis/metrics", methods=["GET"])
    async def api_get_metrics(self, request: Request):
        mint = request.query_params.get("mint", "")
        from app.services.dealer_detector import _redis
        metrics = {}
        if _redis and mint:
            key = f"metrics:{mint}"
            data = await _redis.hgetall(key)
            for k, v in data.items():
                try:
                    metrics[k] = float(v)
                except:
                    metrics[k] = v
        return JSONResponse({"metrics": metrics})

    @expose("/api/redis/users", methods=["GET"])
    async def api_get_users(self, request: Request):
        mint = request.query_params.get("mint", "")
        status_filter = request.query_params.get("status", "")
        from app.services.dealer_detector import _redis
        users = []
        if _redis and mint:
            cursor = 0
            while True:
                cursor, keys = await _redis.scan(cursor, match="user:*", count=100)
                for key in keys:
                    user_data = await _redis.hgetall(key)
                    if user_data:
                        holding_qty = user_data.get(f"{mint}_holdingQty")
                        if holding_qty and float(holding_qty) > 0:
                            status = user_data.get("status", "unknown")
                            if status_filter and status != status_filter:
                                continue
                            try:
                                conditions = json.loads(user_data.get("conditions", "[]"))
                            except:
                                conditions = []
                            users.append({
                                "address": key.replace("user:", ""),
                                "status": status,
                                "conditions": conditions,
                                "holdingQty": float(holding_qty),
                                "holdingCost": float(user_data.get(f"{mint}_holdingCost", "0")),
                                "avgPrice": float(user_data.get(f"{mint}_avgPrice", "0")),
                                "totalBuyAmount": float(user_data.get(f"{mint}_totalBuyAmount", "0")),
                                "totalSellAmount": float(user_data.get(f"{mint}_totalSellAmount", "0")),
                                "totalSellPrincipal": float(user_data.get(f"{mint}_totalSellPrincipal", "0")),
                            })
                if cursor == 0:
                    break
        users.sort(key=lambda x: x["holdingCost"], reverse=True)
        return JSONResponse({"users": users, "count": len(users)})

    @expose("/api/redis/clear-users", methods=["POST"])
    async def api_clear_users(self, request: Request):
        """清除指定 mint 的全部用户数据"""
        body = await request.json()
        mint = body.get("mint", "")
        if not mint:
            return JSONResponse({"error": "mint is required"}, status_code=400)
        from app.services.dealer_detector import _redis
        deleted = 0
        if _redis:
            cursor = 0
            while True:
                cursor, keys = await _redis.scan(cursor, match="user:*", count=100)
                for key in keys:
                    user_data = await _redis.hgetall(key)
                    holding_qty = user_data.get(f"{mint}_holdingQty")
                    if holding_qty:
                        await _redis.hdel(key, f"{mint}_holdingQty")
                        await _redis.hdel(key, f"{mint}_holdingCost")
                        await _redis.hdel(key, f"{mint}_avgPrice")
                        await _redis.hdel(key, f"{mint}_totalBuyAmount")
                        await _redis.hdel(key, f"{mint}_totalSellAmount")
                        await _redis.hdel(key, f"{mint}_totalSellPrincipal")
                        deleted += 1
                if cursor == 0:
                    break
        return JSONResponse({"message": f"已清除 {deleted} 个用户的 {mint} 数据"})

    @expose("/api/redis/clear-user", methods=["POST"])
    async def api_clear_user(self, request: Request):
        """清除单个用户数据"""
        body = await request.json()
        address = body.get("address", "")
        mint = body.get("mint", "")
        if not address:
            return JSONResponse({"error": "address is required"}, status_code=400)
        from app.services.dealer_detector import _redis
        key = f"user:{address}"
        if _redis:
            if mint:
                await _redis.hdel(key, f"{mint}_holdingQty")
                await _redis.hdel(key, f"{mint}_holdingCost")
                await _redis.hdel(key, f"{mint}_avgPrice")
                await _redis.hdel(key, f"{mint}_totalBuyAmount")
                await _redis.hdel(key, f"{mint}_totalSellAmount")
                await _redis.hdel(key, f"{mint}_totalSellPrincipal")
                return JSONResponse({"message": f"已清除用户 {address} 的 {mint} 数据"})
            else:
                await _redis.delete(key)
                return JSONResponse({"message": f"已清除用户 {address} 的全部数据"})
        return JSONResponse({"error": "Redis 未连接"}, status_code=500)

    @expose("/api/redis/clear-all-tx", methods=["POST"])
    async def api_clear_all_tx(self, request: Request):
        """清除全部 Redis 交易记录（txlist:* 和 tx:*）"""
        from app.services.dealer_detector import _redis
        deleted = 0
        if _redis:
            cursor = 0
            while True:
                cursor, keys = await _redis.scan(cursor, match="txlist:*", count=200)
                if keys:
                    await _redis.delete(*keys)
                    deleted += len(keys)
                if cursor == 0:
                    break
            cursor = 0
            while True:
                cursor, keys = await _redis.scan(cursor, match="tx:*", count=200)
                if keys:
                    await _redis.delete(*keys)
                    deleted += len(keys)
                if cursor == 0:
                    break
        return JSONResponse({"message": f"已清除 {deleted} 个交易相关 keys"})

    @expose("/api/redis/clear-all-users", methods=["POST"])
    async def api_clear_all_users(self, request: Request):
        """清除全部用户数据（user:*）"""
        from app.services.dealer_detector import _redis
        deleted = 0
        if _redis:
            cursor = 0
            while True:
                cursor, keys = await _redis.scan(cursor, match="user:*", count=200)
                if keys:
                    await _redis.delete(*keys)
                    deleted += len(keys)
                if cursor == 0:
                    break
        return JSONResponse({"message": f"已清除 {deleted} 个用户 keys"})

    # ===== 新增：Redis Keys 管理 API =====

    @expose("/api/redis/keys", methods=["GET"])
    async def api_get_keys(self, request: Request):
        """获取 Redis key 列表（分页、筛选）"""
        import app.services.dealer_detector as dealer_module
        import redis.asyncio as aioredis
        
        prefix = request.query_params.get("prefix", "")
        search_mode = request.query_params.get("mode", "prefix")  # prefix | exact | contains
        key_type = request.query_params.get("type", "")  # hash, string, zset, list, set
        page = int(request.query_params.get("page", 1))
        page_size = int(request.query_params.get("page_size", 500))
        
        # 使用模块方式访问 _redis，确保获取最新值
        _redis = dealer_module._redis
        REDIS_URL = dealer_module.REDIS_URL
        
        if _redis:
            redis_client = _redis
        else:
            redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)
        
        # 统计各类型数量
        stats = {"hash": 0, "string": 0, "zset": 0, "list": 0, "set": 0, "total": 0}
        
        # 构建匹配模式（Redis SCAN 需要 glob 通配符）
        if prefix:
            if search_mode == "exact":
                # 精确匹配：直接用 key 名（但 SCAN 需要通配符，用 *key* 然后筛选）
                match_pattern = "*"
                exact_match_filter = prefix
            elif search_mode == "contains":
                # 包含匹配：*keyword*
                match_pattern = f"*{prefix}*"
            else:
                # 前缀匹配（默认）：keyword*
                match_pattern = f"{prefix}*"
        else:
            match_pattern = "*"
        
        # 确保 exact_match_filter 始终定义
        exact_match_filter = locals().get('exact_match_filter')
        
        all_keys = []
        cursor = 0
        while True:
            cursor, keys = await redis_client.scan(cursor, match=match_pattern, count=500)
            for key in keys:
                # 精确匹配二次筛选
                if exact_match_filter and key != exact_match_filter:
                    continue
                    
                try:
                    key_type_name = await redis_client.type(key)
                    if key_type and key_type_name != key_type:
                        continue
                    
                    # 统计
                    if key_type_name in stats:
                        stats[key_type_name] += 1
                    stats["total"] += 1
                    
                    # 获取大小
                    ttl = await redis_client.ttl(key)
                    size = 0
                    try:
                        if key_type_name == "string":
                            size = len(await redis_client.get(key) or "")
                        elif key_type_name == "hash":
                            size = await redis_client.hlen(key)
                        elif key_type_name == "zset":
                            size = await redis_client.zcard(key)
                        elif key_type_name == "list":
                            size = await redis_client.llen(key)
                        elif key_type_name == "set":
                            size = await redis_client.scard(key)
                    except:
                        pass
                    
                    # 获取 key 的详细信息
                    key_info = {
                        "key": key,
                        "type": key_type_name,
                        "size": size,
                        "ttl": ttl if ttl > 0 else -1
                    }
                    
                    # 如果是 user:* key，额外获取 status
                    if key.startswith("user:"):
                        user_data = await redis_client.hgetall(key)
                        key_info["status"] = user_data.get("status", "-")
                        key_info["conditions"] = user_data.get("conditions", "[]")
                    
                    all_keys.append(key_info)
                except Exception as e:
                    pass
                    
            if cursor == 0:
                break
        
        # 排序（按 key 名称）
        all_keys.sort(key=lambda x: x["key"])
        
        # 分页
        total = len(all_keys)
        start = (page - 1) * page_size
        end = start + page_size
        paginated_keys = all_keys[start:end]
        
        return JSONResponse({
            "keys": paginated_keys,
            "count": total,
            "page": page,
            "page_size": page_size,
            "stats": stats
        })

    @expose("/api/redis/key-detail", methods=["GET"])
    async def api_get_key_detail(self, request: Request):
        """获取单个 key 的详情"""
        from app.services.dealer_detector import _redis
        
        key = request.query_params.get("key", "")
        if not key:
            return JSONResponse({"error": "key is required"}, status_code=400)
        
        if not _redis:
            return JSONResponse({"error": "Redis 未连接"}, status_code=500)
        
        try:
            key_type = await _redis.type(key)
            result = {"key": key, "type": key_type}
            
            if key_type == "string":
                value = await _redis.get(key)
                result["value"] = value
                result["value_preview"] = (value or "")[:500] if value else ""
            elif key_type == "hash":
                data = await _redis.hgetall(key)
                result["fields"] = {k: v for k, v in data.items()}
                result["count"] = len(data)
            elif key_type == "zset":
                members = await _redis.zrange(key, 0, 99, withscores=True)
                result["members"] = [{"member": m, "score": s} for m, s in members]
                result["total"] = await _redis.zcard(key)
            elif key_type == "list":
                items = await _redis.lrange(key, 0, 99)
                result["items"] = items
                result["total"] = await _redis.llen(key)
            elif key_type == "set":
                members = await _redis.smembers(key)
                result["members"] = list(members)[:100]
                result["total"] = await _redis.scard(key)
            else:
                result["value"] = str(await _redis.get(key) or "")
            
            result["ttl"] = await _redis.ttl(key)
            return JSONResponse(result)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @expose("/api/redis/key", methods=["PUT"])
    async def api_update_key(self, request: Request):
        """更新 key 的值"""
        from app.services.dealer_detector import _redis
        
        body = await request.json()
        key = body.get("key", "")
        value = body.get("value", "")
        
        if not key:
            return JSONResponse({"error": "key is required"}, status_code=400)
        
        if not _redis:
            return JSONResponse({"error": "Redis 未连接"}, status_code=500)
        
        try:
            key_type = await _redis.type(key)
            
            if key_type == "string":
                await _redis.set(key, value)
            elif key_type == "hash":
                # 解析 field:value 格式
                if isinstance(value, str):
                    for line in value.strip().split("\n"):
                        if ":" in line:
                            field, val = line.split(":", 1)
                            await _redis.hset(key, field.strip(), val.strip())
                elif isinstance(value, dict):
                    for field, val in value.items():
                        await _redis.hset(key, field, val)
            elif key_type == "zset":
                # 解析 member:score 格式
                if isinstance(value, str):
                    for line in value.strip().split("\n"):
                        if ":" in line:
                            member, score = line.split(":", 1)
                            await _redis.zadd(key, {member.strip(): float(score)})
            elif key_type == "list":
                # 先清空再添加
                await _redis.delete(key)
                if isinstance(value, list):
                    for item in value:
                        await _redis.rpush(key, item)
                elif isinstance(value, str):
                    for item in value.strip().split("\n"):
                        if item.strip():
                            await _redis.rpush(key, item.strip())
            elif key_type == "set":
                # 先清空再添加
                await _redis.delete(key)
                if isinstance(value, list):
                    for item in value:
                        await _redis.sadd(key, item)
                elif isinstance(value, str):
                    for item in value.strip().split("\n"):
                        if item.strip():
                            await _redis.sadd(key, item.strip())
            else:
                await _redis.set(key, str(value))
            
            return JSONResponse({"message": f"已更新 key: {key}"})
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @expose("/api/redis/key", methods=["DELETE"])
    async def api_delete_key(self, request: Request):
        """删除 key"""
        from app.services.dealer_detector import _redis
        
        body = await request.json()
        key = body.get("key", "")
        
        if not key:
            return JSONResponse({"error": "key is required"}, status_code=400)
        
        if not _redis:
            return JSONResponse({"error": "Redis 未连接"}, status_code=500)
        
        try:
            deleted = await _redis.delete(key)
            return JSONResponse({"message": f"已删除 key: {key}", "deleted": deleted})
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @expose("/api/redis/export/users", methods=["GET"])
    async def api_export_users(self, request: Request):
        """导出全部用户数据"""
        from app.services.dealer_detector import _redis
        
        if not _redis:
            return JSONResponse({"error": "Redis 未连接"}, status_code=500)
        
        users_data = []
        cursor = 0
        while True:
            cursor, keys = await _redis.scan(cursor, match="user:*", count=100)
            for key in keys:
                user_data = await _redis.hgetall(key)
                if user_data:
                    users_data.append({
                        "key": key,
                        "data": {k: v for k, v in user_data.items()}
                    })
            if cursor == 0:
                break
        
        return JSONResponse({
            "count": len(users_data),
            "users": users_data
        })

    @expose("/api/redis/import/users", methods=["POST"])
    async def api_import_users(self, request: Request):
        """导入用户数据（覆盖）"""
        from app.services.dealer_detector import _redis
        
        body = await request.json()
        users = body.get("users", [])
        
        if not _redis:
            return JSONResponse({"error": "Redis 未连接"}, status_code=500)
        
        imported = 0
        errors = []
        
        for user in users:
            key = user.get("key", "")
            data = user.get("data", {})
            
            if not key or not data:
                errors.append(f"Invalid user data: {user}")
                continue
            
            try:
                await _redis.delete(key)
                if data:
                    await _redis.hset(key, mapping=data)
                imported += 1
            except Exception as e:
                errors.append(f"Error importing {key}: {str(e)}")
        
        return JSONResponse({
            "message": f"已导入 {imported} 个用户",
            "errors": errors[:10]
        })

    @expose("/api/redis/export/all", methods=["GET"])
    async def api_export_all(self, request: Request):
        """导出全部 Redis 数据"""
        from app.services.dealer_detector import _redis
        
        if not _redis:
            return JSONResponse({"error": "Redis 未连接"}, status_code=500)
        
        all_data = []
        cursor = 0
        while True:
            cursor, keys = await _redis.scan(cursor, match="*", count=500)
            for key in keys:
                try:
                    key_type = await _redis.type(key)
                    item = {"key": key, "type": key_type}
                    
                    if key_type == "string":
                        item["value"] = await _redis.get(key)
                    elif key_type == "hash":
                        item["fields"] = await _redis.hgetall(key)
                    elif key_type == "zset":
                        members = await _redis.zrange(key, 0, -1, withscores=True)
                        item["members"] = [{"member": m, "score": s} for m, s in members]
                    elif key_type == "list":
                        item["items"] = await _redis.lrange(key, 0, -1)
                    elif key_type == "set":
                        item["members"] = list(await _redis.smembers(key))
                    
                    all_data.append(item)
                except:
                    pass
            if cursor == 0:
                break
        
        return JSONResponse({
            "count": len(all_data),
            "data": all_data
        })

    @expose("/api/redis/import/all", methods=["POST"])
    async def api_import_all(self, request: Request):
        """导入全部数据（覆盖）"""
        from app.services.dealer_detector import _redis
        
        body = await request.json()
        all_data = body.get("data", [])
        
        if not _redis:
            return JSONResponse({"error": "Redis 未连接"}, status_code=500)
        
        imported = 0
        errors = []
        
        for item in all_data:
            key = item.get("key", "")
            key_type = item.get("type", "")
            
            if not key:
                continue
            
            try:
                await _redis.delete(key)
                
                if key_type == "string":
                    value = item.get("value", "")
                    if value:
                        await _redis.set(key, value)
                elif key_type == "hash":
                    fields = item.get("fields", {})
                    if fields:
                        await _redis.hset(key, mapping=fields)
                elif key_type == "zset":
                    members = item.get("members", [])
                    if members:
                        mapping = {m["member"]: m["score"] for m in members}
                        await _redis.zadd(key, mapping)
                elif key_type == "list":
                    items = item.get("items", [])
                    if items:
                        for i in items:
                            await _redis.rpush(key, i)
                elif key_type == "set":
                    members = item.get("members", [])
                    if members:
                        for m in members:
                            await _redis.sadd(key, m)
                
                imported += 1
            except Exception as e:
                errors.append(f"Error importing {key}: {str(e)}")
        
        return JSONResponse({
            "message": f"已导入 {imported} 条数据",
            "errors": errors[:20]
        })

    @expose("/api/redis/delete-by-prefix", methods=["DELETE"])
    async def api_delete_by_prefix(self, request: Request):
        """按前缀删除 key"""
        from app.services.dealer_detector import _redis
        
        body = await request.json()
        prefix = body.get("prefix", "")
        
        if not prefix:
            return JSONResponse({"error": "prefix is required"}, status_code=400)
        
        if not _redis:
            return JSONResponse({"error": "Redis 未连接"}, status_code=500)
        
        deleted = 0
        cursor = 0
        while True:
            cursor, keys = await _redis.scan(cursor, match=f"{prefix}*", count=500)
            if keys:
                await _redis.delete(*keys)
                deleted += len(keys)
            if cursor == 0:
                break
        
        return JSONResponse({
            "message": f"已删除 {deleted} 个 key (前缀: {prefix})",
            "deleted": deleted
        })
