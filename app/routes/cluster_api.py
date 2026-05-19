"""簇组管理 API 路由"""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
import logging
logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["簇组管理"])


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
        "base_cu", "base_program_count", "base_main_instruction_count",
        "base_inner_instruction_count", "tx_count", "user_count", "created_at"
    }
    if sort_by not in allowed_sort_fields:
        sort_by = "created_at"
    
    db = SessionLocal()
    try:
        manager = create_manager(db)
        clusters = await manager.get_all_clusters()
        
        # 搜索过滤（匹配簇组名称或用户地址）
        if search:
            search_lower = search.lower()
            filtered = []
            for c in clusters:
                # 匹配簇组名称
                if search_lower in c.name.lower():
                    filtered.append(c)
                # 匹配用户地址
                elif any(search_lower in user.lower() for user in c.users):
                    filtered.append(c)
            clusters = filtered
        
        # 排序
        reverse = sort_order == "desc"
        def sort_key(c):
            if sort_by == "base_cu":
                return c.base_cu
            elif sort_by == "base_program_count":
                return c.base_program_count
            elif sort_by == "base_main_instruction_count":
                return c.base_main_instruction_count
            elif sort_by == "base_inner_instruction_count":
                return c.base_inner_instruction_count
            elif sort_by == "tx_count":
                return c.tx_count
            elif sort_by == "user_count":
                return c.user_count
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
                "total_txs": sum(c.tx_count for c in clusters),
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
        "txs": cluster.txs,
        "tx_count": cluster.tx_count,
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
        judgment_type = body.get("judgment_type", "manual")
        mint = body.get("mint", "")  # 需要传入 mint 才能调用 include_retail
        
        # 获取旧簇组信息
        old_cluster = await get_cluster(name)
        old_type = old_cluster.cluster_type if old_cluster else None
        
        manager = create_manager(db)
        await manager.set_cluster_type(name, new_cluster_type, judgment_type)
        
        # 如果从 dealer 改成其他类型，需要恢复数据
        if old_type == "dealer" and new_cluster_type != "dealer" and mint:
            # 从簇组中获取第一个用户的 address
            if old_cluster and old_cluster.users:
                address = old_cluster.users[0]
                from app.services.trade_processor import include_retail
                await include_retail(mint, address)
                logger.info(f"[簇组类型修改] {address[:8]}... 从庄家改回 {new_cluster_type}，已恢复数据")
        
        return JSONResponse({"message": "已更新"})
    finally:
        db.close()
