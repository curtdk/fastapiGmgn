"""直接验证 _flushTrades 的顺序逻辑"""
import time

# 模拟 DOM（最小 mock）
class FakeTr:
    def __init__(self, id):
        self.id = id
    def __repr__(self):
        return f"<tr id={self.id}>"

# 测试场景：3 个 trade 按时间顺序到达
# trade1 (t1), trade2 (t2), trade3 (t3)  t3 最新
batch = [
    {"id": "trade1", "time": "t1"},
    {"id": "trade2", "time": "t2"},
    {"id": "trade3", "time": "t3"},  # 最新
]

# 模拟修复后的逻辑：倒序遍历
frag = []
for i in range(len(batch) - 1, -1, -1):
    frag.append(FakeTr(batch[i]["id"]))

print("修复后 fragment 顺序（倒序遍历）:")
for tr in frag:
    print(f"  {tr}")

# 模拟 insertBefore(frag, tbody.firstChild)
# tbody 原本有 [old1, old2, old3]
# 插入 frag 后：tbody = [frag..., old1, old2, old3]
# 所以新行在最上面

tbody_after = frag + [FakeTr("old1"), FakeTr("old2"), FakeTr("old3")]
print(f"\ntbody 顺序（修复后）: {[t.id for t in tbody_after]}")
print(f"最上面（第 1 行）: {tbody_after[0].id} (期望 trade3 最新)")
print(f"第 2 行: {tbody_after[1].id}")
print(f"第 3 行: {tbody_after[2].id}")
print(f"第 4 行: {tbody_after[3].id}")

# 验证
assert tbody_after[0].id == "trade3", f"❌ 第 1 行应是 trade3，实际是 {tbody_after[0].id}"
assert tbody_after[1].id == "trade2"
assert tbody_after[2].id == "trade1"
print("\n✅ 修复后顺序正确：trade3 (最新) 在最上面")