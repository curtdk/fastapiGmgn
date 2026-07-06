"""
测试 backfill 模式切换：0=正式，2=测试
"""
import sys
sys.path.insert(0, "/Users/curtdk/.openclaw/workspace/fastapiGmgn")

from app.services.trade_processor import (
    set_backfill_broadcast_mode,
    get_backfill_broadcast_mode,
    _should_broadcast_during_backfill,
)


def test_mode_switch():
    print("=== Backfill 模式切换测试 ===\n")

    # 默认 0
    set_backfill_broadcast_mode(0)
    assert get_backfill_broadcast_mode() == 0, "默认应为 0"
    assert _should_broadcast_during_backfill() == False, "模式 0 不应广播"
    print("✅ 默认模式 0：backfill 不广播")

    # 切到 2
    set_backfill_broadcast_mode(2)
    assert get_backfill_broadcast_mode() == 2
    assert _should_broadcast_during_backfill() == True, "模式 2 应广播"
    print("✅ 模式 2：backfill 期间也广播")

    # 切回 0
    set_backfill_broadcast_mode(0)
    assert get_backfill_broadcast_mode() == 0
    assert _should_broadcast_during_backfill() == False
    print("✅ 切回模式 0：恢复不广播")

    print("\n🎉 全部通过")


if __name__ == "__main__":
    test_mode_switch()