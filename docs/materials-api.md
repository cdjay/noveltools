# 书籍资料库接口

默认服务地址为 `http://127.0.0.1:8000`，交互式文档见 `/docs`。所有路径以 `/api/books/{book_id}/materials` 开头，按书籍与资料标识隔离。

当前支持写作规则与普通文本资料，作者手动维护，不调用 LLM，也不自动加入当前状态速查的模型请求。人物资料和动态记忆的专门流程、写前上下文组装属于后续阶段。

## 读取写作规则

`GET /api/books/{book_id}/materials/writing-rules`

`writing-rules` 是固定标识。尚未填写时返回 `version: null` 和可选填写模板，读取不会创建数据库记录。模板不是作者已采用的规则。

```json
{
  "schema_version": 1,
  "book_id": 1,
  "material_key": "writing-rules",
  "title": "写作规则",
  "format": "markdown",
  "version": {
    "id": 12,
    "book_id": 1,
    "material_key": "writing-rules",
    "version_number": 2,
    "title": "写作规则",
    "content": "## 必须遵守\n使用第三人称。",
    "source": "manual",
    "note": "",
    "created_at": "2026-09-08T02:00:00"
  },
  "template": "# 写作规则\n……"
}
```

`version.content` 是已保存的原始全文；显示方式切换不转换存储内容。时间是无时区后缀的 UTC 时间。`source` 为 `manual` 或 `restored`；恢复同时恢复旧标题与正文，并追加版本。

## 其他路由

以下路径相对于资料库前缀，目录接口不加末尾斜杠：

| 方法与路径 | 用途与请求体 |
| --- | --- |
| `GET` 前缀本身 | 本书资料目录，写作规则置顶；含标题、稳定标识、最新版本 ID/编号，不含全文 |
| `POST` 前缀本身 | 创建普通资料并保存第一版，提交 `{"title":"…","content":"…"}`；正文可省略 |
| `GET /{material_key}` | 最新已保存全文和版本信息 |
| `GET /{material_key}/export.md` | 直接提取原文，返回 `text/markdown`，`X-Material-Version` 响应头为版本号 |
| `GET /{material_key}/versions` | 历史元数据列表，不含全文 |
| `GET /{material_key}/versions/{version_id}` | 完整历史版本 |
| `POST /{material_key}/versions` | 保存新版本，提交 `{"title":"…","content":"…","base_version_id":12}` |
| `POST /{material_key}/versions/{version_id}/restore` | 恢复旧版，提交 `{"base_version_id":12}` |

普通资料标识由服务生成，例如 `note-…`，从创建响应或目录获取，不使用标题猜测。重命名后标识不变；写作规则标题固定。普通标题首尾空白会去除，不能为空，最多 120 字符。

保存与恢复必须携带调用方所持最新版本 ID。写作规则首次保存传 `null`；普通资料从创建响应取得第一版 ID。并发更新返回 409，保留原稿。未知资料或越界版本返回 404；尚未保存的写作规则导出 Markdown 返回 404。空正文可以保存，仍保留历史。

## 读取示例

```python
import json
from urllib.request import urlopen

book_id = 1
with urlopen(f"http://127.0.0.1:8000/api/books/{book_id}/materials/writing-rules") as response:
    rules = json.load(response)
saved_rules = rules["version"]["content"] if rules["version"] else ""
```

页面提供每份资料的 JSON 和 Markdown 地址及复制按钮。其他项目的后端可以直接读取；浏览器跨来源访问仍使用 `.env` 的 `CORS_ORIGINS`。接口不返回未保存草稿或 LLM 密钥。

## 备份和范围

书籍备份格式升级为 v3，新增 `material_versions`，保存每份资料所有标题与全文历史。恢复创建新书籍和新的数据库版本 ID，保留资料标识与版本编号。v1、v2 备份仍可导入，资料缺失等同于尚未填写。

预览返回 `materials`（已保存资料数）与 `material_versions`（历史总数）。默认但未保存的写作规则不计数。重复的“资料标识＋版本编号”和无效标题、标识会在写入前拒绝。删除整本书会级联删除其资料版本。

本阶段没有独立资料删除入口、专门人物卡和记忆检索，也不自动生成、提炼或改写规则。
