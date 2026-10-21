# 持续生成正文（保存的画布工作流）

正文工具栏的“持续生成正文”使用每本书选择的“生成正文”工作流逐章写作；审核调用配置的审稿工作流，修订调用配置的正文修订工作流。应用只负责范围、状态、次数和安全检查，不再使用内部固定正文提示词直接请求模型。单章入口保持原有手动行为。

## 范围与授权

- 按数量：输入 1—1000 章。默认从连续已有正文之后的第一处空章开始；正文页和章纲页可另外指定 `start_chapter`，从指定章连续处理。
- 到指定章纲：使用稳定章纲 ID 作为终点，包含终点。例如已写完前 4 章，选第 20 章，生成第 5—20 章，共 16 章。
- 范围必须具有连续、已确认章纲；缺章、草稿章纲或位置/关联不匹配会阻止启动，不跳过。已有非空正文默认阻止启动；明确传入 `replace_existing:true` 后才会生成新版本，旧稿保留。
- 已关联的空章节复用；未关联章纲在轮到本章时创建空章。空章、关联、子任务和父任务检查点在同一事务提交。
- 启动时记录章纲 ID、版本、位置，以及三个工作流 ID、输出节点和 ETag。任务中途的章纲变化会停止；画布版本变化通过 expectedVersion 拒绝提交，不静默切换。
- 本次自动局部修订默认为开；仅采纳有正文证据的确定问题，可选建议不采纳，需作者裁定或改纲时暂停。所有自动正文保留草稿，机器审核不代表作者确认。

## 动态审核

每章完整审核上限默认 **3 次**，含首次。可设 0—5；0 表示只生成。

1. 生成正文并保存初稿。
2. 完整审核覆盖剧情推进、主角行动、实际回报、人物反应、阅读流畅度、连续性、大纲与本章目标。通过即完成本章。
3. 有确定且可局部修复的问题时，调用“正文修订”。局部编辑由原工作流生成替换清单并合成正文，复核节点检查实际改动和采纳问题。
4. 仅字词、标点或局部表达变化，且采纳问题已解决、无新问题，可以免重审。涉及剧情、因果、动机、时间线、能力或章纲落实时，重新完整审核。
5. 完整审核达到上限仍需重审、重复问题证据、修订无变化、结论缺失/矛盾/来源版本错误或需要作者处理时暂停。修订次数也不超过审核上限，防止循环。

完整审核与修订内的复核分别计数。模型报告末尾须返回唯一 `<continuous_decision>` JSON 块，包含 stage、source_version_id、action、reason、needs_author、impact、checked_dimensions、issues、resolved_ids 等字段。后端严格校验，不能根据自然语言“通过”猜测结果。人类审稿界面隐藏机器块，原始报告保留供查证。

首次启用时在保存的审核/修订画布中添加空文本节点 `continuous-review-control`，分别接入最终主编/修订复核提示词；保留原提示词与引用。修改前的完整画布保存到 `DATA_DIR/workflow-backups/<sha256>.json`，使用 If-Match 防并发覆盖。连续任务运行时传入判断规则和正文来源版本；手动工作流中该输入默认空白。重复配置不再次修改。

## 前文资料、停止与恢复

每章生成前，沿用画布入口补齐/更新前文梗概和速查；从上一章最终正文版本取材。连续任务的批量梗概/速查模块与备用模型链在启动时固定，密钥不写入快照。未开始写下一章时不提前归档当前草稿；末章的梗概与速查待下次续写前更新。

关闭浮窗、换页或刷新不会停止任务。服务器持久保存最近 100 条步骤日志、审核次数、修订次数及工作流子任务记录。任务开始后收起新任务配置，主要显示进度与日志；可随时打开新任务设置。

停止会等待当前已提交工作流结束，不启动下一步骤；尚未提交的子任务取消。恢复沿用原范围、配置和已完成步骤，不重复生成。连接/已知执行失败可显式重试；提交结果未知或远端运行未确认终止时拒绝重复提交。服务重启后父任务标为 interrupted，未提交子任务等待手动继续，已提交任务继续查询原 run_id。旧版内部模型任务不能恢复，请重新安排画布任务，历史正文保留。

质量判断引起的暂停需要作者处理并确认本章后继续；作者确认后可越过已处理章节。正文/章纲来源变化须先核对，不能用旧意见修订新稿。

## API

- `GET /api/books/{book_id}/writing-jobs/options`：返回默认续写起点及剩余章纲；`GET /api/books/{book_id}/writing-jobs/generation-options` 返回全部章纲，供指定章节重写入口使用。
- `POST /api/books/{book_id}/writing-jobs`：按数量 `{ "mode": "count", "chapter_count": 1, "start_chapter": 3, "replace_existing": true, "entry": "reader", "review_limit": 3, "auto_revise": true }`；不填写 `start_chapter` 时沿用默认续写起点。同一本书已有活动任务时返回该任务。
- `GET /api/books/{book_id}/writing-jobs/latest`、`GET /api/writing-jobs/{id}`：任务范围、workflow names、logs、failover_events，以及每章 phase/review_count/revision_count/decision/child_id。
- `POST /api/writing-jobs/{id}/cancel`：停止；`POST /api/writing-jobs/{id}/resume`：检查来源与人工处理结果后继续。

`engine=canvas-v1`；saved_count 为已保存正文数，completed_count 为自动流程已完成数，并非正式稿或梗概归档数。状态包括 queued/running/cancelling/cancelled/paused/failed/interrupted/completed。阶段包括 preparing/generate/review/revision/waiting/paused/completed。

数据库增加 `writing_job_items.execution` JSON 保存检查点。任务记录只在本机保存，不随便携书籍备份恢复为可执行任务。升级前应备份数据库。
