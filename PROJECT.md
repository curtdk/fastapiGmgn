# fastapiGmgn 项目资料

## 基本信息
- **项目名称**: fastapiGmgn
- **项目路径**: /Users/curtdk/.openclaw/workspace/fastapiGmgn
- **创建时间**: 2024年4月
- **最后更新**: 2026-06-05

## 目录结构

```
fastapiGmgn/
├── app/                    # 主应用
│   ├── admin/             # 管理后台
│   ├── jup/              # Jupyter相关
│   ├── models/            # 数据模型
│   ├── routes/            # 路由
│   ├── schemas/           # Schemas
│   ├── services/         # 服务层
│   ├── taskcl/          # 任务处理
│   ├── templates/        # 模板
│   ├── utils/            # 工具
│   └── websocket/        # WebSocket
├── dk/                   # 自定义模块
├── docs/                 # 文档
├── logs/                 # 日志
├── tests/                # 测试
├── 庄家测试/             # 庄家测试脚本
├── 庄家测试2/            # 庄家测试2
├── 中文文档/              # 中文文档
│
├── main.py               # 主入口
├── requirements.txt     # 依赖
├── gmgn.db              # SQLite数据库
├── .env                # 环境变量
└── README.md            # 说明
```

## 技术栈
- FastAPI
- SQLAlchemy
- SQLite
- WebSocket
- JWT认证

## 接口模块 (routes/)
- 业务相关的API路由

## 服务层 (services/)
- 核心业务逻辑

## 数据库
- SQLite: gmgn.db (~220MB)

## 环境变量 (.env)
- 配置敏感信息

## 相关命令
```bash
cd /Users/curtdk/.openclaw/workspace/fastapiGmgn
source venv/bin/activate
python main.py
```

## 测试脚本
- test_batch_tx.py
- test_fetch_first_tx.py
- test_wallet_tx.py
- 簇组测试脚本.py