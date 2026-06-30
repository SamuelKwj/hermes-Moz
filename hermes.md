# voice_desktop 工具权限修复

## 问题

语音桌面端（voice_desktop）通过 Hermes API（8642端口）调用 LLM 时，模型回复"我没有工具"，
无法执行创建文件夹、打开程序等系统操作。

## 根因

`backend/hermes_client.py` 的 `chat()` 和 `chat_stream()` 方法向 Hermes API 发送请求时，
请求体缺少 `tools` 字段。Hermes API 服务器虽然内部自带完整的工具执行循环，
但需要请求中明确声明工具定义才会启用工具调用。

DeepSeek V4 Pro 模型行为严谨：请求中没看到 `tools` 定义就不会尝试调用工具。
而 Flash 版模型更"热心"，即使没有显式工具定义也可能尝试执行操作，
这就是同一份代码在另一台电脑（Flash 模型）上可用的原因。

## 修改内容

**文件：** `D:\project\voice_desktop\backend\hermes_client.py`

### 1. 新增 `HERMES_TOOLS` 常量（第17-90行）

定义了4个工具，使用 OpenAI function calling 格式：

| 工具 | 用途 | 参数 |
|------|------|------|
| `terminal` | 执行命令行操作 | `command` (string) |
| `read_file` | 读取文件内容 | `path` (string) |
| `write_file` | 创建或覆盖写入文件 | `path`, `content` (string) |
| `web_search` | 网上搜索信息 | `query` (string) |

### 2. `chat()` 方法（第112-113行）

请求体增加：
```python
"tools": HERMES_TOOLS,
"tool_choice": "auto",
```

### 3. `chat_stream()` 方法（第164-165行）

同上。

## 工作原理

```
voice_desktop → POST /v1/chat/completions (带 tools)
                      ↓
              Hermes API Server (8642)
                      ↓
              创建 AIAgent（加载 tools）
                      ↓
              run_conversation() 完整工具循环
                      ↓
              返回最终文本响应
```

Hermes API 服务器内部处理整个工具调用循环（模型决策 → 工具执行 → 结果回传 → 模型最终回复），
voice_desktop 只需在请求中声明工具即可，无需自己实现工具执行逻辑。

## 不影响的部分

- 系统提示词未修改（仍为中文桌面语音助手）
- 响应处理逻辑未修改（API 返回的仍是最终文本）
- 历史对话处理未修改
- settings.json 配置未修改

## 验证方法

重启 voice_desktop 后，语音输入"在桌面建一个叫测试的文件夹"，
模型应能调用 terminal 工具创建文件夹，而非回复"没有工具"。
