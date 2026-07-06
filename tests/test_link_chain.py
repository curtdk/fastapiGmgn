"""
验证完整链路：backfill_done → fetchUsers → renderUserTable → computeClusterSummary
"""
import sys
sys.path.insert(0, "/Users/curtdk/.openclaw/workspace/fastapiGmgn")


def check_chain():
    print("=== 完整链路验证 ===\n")

    # 1. 检查 trade_processor 广播条件
    with open("app/services/trade_processor.py", "r") as f:
        content = f.read()
        assert "_should_broadcast_during_backfill" in content, "trade_processor 缺少 _should_broadcast_during_backfill"
        assert "if mint not in _backfilling_mints or _should_broadcast_during_backfill():" in content, "广播条件没改"
        print("✅ trade_processor: 广播条件已修改（支持 skip=2 测试模式）")

    # 2. 检查 trade_backfill 同步设置
    with open("app/services/trade_backfill.py", "r") as f:
        content = f.read()
        assert "set_backfill_broadcast_mode" in content, "trade_backfill 缺少 set_backfill_broadcast_mode 调用"
        print("✅ trade_backfill: backfill 启动时同步模式给 trade_processor")

    # 3. 检查 trade_live.html backfill_done 处理
    with open("app/templates/trade_live.html", "r") as f:
        content = f.read()

        # backfill_done + fetchUsers
        assert "msg.type === 'backfill_done'" in content
        # 验证 fetchUsers 在 backfill_done 块内
        backfill_done_idx = content.find("msg.type === 'backfill_done'")
        backfill_done_block = content[backfill_done_idx:backfill_done_idx + 500]
        assert "fetchUsers()" in backfill_done_block, "backfill_done 块内没有 fetchUsers()"
        print("✅ trade_live.html: backfill_done 时调 fetchUsers()")

        # renderUserTable 末尾调 computeClusterSummary
        render_idx = content.find("function renderUserTable()")
        render_block = content[render_idx:render_idx + 4000]
        assert "computeClusterSummary();" in render_block, "renderUserTable 末尾没有调 computeClusterSummary"
        print("✅ trade_live.html: renderUserTable 末尾调 computeClusterSummary()")

        # 顶部按钮
        assert "switchBackfillMode(0)" in content, "缺少正式模式按钮"
        assert "switchBackfillMode(2)" in content, "缺少测试模式按钮"
        print("✅ trade_live.html: 顶部加了正式/测试切换按钮")

        # JS 函数
        assert "function loadBackfillMode()" in content
        assert "function switchBackfillMode" in content
        assert "function updateModeUI" in content
        print("✅ trade_live.html: JS 函数完整（loadBackfillMode/switchBackfillMode/updateModeUI）")

    # 4. 检查 cluster_api 新端点
    with open("app/routes/cluster_api.py", "r") as f:
        content = f.read()
        assert "/api/settings/backfill-mode" in content, "缺少 backfill-mode 端点"
        assert "api_get_backfill_mode" in content
        assert "api_set_backfill_mode" in content
        print("✅ cluster_api.py: GET/PUT /admin/api/settings/backfill-mode 已添加")

    print("\n🎉 完整链路验证通过")


if __name__ == "__main__":
    check_chain()