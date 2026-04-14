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

    # ===== API 端点（使用 query params 避免 sqladmin 菜单解析 path params 出错） =====

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
                    "sol_spent": tx.sol_spent,
                    "fee": tx.fee,
                    "source": tx.source,
                    "net_sol_flow": analysis.net_sol_flow if analysis else 0.0,
                    "net_token_flow": analysis.net_token_flow if analysis else 0.0,
                    "price_per_token": analysis.price_per_token if analysis else 0.0,
                    "wallet_tag": analysis.wallet_tag if analysis else None,
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

        db = SessionLocal()
        try:
            from app.services.settings_service import get_setting
            api_key = get_setting(db, "helius_api_key") or ""
            stream = TradeStream(mint=mint, api_key=api_key)
            backfill = TradeBackfill(db=db, mint=mint, stream=stream)
            active_monitors[mint] = {"backfill": backfill, "stream": stream}

            # 先启动实时流
            asyncio.create_task(stream.start())

            # 实时流启动后立即启动回填
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
        return JSONResponse({"message": f"已停止监听 {mint}"})

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
