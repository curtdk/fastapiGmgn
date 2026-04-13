================================================================================
SIG 顺序验证测试 - Mint: 9hK5hP651A7GSwR7jdBD7SFoo5ut4BYLeCj1LhBipump
测试时间: 2026-04-13T22:32:42.314597
================================================================================

【1. 数量对比】
  RPC 总签名数: 11
  RPC 成功交易: 10
  RPC 失败交易: 1（被系统正确跳过）
  数据库记录数: 10
  差异（成功交易 - DB）: 0

【2. 完整性检查 - 成功的 RPC 签名是否全部入库】
  通过: 所有成功的 RPC 签名均已入库
  通过: 数据库无多余签名

  被跳过的失败交易:
    - 4FdHdEtd5U48q2TH...  slot=412945911  err={'InstructionError': [3, {'Custom': 2006}]}

【3. 顺序对比（从新到旧）】
  交集签名数: 10
  顺序是否一致: 是

【4. 数据库 Slot 单调性检查】
  降序单调性: 通过
  升序单调性: 通过

【5. Slot 对应关系（前 10 条）】
    #         RPC Slot          DB Slot      一致           RPC Sig            DB Sig
  ---  ---------------  ---------------  ------  ----------------  ----------------
    1        412946134        412946134      OK  3dMxFn69emiB9dTM  3dMxFn69emiB9dTM
    2        412945984        412945984      OK  3MmtAfD1QC9LV3P2  3MmtAfD1QC9LV3P2
    3        412945983        412945983      OK  2BHhZdfpJ6xeLJqH  2BHhZdfpJ6xeLJqH
    4        412945982        412945982      OK  4sehNRB3x77RVH1j  4sehNRB3x77RVH1j
    5        412945945        412945945      OK  4TSu3QwuSKuXZMCB  4TSu3QwuSKuXZMCB
    6        412945935        412945935      OK  bKV5rHWZ7WKoiQyN  bKV5rHWZ7WKoiQyN
    7        412945912        412945912      OK  45QAqSzPNmKmJ7tr  45QAqSzPNmKmJ7tr
    8        412945909        412945909      OK  5NYf3skyMFz3XvSf  5NYf3skyMFz3XvSf
    9        412945908        412945908      OK  3QyV8awypmDhpTXn  3QyV8awypmDhpTXn
   10        412945908        412945908      OK  x36NPugfsVRsMfQw  x36NPugfsVRsMfQw

【6. WSS 实时写入验证】
  按 source 分类:
  回填入库记录数 (solana_rpc): 10
  WSS 入库记录数 (helius_ws): 0
  无 WSS 记录（未启动实时流）

================================================================================
【总结】
  完整性: 通过
  顺序一致性: 通过
  Slot 单调性: 通过
  失败交易过滤: 通过
  总体: 全部通过
================================================================================