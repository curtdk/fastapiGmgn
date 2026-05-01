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
from app.models.models import User, Token
from app.models.trade import Setting
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
        from app.services import tx_redis
        mint = request.query_params.get("mint", "")
        page = int(request.query_params.get("page", 1))
        page_size = int(request.query_params.get("page_size", 300))
        
        # 从 Redis 获取交易列表
        rpc_sigs = await tx_redis.get_tx_list(mint, "rpc_fill")
        ws_sigs = await tx_redis.get_tx_list(mint, "ws")
        total = len(rpc_sigs) + len(ws_sigs)
        
        # 分页
        start = (page - 1) * page_size
        end = start + page_size
        all_sigs = rpc_sigs + ws_sigs
        paginated_sigs = all_sigs[start:end]
        
        trade_list = []
        for sig in paginated_sigs:
            tx_detail = await tx_redis.get_tx(sig)
            if not tx_detail:
                continue
            trade_list.append({
                "sig": tx_detail.get("sig", ""),
                "slot": tx_detail.get("slot", 0),
                "block_time": tx_detail.get("block_time"),
                "from_address": tx_detail.get("from_address", ""),
                "to_address": tx_detail.get("to_address", ""),
                "amount": tx_detail.get("amount", 0),
                "token_mint": tx_detail.get("token_mint", mint),
                "token_symbol": tx_detail.get("token_symbol", ""),
                "transaction_type": tx_detail.get("transaction_type", ""),
                "dex": tx_detail.get("dex", ""),
                "pool_address": tx_detail.get("pool_address", ""),
                "sol_spent": round(tx_detail.get("sol_spent", 0) or 0, 6),
                "fee": tx_detail.get("fee", 0),
                "source": tx_detail.get("source", ""),
                "net_sol_flow": tx_detail.get("net_sol_flow", 0.0),
                "net_token_flow": tx_detail.get("net_token_flow", 0.0),
                "price_per_token": tx_detail.get("price_per_token", 0.0),
                "wallet_tag": tx_detail.get("wallet_tag"),
                "risk_score": tx_detail.get("risk_score", 0) or 0,
                "risk_verdict": tx_detail.get("risk_verdict", ""),
                "risk_indicators": tx_detail.get("risk_indicators", "[]"),
                "priority_fee": tx_detail.get("priority_fee", 0),
                "cu_consumed": tx_detail.get("cu_consumed", 0) or 0,
                "cu_limit": tx_detail.get("cu_limit", 200000) or 200000,
                "cu_price": tx_detail.get("cu_price", 0) or 0,
                "instructions_count": tx_detail.get("instructions_count", 0) or 0,
                "inner_instructions_count": tx_detail.get("inner_instructions_count", 0) or 0,
                "total_instruction_count": tx_detail.get("total_instruction_count", 0) or 0,
                "account_keys_count": tx_detail.get("account_keys_count", 0) or 0,
                "uses_lookup_table": tx_detail.get("uses_lookup_table", False) or False,
                "signers_count": tx_detail.get("signers_count", 0) or 0,
                "main_instructions": tx_detail.get("main_instructions", "[]"),
                "inner_instructions": tx_detail.get("inner_instructions", "[]"),
                "program_ids": tx_detail.get("program_ids", "[]"),
            })
        return JSONResponse({"trades": trade_list, "total": total, "has_more": (end < total)})

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

    @expose("/api/sol-price", methods=["GET"])
    async def api_get_sol_price(self, request: Request):
        """从 Binance API 获取 SOL/USD 价格（后端转发避免 CORS）"""
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get("https://api.binance.com/api/v3/ticker/price?symbol=SOLUSDT")
            data = resp.json()
            price = float(data.get("price", 0))
            if price > 0:
                return JSONResponse({"price": price})
            return JSONResponse({"error": "无法获取价格"}, status_code=500)
        except Exception as e:
            logger.error(f"[SOL价格] 获取失败: {e}")
            return JSONResponse({"error": str(e)}, status_code=500)


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


# Redis 相关类已移至 app/admin/redis_admin.py
from app.admin.redis_admin import RedisViewerMenu, RedisKeysMenu, RedisApiView


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
admin.add_view(TokenAdmin)
admin.add_view(SettingAdmin)
admin.add_base_view(TradeMonitorView)
admin.add_base_view(DealerSettingsMenu)
admin.add_base_view(TradeLiveMenu)
admin.add_base_view(RedisViewerMenu)
admin.add_base_view(RedisKeysMenu)
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
