import json
import logging
import os
from logging.handlers import RotatingFileHandler
from datetime import datetime

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from sqladmin import Admin, ModelView, BaseView, expose
from sqladmin.authentication import AuthenticationBackend
from app.routes import users, auth
from app.routes.trades import router as trades_router, settings_router, active_monitors
from app.utils.database import engine, Base, SessionLocal
from app.models.models import User, Transaction, Token
from app.models.trade import Setting, TradeAnalysis
from app.routes.auth import verify_password
from app.websocket.manager import ws_manager

# 配置日志
LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        RotatingFileHandler(
            os.path.join(LOG_DIR, "app.log"),
            maxBytes=10 * 1024 * 1024,  # 10MB
            backupCount=5
        ),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

# 创建数据库表
Base.metadata.create_all(bind=engine)

# 迁移 transactions 表（添加新列）
from app.utils.database import migrate_transactions_table
migrate_transactions_table()

SESSION_SECRET = "your-secret-key-change-in-production"

app = FastAPI(title="GMGN API", version="1.0.0")


@app.on_event("startup")
async def on_startup():
    """应用启动：初始化庄家判定服务"""
    from app.services.dealer_detector import init_dealer_detector
    await init_dealer_detector()
    logger.info("庄家判定服务已启动")


@app.on_event("shutdown")
async def on_shutdown():
    """应用关闭：释放庄家判定服务资源"""
    from app.services.dealer_detector import close_dealer_detector
    await close_dealer_detector()
    logger.info("庄家判定服务已关闭")


# Session 中间件 (SQLAdmin 需要)
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(auth.router, prefix="/api/auth", tags=["认证"])
app.include_router(users.router, prefix="/api/users", tags=["用户"])
app.include_router(trades_router)
app.include_router(settings_router)
from app.routes.trades import live_router
app.include_router(live_router)

# ========== Admin Model Views ==========
class UserAdmin(ModelView, model=User):
    column_list = [User.id, User.username, User.email, User.is_active, User.is_admin, User.created_at]
    column_searchable_list = [User.username, User.email]
    column_sortable_list = [User.created_at]
    can_delete = True
    can_create = True
    can_edit = True

class TransactionAdmin(ModelView, model=Transaction):
    column_list = [Transaction.id, Transaction.sig, Transaction.from_address, Transaction.to_address,
                   Transaction.token_mint, Transaction.dex, Transaction.source, Transaction.block_time]
    column_searchable_list = [Transaction.sig, Transaction.from_address, Transaction.to_address, Transaction.token_mint]
    column_sortable_list = [Transaction.block_time]
    can_delete = True
    can_create = False
    can_edit = False

class TokenAdmin(ModelView, model=Token):
    column_list = [Token.id, Token.mint_address, Token.symbol, Token.name, Token.price, Token.market_cap]
    column_searchable_list = [Token.mint_address, Token.symbol, Token.name]
    column_sortable_list = [Token.created_at]
    can_delete = True
    can_create = True
    can_edit = True

class SettingAdmin(ModelView, model=Setting):
    column_list = [Setting.key, Setting.value, Setting.description, Setting.updated_at]
    column_searchable_list = [Setting.key]
    can_delete = False
    can_create = False
    can_edit = True

class TradeAnalysisAdmin(ModelView, model=TradeAnalysis):
    column_list = [TradeAnalysis.id, TradeAnalysis.sig, TradeAnalysis.net_sol_flow,
                   TradeAnalysis.net_token_flow, TradeAnalysis.price_per_token,
                   TradeAnalysis.wallet_tag, TradeAnalysis.processed_at]
    column_searchable_list = [TradeAnalysis.sig]
    column_sortable_list = [TradeAnalysis.processed_at]
    can_delete = True
    can_create = False
    can_edit = False

# ========== 自定义 Admin 页面（含页面 + API） ==========
active_monitors: dict = {}

class TradeMonitorView(BaseView):
    """交易监控页面"""
    name = "交易监控"
    icon = "fa-solid fa-chart-line"

    @expose("/trade-monitor", methods=["GET"])
    async def monitor_page(self, request: Request):
        return await self.templates.TemplateResponse(request, "trade_monitor.html")

    @expose("/settings-page", methods=["GET"])
    async def settings_page(self, request: Request):
        return await self.templates.TemplateResponse(request, "settings.html")

    @expose("/dealer-settings", methods=["GET"])
    async def dealer_settings_page(self, request: Request):
        return await self.templates.TemplateResponse(request, "dealer_settings.html")

    # ===== API 端点 =====

    @expose("/api/trades", methods=["GET"])
    async def api_get_trades(self, request: Request):
        mint = request.query_params.get("mint", "")
        page = int(request.query_params.get("page", 1))
        page_size = int(request.query_params.get("page_size", 300))
        db = SessionLocal()
        try:
            from app.models.models import Transaction
            from app.models.trade import TradeAnalysis
            total = db.query(Transaction).filter(Transaction.token_mint == mint).count()
            trades = (
                db.query(Transaction, TradeAnalysis)
                .outerjoin(TradeAnalysis, Transaction.sig == TradeAnalysis.sig)
                .filter(Transaction.token_mint == mint)
                .order_by(Transaction.slot.desc(), Transaction.block_time.desc(), Transaction.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
                .all()
            )
            trade_list = []
            for tx, analysis in trades:
                trade_list.append({
                    "sig": tx.sig,
                    "slot": tx.slot,
                    "block_time": tx.block_time.isoformat() if tx.block_time else None,
                    "from_address": tx.from_address,
                    "to_address": tx.to_address,
                    "amount": tx.amount,
                    "token_mint": tx.token_mint,
                    "token_symbol": tx.token_symbol,
                    "transaction_type": tx.transaction_type,
                    "dex": tx.dex,
                    "pool_address": tx.pool_address,
                    "sol_spent": round(tx.sol_spent, 6) if tx.sol_spent is not None else None,
                    "fee": tx.fee,
                    "source": tx.source,
                    "net_sol_flow": analysis.net_sol_flow if analysis else 0.0,
                    "net_token_flow": analysis.net_token_flow if analysis else 0.0,
                    "price_per_token": analysis.price_per_token if analysis else 0.0,
                    "wallet_tag": analysis.wallet_tag if analysis else None,
                    "risk_score": tx.risk_score or 0,
                    "risk_verdict": tx.risk_verdict,
                    "risk_indicators": tx.risk_indicators,
                    "priority_fee": tx.priority_fee,
                    "cu_consumed": tx.cu_consumed or 0,
                    "cu_limit": tx.cu_limit or 200000,
                    "cu_price": tx.cu_price or 0,
                    "instructions_count": tx.instructions_count or 0,
                    "inner_instructions_count": tx.inner_instructions_count or 0,
                    "total_instruction_count": tx.total_instruction_count or 0,
                    "account_keys_count": tx.account_keys_count or 0,
                    "uses_lookup_table": tx.uses_lookup_table or False,
                    "signers_count": tx.signers_count or 0,
                    "main_instructions": tx.main_instructions,
                    "inner_instructions": tx.inner_instructions,
                    "program_ids": tx.program_ids,
                })
            return JSONResponse({"trades": trade_list, "total": total, "has_more": (page * page_size < total)})
        finally:
            db.close()

    @expose("/api/metrics", methods=["GET"])
    async def api_get_metrics(self, request: Request):
        mint = request.query_params.get("mint", "")
        from app.services.trade_processor import calculate_metrics
        db = SessionLocal()
        try:
            metrics = await calculate_metrics(db, mint)
            return JSONResponse(metrics)
        finally:
            db.close()

    @expose("/api/start", methods=["POST"])
    async def api_start_monitor(self, request: Request):
        import asyncio
        from app.services.trade_backfill import TradeBackfill
        from app.services.trade_stream import TradeStream

        form = await request.form()
        mint = form.get("mint", "")

        if mint in active_monitors:
            monitor = active_monitors[mint]
            bf = monitor.get("backfill")
            st = monitor.get("stream")
            if bf: bf.stop()
            if st: await st.stop()
            del active_monitors[mint]

            from app.services.trade_processor import reset_processor
            _reset_db = SessionLocal()
            try:
                await reset_processor(mint, _reset_db)
            finally:
                _reset_db.close()

        db = SessionLocal()
        try:
            from app.services.settings_service import get_setting
            api_key = get_setting(db, "helius_api_key") or ""
            stream = TradeStream(mint=mint, api_key=api_key)
            backfill = TradeBackfill(db=db, mint=mint, stream=stream)
            active_monitors[mint] = {"backfill": backfill, "stream": stream}

            asyncio.create_task(stream.start())

            async def start_backfill_after():
                while not stream.running:
                    await asyncio.sleep(0.2)
                await backfill.run()

            asyncio.create_task(start_backfill_after())
            return JSONResponse({"message": f"已开始监听 {mint}"})
        finally:
            db.close()

    @expose("/api/stop", methods=["POST"])
    async def api_stop_monitor(self, request: Request):
        form = await request.form()
        mint = form.get("mint", "")
        monitor = active_monitors.get(mint)
        if not monitor:
            return JSONResponse({"message": "未运行"})
        bf = monitor.get("backfill")
        st = monitor.get("stream")
        if bf: bf.stop()
        if st: await st.stop()
        del active_monitors[mint]

        # 不再删除数据库记录，只清理 Redis 内存状态
        # from app.services.trade_processor import reset_processor
        # db = SessionLocal()
        # try:
        #     await reset_processor(mint, db)
        # finally:
        #     db.close()

        return JSONResponse({"message": f"已停止监听 {mint}，数据库记录已保留"})

    @expose("/api/settings", methods=["GET"])
    async def api_get_settings(self, request: Request):
        db = SessionLocal()
        try:
            from app.services.settings_service import get_all_settings
            return JSONResponse(get_all_settings(db))
        finally:
            db.close()

    @expose("/api/settings", methods=["PUT"])
    async def api_update_setting(self, request: Request):
        db = SessionLocal()
        try:
            body = await request.json()
            key = body.get("key", "")
            from app.services.settings_service import update_setting
            result = update_setting(db, key, body.get("value", ""))
            return JSONResponse({"key": result.key, "value": result.value})
        finally:
            db.close()


class DealerSettingsMenu(BaseView):
    """庄家设置菜单项"""
    name = "庄家设置"
    icon = "fa-solid fa-user-shield"

    @expose("/dealer-settings", methods=["GET"])
    async def dealer_settings_page(self, request: Request):
        return await self.templates.TemplateResponse(request, "dealer_settings.html")


class TradeLiveMenu(BaseView):
    """实时交易菜单项"""
    name = "实时交易"
    icon = "fa-solid fa-bolt"

    @expose("/trade", methods=["GET"])
    async def trade_page(self, request: Request):
        return await self.templates.TemplateResponse(request, "trade_live.html")


class RedisViewerMenu(BaseView):
    """Redis 信息菜单项（只用于显示页面菜单）"""
    name = "Redis 信息"
    icon = "fa-solid fa-database"

    @expose("/admin/redis-viewer", methods=["GET"], identity="redis-viewer")
    async def redis_viewer_page(self, request: Request):
        return await self.templates.TemplateResponse(request, "redis_viewer.html")


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
        if not mint:
            return JSONResponse({"sigs": [], "count": 0})
        from app.services import tx_redis
        sigs = await tx_redis.get_tx_list(mint, source)
        count = await tx_redis.get_tx_count(mint, source)
        return JSONResponse({"sigs": sigs, "count": count})

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
                        # 清理该用户在此 mint 的数据
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
            # 清除 txlist:* 
            cursor = 0
            while True:
                cursor, keys = await _redis.scan(cursor, match="txlist:*", count=200)
                if keys:
                    await _redis.delete(*keys)
                    deleted += len(keys)
                if cursor == 0:
                    break
            # 清除 tx:*
            cursor = 0
            while True:
                cursor, keys = await _redis.scan(cursor, match="tx:*", count=200)
                if keys:
                    await _redis.delete(*keys)
                    deleted += len(keys)
                if cursor == 0:
                    break
        return JSONResponse({"message": f"已清除 {deleted} 个交易相关 keys"})


# ========== SQLAdmin 配置 ==========

# 自定义认证后端
class AdminAuth(AuthenticationBackend):
    async def login(self, request: Request) -> bool:
        form = await request.form()
        username = form.get("username")
        password = form.get("password")

        db = SessionLocal()
        try:
            user = db.query(User).filter(User.username == username).first()
            if user and verify_password(password, user.hashed_password):
                request.session["admin_user"] = user.username
                return True
        finally:
            db.close()

        return False

    async def logout(self, request: Request) -> bool:
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> bool:
        return request.session.get("admin_user") is not None

admin_auth = AdminAuth(SESSION_SECRET)

# 创建 Admin 实例（认证后端必须在构造函数中传入）
admin = Admin(
    app, engine,
    title="GMGN Admin",
    templates_dir="app/templates",
    authentication_backend=admin_auth,
)

# 注册视图
admin.add_view(UserAdmin)
admin.add_view(TransactionAdmin)
admin.add_view(TokenAdmin)
admin.add_view(SettingAdmin)
admin.add_view(TradeAnalysisAdmin)
admin.add_base_view(TradeMonitorView)
admin.add_base_view(DealerSettingsMenu)
admin.add_base_view(TradeLiveMenu)
admin.add_base_view(RedisViewerMenu)
admin.add_base_view(RedisApiView)

# ========== WebSocket 路由 ==========
@app.websocket("/ws/trades/{mint}")
async def admin_ws(websocket: WebSocket, mint: str):
    """管理后台 WebSocket 端点"""
    await ws_manager.connect(websocket, mint)
    try:
        # 发送当前状态
        monitor = active_monitors.get(mint)
        if monitor:
            stream = monitor.get("stream")
            backfill = monitor.get("backfill")
            if backfill and backfill.running:
                status = "BACKFILLING"
                message = f"回填中... 已获取 {backfill.total_fetched} 条"
            elif stream and stream.running:
                status = "STREAMING"
                message = "实时监听中..."
            else:
                status = "STOPPED"
                message = "已停止"
        else:
            status = "IDLE"
            message = "未启动"

        await websocket.send_json({
            "type": "status",
            "data": {"mint": mint, "status": status, "message": message, "progress": 0},
        })

        while True:
            data = await websocket.receive_text()
            try:
                msg = eval(data)
                if msg.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
            except Exception:
                pass
    except WebSocketDisconnect:
        pass
    finally:
        await ws_manager.disconnect(websocket)

# ========== 初始化超级管理员 ==========
def init_super_admin():
    from app.routes.auth import get_password_hash

    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.username == "admin").first()
        if not existing:
            admin_user = User(
                username="admin",
                email="admin@example.com",
                hashed_password=get_password_hash("admin123"),
                is_active=True,
                is_admin=True,
                created_at=datetime.utcnow()
            )
            db.add(admin_user)
            db.commit()
            logger.info("Admin user created: admin / admin123")
        else:
            logger.info("Admin user already exists")
    finally:
        db.close()

init_super_admin()

# 初始化默认设置
def init_settings():
    db = SessionLocal()
    try:
        from app.services.settings_service import init_default_settings
        init_default_settings(db)
    finally:
        db.close()

init_settings()

# ========== 路由 ==========
@app.get("/")
def root():
    logger.info("Root endpoint accessed")
    return RedirectResponse(url="/admin/trade-monitor")

@app.get("/health")
def health():
    logger.info("Health check passed")
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
