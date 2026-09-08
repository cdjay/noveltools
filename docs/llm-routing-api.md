# LLM 模块分流

LLM 配置分为“模型列表”和“模块分流”。分流属于全局应用配置，不按书籍区分；书籍备份不包含模型连接、密钥或分流，完整数据库备份包含这些配置。

## 当前模块

| 标识 | 功能 | 调用入口 |
| --- | --- | --- |
| `chapter_summary` | 单章梗概 | `POST /api/chapters/{chapter_id}/summary` |
| `batch_summary` | 批量梗概 | 持久化梗概任务创建、继续；兼容的 `POST /api/books/{book_id}/chapter-summaries` |
| `current_state` | 当前状态速查 | `POST /api/books/{book_id}/current-state/generate` |
| `chapter_writing` | 正文生成 | 持续生成正文任务，详见 [工作流接口](writing-workflow-api.md) |

连接测试和读取模型列表直接使用 URL 指定的配置 ID，不经过模块分流。正文生成默认选取已保存的 Gemini 模型配置，也可明确指定其他模型。正文、大纲、资料和配置保存不触发模型调用。

## 默认与指定配置

- 原 `LLMProvider.enabled` 字段保留兼容，现在在界面明确称为“设为默认模型”。仍最多一个默认模型，升级不更改现有选择。
- 正文生成默认匹配模型 ID 含 gemini 的有密钥配置，优先默认 Gemini，其次最近保存的 Gemini；无匹配则提示先配置。其他没有独立分流的模块使用有密钥的默认模型；若无可用默认配置，提示先配置，不任意选择其他模型。
- 指定分流可以使用任意有密钥的 OpenAI Compatible 配置，无需把它设为默认。切换默认模型不改变明确指定的分流。
- 删除指定配置时，数据库将路由的配置 ID 清空，但保留“明确指定”状态。模块提示失效，不自动跟随默认，避免删除后 ID 被重用时调用其他配置。清除密钥同样使其失效。
- `enabled: false` 表示非默认，不再表示禁止明确分流调用。要停用某配置，先将对应模块切换到其他模型；清除密钥也会使其无法用于后续请求。

## 读取与修改

`GET /api/llm/routes` 返回当前四个模块：

```json
[
  {
    "module": "chapter_summary",
    "label": "单章梗概",
    "description": "正文页生成或重新生成本章梗概。",
    "provider_id": 3,
    "use_default": false,
    "revision": 1,
    "error": null,
    "effective_provider": {"id": 3, "provider_name": "整理模型", "model": "model-id"}
  }
]
```

`provider_id` 是配置 ID；模型配置列表仍通过 `GET /api/llm/providers` 读取，密钥不回显。没有已保存的分流时返回 `revision: 0`、`use_default: true`，GET 不写数据库。

`PUT /api/llm/routes/{module}`：

```json
{"provider_id": 3, "base_revision": 0}
```

传入 `provider_id: null` 恢复跟随默认模型。必须携带上次读取的修订号；成功返回该模块最新状态。并发旧修订返回 409；未知模块返回 404；指定配置不存在、类型不符或缺少密钥返回 400。失效指定配置的 `provider_id: null, use_default: false` 与正常默认模式不同，调用方不能只凭 ID 为空自行回退。

速查 `/context` 的 `model`、`max_output_tokens`、`timeout_seconds` 来自速查分流最终选择的配置。指定配置失效时返回 `provider_error`，仍返回来源资料统计，已有速查保持可读。

## 任务时机

单章梗概和速查在请求启动时选择模型。批量任务在创建时固定配置 ID、模型和参数（不保存密钥），正在运行或排队的任务不因分流修改切换模型。停止后点击继续时读取最新批量分流；既有章节结果不会重复覆盖。兼容的同步批量接口也在请求启动时固定选择。

分流配置本身只分配模型，不触发生成。持续生成任务由正文页显式启动，不增加自动重试或负载均衡。保存分流无需等待正在运行的生成结束，修改作用于后续任务。

## 复制模型配置

`POST /api/llm/providers/{provider_id}/copy` 无需请求体。复制已保存配置的 Base URL、API Key、模型、温度、最大输出 Tokens 和超时，创建名称带“副本”的独立配置，返回与模型列表相同的无密钥响应。原配置不存在或不是 OpenAI Compatible 类型时返回 404。没有密钥的配置也可复制，副本同样显示未设置密钥。

复制不改变默认模型或模块分流，副本固定为非默认。页面创建后直接选中副本，只改名称或模型名再保存即可；密钥由服务器复制，浏览器不读取原密钥。后续编辑、清除密钥或删除副本不会改动原配置。此接口不调用上游模型。

进入模型列表时优先选中默认配置，无默认时选中已有列表的第一项；只有没有任何配置或主动点击“新建”时显示空白连接表单，不预填示例服务地址或模型 ID。未保存的表单修改会禁用复制，切换配置时提供草稿保护。
