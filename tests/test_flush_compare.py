"""对比：修复前 vs 修复后"""

class FakeTr:
    def __init__(self, id):
        self.id = id
    def __repr__(self):
        return f"<tr id={self.id}>"

batch = [
    {"id": "trade1", "time": "t1"},
    {"id": "trade2", "time": "t2"},
    {"id": "trade3", "time": "t3"},  # 最新
]

# === 修复前：正序遍历 ===
frag_before = []
for data in batch:
    frag_before.append(FakeTr(data["id"]))

tbody_before = frag_before + [FakeTr("old1"), FakeTr("old2"), FakeTr("old3")]
print(f"修复前 tbody 顺序: {[t.id for t in tbody_before]}")
print(f"  最上面: {tbody_before[0].id} ← 最早的！bug")

# === 修复后：倒序遍历 ===
frag_after = []
for i in range(len(batch) - 1, -1, -1):
    frag_after.append(FakeTr(batch[i]["id"]))

tbody_after = frag_after + [FakeTr("old1"), FakeTr("old2"), FakeTr("old3")]
print(f"\n修复后 tbody 顺序: {[t.id for t in tbody_after]}")
print(f"  最上面: {tbody_after[0].id} ← 最新的！✅")

# 总结
print(f"\n=== 总结 ===")
print(f"修复前：trade1(最早) 在最上面，新交易反着排 → 用户看到的是反的")
print(f"修复后：trade3(最新) 在最上面，符合用户预期")