# SQLAdmin BaseView 菜单问题说明

## 问题描述

在 SQLAdmin 中使用 `BaseView` 创建自定义视图时，**每个 BaseView 都会自动在左侧菜单栏生成一个菜单项**。

假设创建一个 `ClusterApiView`：

```python
class ClusterApiView(BaseView):
    name = "簇组API"
    icon = "fa-solid fa-layer-group"

    @expose("/api/clusters", methods=["GET"])
    async def api_get_clusters(self, request: Request):
        ...
```

结果：
- 菜单会显示一个新项 **"簇组API"**
- 该菜单项的默认跳转地址是 `/admin/api/clusters`（取第一个 `@expose` 的路径）

如果只想让 `ClusterSettingsMenu` 作为菜单项，但不希望有额外的 API 菜单项，就会产生冲突。

## 解决方案

**核心原则**：页面用 BaseView，API 用 FastAPI Router。

| 场景 | 使用方式 | 是否生成菜单 |
|------|---------|-------------|
| 页面 + 内嵌 API | SQLAdmin BaseView | ✅ 会生成 |
| 独立 API 端点 | FastAPI Router | ❌ 不会生成 |

---

## 具体改动

### 1. 新建文件：`app/routes/cluster_api.py`

**原来的问题代码**（在 main.py 中的 ClusterApiView）：
```python
class ClusterApiView(BaseView):
    """❌ 这样做会生成菜单项"""
    name = "簇组API"
    icon = "fa-solid fa-layer-group"

    @expose("/api/clusters", methods=["GET"])
    async def api_get_clusters(self, request: Request):
        ...
```

**修改后**（新建 FastAPI 路由文件）：
```python
# app/routes/cluster_api.py
"""簇组管理 API 路由"""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/admin", tags=["簇组管理"])

@router.get("/api/clusters")
async def api_get_clusters(request: Request):
    """获取所有簇组"""
    ...

@router.delete("/api/clusters/{name}")
async def api_delete_cluster(name: str):
    """删除簇组"""
    ...

@router.put("/api/clusters/{name}/type")
async def api_update_cluster_type(name: str, request: Request):
    """修改簇组类型"""
    ...
```

### 2. 修改文件：`main.py`

**删除的内容**：
```python
# ❌ 删除这些代码：
from app.admin.cluster_api import ClusterApiView  # 删除导入

class ClusterApiView(BaseView):  # 删除整个类
    name = "簇组API"
    @expose("/api/clusters", methods=["GET"])
    async def api_get_clusters(self, request: Request):
        ...

admin.add_base_view(ClusterApiView)  # 删除注册
```

**新增的内容**：
```python
# ✅ main.py 中添加：
from app.routes.cluster_api import router as cluster_api_router  # 新增导入

# 在路由注册部分添加：
app.include_router(cluster_api_router)  # 簇组 API 路由

# admin.add_base_view 保持不变：
admin.add_base_view(ClusterSettingsMenu)  # 簇组管理页面
```

### 3. 修改文件：`cluster.html` 中的 API 路径

**保持不变**（API 路径不变）：
```javascript
async function loadClusters() {
  const resp = await fetch('/admin/api/clusters');  // ✅ 保持原样
  ...
}

await fetch(`/admin/api/clusters/${encodeURIComponent(name)}/type`, {...})  // ✅ 保持原样
await fetch(`/admin/api/clusters/${encodeURIComponent(name)}`, {...})       // ✅ 保持原样
```

---

## 总结

| 文件 | 操作 | 说明 |
|------|------|------|
| `app/routes/cluster_api.py` | **新建** | FastAPI 路由处理 API 请求 |
| `main.py` | **删除** ClusterApiView 类 | 移除 BaseView 方式的 API |
| `main.py` | **添加** import 和 include_router | 注册 FastAPI 路由 |
| `cluster.html` | **无需修改** | API 路径保持不变 |

## 总结

- **BaseView**：适合页面展示，可能包含少量内嵌 API
- **FastAPI Router**：适合独立的、复杂的 API 端点，不会污染菜单

如果 API 逻辑简单且只在当前页面使用，可以保留在 BaseView 中；如果需要分离或避免菜单干扰，使用 FastAPI Router 是更好的选择。