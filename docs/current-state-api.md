# 当前状态速查接口

默认服务地址：`http://127.0.0.1:8000`。交互式接口文档：`/docs`，OpenAPI：`/openapi.json`。

接口按书籍 ID 隔离。可先使用 `GET /api/books` 获取书籍 ID。只有已保存版本可以被其他项目读取，页面未保存草稿不会泄入接口。

## 读取最新速查

`GET /api/books/{book_id}/current-state`

响应示例（字段值仅为说明）：

```json
{
  "schema_version": 1,
  "book_id": 1,
  "format": "markdown",
  "version": {
    "id": 12,
    "book_id": 1,
    "version_number": 3,
    "content": "## 当前进度\n\n……",
    "source": "llm",
    "note": "最后五章正文与全部已有章节梗概",
    "model": "your-model-id",
    "prompt": "生成此版本时使用的提示词",
    "source_manifest": {
      "book_title": "小说名",
      "chapters": [],
      "body_chapter_ids": [16, 17, 18, 19, 20],
      "missing_summary_chapter_ids": [],
      "stale_summary_chapter_ids": []
    },
    "created_at": "2026-09-08T08:00:00"
  },
  "is_stale": false,
  "generating": false,
  "source_error": null
}
```

- `version: null` 表示这本书尚无速查，不是空白版本。
- `content` 是可直接交给其他项目使用的 Markdown 原文。
- `source` 为 `llm`、`manual` 或 `restored`。
- `source_manifest.chapters` 包含当时每章的 ID、编号、标题、正文版本 ID、梗概来源版本 ID 与摘要哈希；最近五章还包含正文哈希。它不包含完整章节正文或 LLM 密钥。
- `is_stale` 表示当前取材与保存的来源记录不一致。章节增删、排序、标题、正文版本及梗概变化均可能导致过时。手动编辑和恢复旧稿不会自动消除来源过时标记。
- `source_error` 表示当前来源正文缺失等问题；已有速查仍返回，且标记过时。
- `generating` 表示本书正在发起模型调用。它不代表已有版本已经更新。
- 时间遵循项目现有约定，返回无时区后缀的 UTC 时间。

Python 读取示例（仅用标准库）：

```python
import json
from urllib.request import urlopen

book_id = 1
with urlopen(f"http://127.0.0.1:8000/api/books/{book_id}/current-state") as response:
    state = json.load(response)
if state["version"] is None:
    raise RuntimeError("尚无当前状态速查")
if state["is_stale"]:
    raise RuntimeError("状态速查来源已变化，请先核对或重新生成")
markdown = state["version"]["content"]
```

## 直接提取 Markdown

`GET /api/books/{book_id}/current-state/export.md`

返回 `text/markdown` 正文。响应头 `X-State-Version` 为版本号，`X-State-Stale` 为 `true` 或 `false`。没有速查时返回 404。需要判断来源是否可靠的调用方建议使用 JSON 接口。

## 其他接口

以下路径均以 `/api/books/{book_id}/current-state` 为前缀：

| 方法与路径 | 用途 | 请求体 |
| --- | --- | --- |
| `GET /context` | 最近五章范围、全部梗概数量、缺失/过时梗概 ID、输入字符数、当前模型及最大输出 Tokens | 无 |
| `GET /settings` | 读取本书提示词、配置修订号与默认提示词 | 无 |
| `PUT /settings` | 修改本书提示词 | `{"prompt":"…","base_revision":0}` |
| `POST /generate` | 显式调用模型并保存新速查版本 | `{"base_version_id":null}` |
| `GET /versions` | 历史列表；不包含全文、生成提示词和来源明细 | 无 |
| `GET /versions/{version_id}` | 读取完整历史版本 | 无 |
| `POST /versions` | 手动保存新版本 | `{"content":"…","base_version_id":12}` |
| `POST /versions/{version_id}/restore` | 恢复旧稿为新版本，保留其来源信息 | `{"base_version_id":12}` |

所有保存、生成、恢复必须传入调用方当前持有的最新版本 ID；首次无版本时传 `null`。提示词首次保存的 `base_revision` 为 `0`，之后使用读取到的修订号。过时编辑返回 409。

## 生成约定

按当前章节编号排序，取最后五章的当前已保存正文；不足五章时全部发送。再发送全书所有非空的当前章节梗概，不发送其他书籍、大纲或未保存草稿，也不会自动补生成缺失梗概。

正文与梗概作为 JSON 资料放入用户消息，本书保存的提示词放入系统消息。资料不会静默截断。全局启用的 LLM 配置决定模型、输出 Tokens 和超时；容量不足或超时会明确失败，原有速查保留。

同书重复生成返回 409。模型请求期间不持有数据库写事务；返回后重新检查正文、梗概及速查版本，变化时拒绝写入。空输出、截断输出或模型连接错误不保存为新版本。

上游 HTTP 429 根据可识别错误分类显示并发、Token 用量或额度限制；服务提供有效 `Retry-After` 时显示等待时间，不自动重发。没有具体原因时保留一般限流提示，不把所有 429 都当作输入过长或配置错误。错误分类不会回显上游任意正文和密钥。

`/context` 的 `max_output_tokens` 是当前模型的输出上限；`input_characters` 是输入资料 JSON 的字符数，两者均不是模型上下文容量，也不能直接相加作为 Token 用量。连接测试只发送短句且最多输出 10 Tokens，其成功不保证真实长文本请求成功。

生成是一次可等待的 HTTP 请求。网页离开后返回可查看保存结果；服务中断时不自动重试，重新启动会清理运行标记。调用方若连接中断，应先读取最新状态与 `generating`，确认上一请求结果后再重试。

## 接入与备份

接口沿用本地服务配置，不回传 API Key。其他项目的后端进程可直接调用；浏览器跨端口访问时，在本项目 `.env` 的 `CORS_ORIGINS` 中加入调用页面的完整来源并重启服务，保留已有来源。示例：

```dotenv
CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000
```

书籍备份格式升级为版本 2，包含全部速查版本和本书提示词。恢复时重映射来源章节、正文版本 ID。版本 1 备份仍可导入，恢复后的书籍暂时没有状态速查。历史来源已删除的记录保持无法对应状态，不会被认作最新资料。
