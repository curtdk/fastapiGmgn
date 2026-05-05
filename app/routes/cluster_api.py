"""簇组管理 API 路由"""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/admin", tags=["簇组管理"])


@router.get("/api/clusters")
async def api_get_clusters(request: Request):
    """获取所有簇组"""
    from app.services.cluster.manager import create_manager
    from app.utils.database import SessionLocal
    db = SessionLocal()
    try:
        manager = create_manager(db)
        clusters = await manager.get_all_clusters()
        return JSONResponse([c.to_dict() for c in clusters])
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


@router.put("/api/clusters/{name}/type")
async def api_update_cluster_type(name: str, request: Request):
    """修改簇组类型（手动锁定）"""
    from app.services.cluster.manager import create_manager
    from app.utils.database import SessionLocal
    from urllib.parse import unquote
    name = unquote(name)
    db = SessionLocal()
    try:
        body = await request.json()
        cluster_type = body.get("cluster_type", "undefined")
        judgment_type = body.get("judgment_type", "manual")
        manager = create_manager(db)
        await manager.set_cluster_type(name, cluster_type, judgment_type)
        return JSONResponse({"message": "已更新"})
    finally:
        db.close()