# data/

X-Reader 的持久化产物。**这个目录会被提交进仓库** —— 在 GitHub Actions 上，仓库本身是唯一可用的持久化位置，所以数据必须随代码一起提交。

```
data/
├── items/<source_id>.jsonl     ★ 规范存储：NormalizedItem，append-only，每行一条 JSON
├── accounts/<unit>.jsonl       Phase 1 原始记录归档（仅证据，目前只有 X 推文）
├── runs/<YYYY-MM-DD>.jsonl     审计轨迹，每次 (Unit × Provider × 路由) 尝试一行
├── raw/<unit>/                 原始响应留存 <ts>__<provider>__<route>.<ext>
└── delivery/<output>.jsonl     每个输出适配器的投递状态
```

## 两个存储，不要混同

| 目录 | 是什么 | 是不是数据流的结果 |
| --- | --- | --- |
| `items/` | **规范存储**。每行一个 `NormalizedItem`，一个来源一个文件，文件名是它的 `source_id` | **是** |
| `accounts/` | **原始记录归档**。解析器对来源的原始提取，用于「将来可以重新解析而不重新抓取」 | 不是。它是证据 |

`accounts/` 保留 Phase 1 的名字：重命名一个装着已提交历史的目录只是为美观做数据迁移。它今天只有 X 写入。

`delivery/` 记录的是**每个输出适配器**对每条内容做了什么。它与 `items/` 是两件不同的事：`items/` 回答「我见过吗」，`delivery/` 回答「投递了吗」。**把两者混为一谈是内容丢失的经典成因**——投递失败的条目必须保持待投递，而不是因为「已见」而被跳过。

## 语义

- **append-only。** 每次运行只追加新增记录，已存在的 `identity_key` 会被跳过而**不会**被重写。因此 `git diff` 只反映真正新增的数据。
- **首写优先。** 记录一经写入即不可变。对已存条目的富化是后续阶段的事项，见 `docs/NEXT_PHASE_PLAN.md` §6.4。
- **身份是 `<source_id>:<item_id>`。** 同一个数字 id 出现在两个来源下时是两条不同的记录（`x:12345` 与 `youtube:12345` 并存）。
- **文件名由 `source_id` 安全化而来**：`[^A-Za-z0-9_.-]` 会被换成 `_`。所以 `youtube:UC...` 这个来源落在 `items/youtube_UC....jsonl`——文件名里的冒号是下划线，不是笔误。
- **文件名由 `source_id` 安全化而来**：`[^A-Za-z0-9_.-]` 会被换成 `_`。所以 `youtube:UC...` 这个来源落在 `items/youtube_UC....jsonl`——文件名里的冒号是下划线，不是笔误。
- **`items/` 是事实来源。** `runs/`、`raw/`、`delivery/` 是辅助产物；`raw/` 可以按策略清理，`runs/` 可以按需归档，都不影响事实来源的完整性。
- **单行损坏不会毁掉整个文件。** 解析失败的行会被跳过并计数，其余记录照常可读。

## 不要手工编辑

需要修正数据时，应当走受控的修正流程（Phase 3，见 `docs/NEXT_PHASE_PLAN.md` §6.4），而不是直接改文件——手工编辑会破坏「记录不可变」这一前提，也会让追加语义失效。

## 检查

```bash
python scripts/verify_storage.py
```

校验每个文件可解析、必填字段齐全、`identity_key` 与自己的分量一致、单个来源内无重复 `identity_key`（归档内无重复 `tweet_id`），并汇总每个来源的记录数与运行状态分布。
