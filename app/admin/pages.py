"""SQLAdmin 自定义页面"""
from sqladmin import BaseView, expose
from fastapi import Request


class TradeMonitorView(BaseView):
    """交易监控页面 - 登录后默认首页"""
    name = "交易监控"
    icon = "fa-solid fa-chart-line"

    @expose("/trade-monitor", methods=["GET"])
    async def monitor_page(self, request: Request):
        return await self.templates.TemplateResponse(request, "trade_monitor.html")


class SettingsView(BaseView):
    """系统设置页面"""
    name = "系统设置"
    icon = "fa-solid fa-gear"

    @expose("/settings", methods=["GET"])
    async def settings_page(self, request: Request):
        return await self.templates.TemplateResponse(request, "settings.html")


class DealerSettingsView(BaseView):
    """庄家检测条件设置页面"""
    name = "庄家设置"
    icon = "fa-solid fa-user-shield"

    @expose("/dealer-settings", methods=["GET"])
    async def dealer_settings_page(self, request: Request):
        return await self.templates.TemplateResponse(request, "dealer_settings.html")


class ClusterSettingsView(BaseView):
    """簇组管理页面"""
    name = "簇组管理"
    icon = "fa-solid fa-layer-group"

    @expose("/cluster-settings", methods=["GET"])
    async def cluster_settings_page(self, request: Request):
        return await self.templates.TemplateResponse(
            request, 
            "cluster/cluster.html",
            {"request": request}
        )
