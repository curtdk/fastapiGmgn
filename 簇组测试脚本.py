"""簇组匹配测试脚本

用法：
  python 簇组测试脚本.py

测试数据来自：中文文档/簇组/簇组测试.md
"""
import json

# 测试数据 1 - SELL 交易（带 closeAccount）
TX1 = {
    "sig": "55ktQUvE4N945SgeXr5BcdRsxGt6A7FEACGDwPeqQMiBphAzUhY6epnAAYeXJ6z5guhXDfyh7YuLNtPwkCeQBzCX",
    "from_address": "BaeUFLwwEj5AySoXUD9DfrvwqWLsh3w3S9ZcYGzPXDLj",
    "cu_consumed": 51413,
    "instructions_count": 4,
    "inner_instructions_count": 3,
    "program_ids": "[\"ComputeBudget111111111111111111111111111111\", \"6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P\", \"TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb\", \"pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ\"]",
    "main_instructions": "[{\"index\": 0, \"program_id\": \"ComputeBudget111111111111111111111111111111\", \"type\": \"SetComputeUnitPrice\"}, {\"index\": 1, \"program_id\": \"ComputeBudget111111111111111111111111111111\", \"type\": \"SetComputeUnitLimit\"}, {\"index\": 2, \"program_id\": \"6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P\", \"type\": \"pump_type_51\"}, {\"index\": 3, \"program_id\": \"TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb\", \"type\": \"closeAccount\"}]",
}

# 测试数据 2 - SELL 交易（不带 closeAccount）
TX2 = {
    "sig": "2GGMpwG25FpudQiWKQj8PFRSTuT11qVTgHcNyiadXVgD5Np4bPfMqnuPHqh55fzdfrsCd7bXahLXN9ZSGmi8oSDC",
    "from_address": "BaeUFLwwEj5AySoXUD9DfrvwqWLsh3w3S9ZcYGzPXDLj",
    "cu_consumed": 49922,
    "instructions_count": 3,
    "inner_instructions_count": 3,
    "program_ids": "[\"ComputeBudget111111111111111111111111111111\", \"6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P\", \"pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ\", \"TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb\"]",
    "main_instructions": "[{\"index\": 0, \"program_id\": \"ComputeBudget111111111111111111111111111111\", \"type\": \"SetComputeUnitPrice\"}, {\"index\": 1, \"program_id\": \"ComputeBudget111111111111111111111111111111\", \"type\": \"SetComputeUnitLimit\"}, {\"index\": 2, \"program_id\": \"6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P\", \"type\": \"pump_type_51\"}]",
}

# 测试数据 3 - SELL 交易（不带 closeAccount）
TX3 = {
    "sig": "4czcreP3HYeKEFpsh2kM7s476rgr5cj6CWEcwFi59FqQF3Pqkh9voEp4Q65vDXnYVDynr2KtkDvXQ8etjyqUrAic",
    "from_address": "BaeUFLwwEj5AySoXUD9DfrvwqWLsh3w3S9ZcYGzPXDLj",
    "cu_consumed": 49953,
    "instructions_count": 3,
    "inner_instructions_count": 3,
    "program_ids": "[\"ComputeBudget111111111111111111111111111111\", \"6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P\", \"pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ\", \"TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb\"]",
    "main_instructions": "[{\"index\": 0, \"program_id\": \"ComputeBudget111111111111111111111111111111\", \"type\": \"SetComputeUnitPrice\"}, {\"index\": 1, \"program_id\": \"ComputeBudget111111111111111111111111111111\", \"type\": \"SetComputeUnitLimit\"}, {\"index\": 2, \"program_id\": \"6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P\", \"type\": \"pump_type_51\"}]",
}


class MockClusterSettings:
    """模拟簇组设置"""
    def __init__(self):
        self.enabled = True
        self.match_cu_enabled = True
        self.match_program_enabled = True
        self.match_main_instruction_enabled = True
        self.match_inner_instruction_enabled = True
        self.cu_offset = 2000
        self.program_offset = 1
        self.main_instruction_offset = 1
        self.inner_instruction_offset = 1


class MockCluster:
    """模拟簇组"""
    def __init__(self, name, base_cu, base_cu_offset, base_program_count, base_program_offset,
                 base_main_instruction_count, base_main_offset, base_inner_instruction_count, base_inner_offset):
        self.name = name
        self.enabled = True
        self.base_cu = base_cu
        self.base_cu_offset = base_cu_offset
        self.base_program_count = base_program_count
        self.base_program_offset = base_program_offset
        self.base_main_instruction_count = base_main_instruction_count
        self.base_main_offset = base_main_offset
        self.base_inner_instruction_count = base_inner_instruction_count
        self.base_inner_offset = base_inner_offset


def extract_features(tx_detail):
    """提取交易特征"""
    programs = json.loads(tx_detail["program_ids"])
    return {
        "cu": tx_detail["cu_consumed"],
        "program_count": len(programs),
        "main_instruction_count": tx_detail["instructions_count"],
        "inner_instruction_count": tx_detail["inner_instructions_count"],
    }


def match_cluster(settings, cluster, features):
    """测试匹配逻辑"""
    conditions_passed = []
    conditions_failed = []
    
    # CU
    if settings.match_cu_enabled:
        cu_min = cluster.base_cu - cluster.base_cu_offset
        cu_max = cluster.base_cu + cluster.base_cu_offset
        if cu_min <= features["cu"] <= cu_max:
            conditions_passed.append(f"CU: {features['cu']} 在 [{cu_min}, {cu_max}] ✓")
        else:
            conditions_failed.append(f"CU: {features['cu']} 不在 [{cu_min}, {cu_max}] ✗")
    
    # 程序数
    if settings.match_program_enabled:
        prog_min = cluster.base_program_count - cluster.base_program_offset
        prog_max = cluster.base_program_count + cluster.base_program_offset
        if prog_min <= features["program_count"] <= prog_max:
            conditions_passed.append(f"程序数: {features['program_count']} 在 [{prog_min}, {prog_max}] ✓")
        else:
            conditions_failed.append(f"程序数: {features['program_count']} 不在 [{prog_min}, {prog_max}] ✗")
    
    # 主指令数
    if settings.match_main_instruction_enabled:
        main_min = cluster.base_main_instruction_count - cluster.base_main_offset
        main_max = cluster.base_main_instruction_count + cluster.base_main_offset
        if main_min <= features["main_instruction_count"] <= main_max:
            conditions_passed.append(f"主指令: {features['main_instruction_count']} 在 [{main_min}, {main_max}] ✓")
        else:
            conditions_failed.append(f"主指令: {features['main_instruction_count']} 不在 [{main_min}, {main_max}] ✗")
    
    # 内部指令数
    if settings.match_inner_instruction_enabled:
        inner_min = cluster.base_inner_instruction_count - cluster.base_inner_offset
        inner_max = cluster.base_inner_instruction_count + cluster.base_inner_offset
        if inner_min <= features["inner_instruction_count"] <= inner_max:
            conditions_passed.append(f"内部指令: {features['inner_instruction_count']} 在 [{inner_min}, {inner_max}] ✓")
        else:
            conditions_failed.append(f"内部指令: {features['inner_instruction_count']} 不在 [{inner_min}, {inner_max}] ✗")
    
    if conditions_failed:
        return False, conditions_passed, conditions_failed
    
    if not conditions_passed:
        return False, [], ["没有开启任何匹配条件"]
    
    return True, conditions_passed, conditions_failed


def main():
    print("=" * 60)
    print("簇组匹配测试")
    print("=" * 60)
    
    settings = MockClusterSettings()
    
    # 创建基准簇组（使用 TX2 的特征作为基准）
    cluster = MockCluster(
        name="BaeUFLwwEj5AySoXUD9DfrvwqWLsh3w3S9ZcYGzPXDLj",
        base_cu=49922,
        base_cu_offset=2000,
        base_program_count=4,
        base_program_offset=1,
        base_main_instruction_count=3,
        base_main_offset=1,
        base_inner_instruction_count=3,
        base_inner_offset=1,
    )
    
    print(f"\n基准簇组：{cluster.name[:16]}...")
    print(f"  CU: {cluster.base_cu} ± {cluster.base_cu_offset}")
    print(f"  程序数: {cluster.base_program_count} ± {cluster.base_program_offset}")
    print(f"  主指令数: {cluster.base_main_instruction_count} ± {cluster.base_main_offset}")
    print(f"  内部指令数: {cluster.base_inner_instruction_count} ± {cluster.base_inner_offset}")
    print()
    
    test_txs = [
        ("TX1 (带closeAccount)", TX1),
        ("TX2 (不带closeAccount)", TX2),
        ("TX3 (不带closeAccount)", TX3),
    ]
    
    for name, tx in test_txs:
        print(f"\n{'='*60}")
        print(f"测试: {name}")
        print(f"  Sig: {tx['sig'][:16]}...")
        features = extract_features(tx)
        print(f"  特征:")
        print(f"    CU: {features['cu']}")
        print(f"    程序数: {features['program_count']}")
        print(f"    主指令数: {features['main_instruction_count']}")
        print(f"    内部指令数: {features['inner_instruction_count']}")
        
        is_matched, passed, failed = match_cluster(settings, cluster, features)
        
        print(f"\n  匹配结果: {'✓ 匹配' if is_matched else '✗ 不匹配'}")
        
        if passed:
            print(f"  通过条件:")
            for p in passed:
                print(f"    {p}")
        
        if failed:
            print(f"  失败条件:")
            for f in failed:
                print(f"    {f}")


if __name__ == "__main__":
    main()