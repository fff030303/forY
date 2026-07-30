# Persona Companion

一个从历史聊天记录构建对话人格、长期记忆与持续反馈闭环的虚拟陪伴产品。

目前已完成：

- `GET /health`：健康检查。
- `POST /chat`：接收聊天消息并返回固定回复。

## 当前目录

```text
apps/api/
├── app/
│   └── main.py
├── tests/
│   ├── test_health.py
│   └── test_chat.py
└── requirements.txt
```

## 本地启动

在项目根目录执行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r apps/api/requirements.txt
cd apps/api
uvicorn app.main:app --reload
```

打开：

- 健康检查：http://127.0.0.1:8000/health
- API 文档：http://127.0.0.1:8000/docs

预期健康检查结果：

```json
{"status":"ok"}
```

聊天接口示例：

```bash
curl -X POST http://127.0.0.1:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"你好"}'
```

预期结果：

```json
{"reply":"你好呀"}
```

## 运行测试

进入 `apps/api` 后执行：

```bash
pytest
```
