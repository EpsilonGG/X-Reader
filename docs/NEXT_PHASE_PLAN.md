# X-Reader 下一阶段规划

**文档版本**: v3.0
**日期**: 2026-09-29
**阶段状态**: **Phase 1–4 = 全部已完成（验收通过）**
**依据**: `docs/ARCHITECTURE_FREEZE_V3.md`（冻结契约）、`AGENTS.md`
**性质**: 工程路线图 + 执行状态。本文**不含任何具体代码**，只描述「做什么、为什么、按什么顺序、做到哪一步」。

> **v3.0 说明**：本文不再是 Phase 1 的历史存档。Phase 1–4 的实现描述归档在
> `docs/ARCHITECTURE.md`（§15 Phase 2、§16 Phase 3、§17 Phase 4）；本文只描述
> **尚未完成的顺序与状态**。
> 冻结契约本身（字段、Identity、Time、Media、边界）以
> `docs/ARCHITECTURE_FREEZE_V3.md` 为准，本文不复制、不改写它。

---

## 0. 本文的定位

X-Reader 的目标不是「一个抓取脚本」，而是**一个可以被反复替换上游、反复重解析、长期在免费 CI 上运行的数据底座**。因此本文按四类划分所有事项：

| 分类 | 含义 | 判断标准 |
| --- | --- | --- |
| **已经完成** | 已落地、有测试、在 CI 可跑 | 有测试、有文档、不依赖任何付费服务 |
| **本阶段必须完成** | 不完成则数据模型或输入能力不完整 | 直接影响跨来源契约能否成立 |
| **未来可以完成** | 有明确价值，但不做也不会损坏现有数据 | 属于「读取者」，与写入链路解耦 |
| **暂时不应该实现** | 现在做会污染数据模型或引入不必要依赖 | 违反冻结文档的边界条款 |

一条贯穿全文的原则：**写入链路（Unit → Provider → Parser → Raw → Normalizer → Storage）与读取链路（Output Adapter / RSS / Summary）必须可以独立演进。** 任何让写入链路依赖读取链路的改动，都归类到「暂时不应该实现」。

---

## 1. Phase 1 —— 已完成（归档）

Phase 1 交付的是**单一来源（X）的获取与持久化**，数据流到 Storage 结束：

```
Account（accounts.yaml）
    ↓
Provider / Fetcher        —— 只管「怎么拿到字节」
    ↓
Raw Response              —— url / status_code / content_type / text / fetched_at
    ↓
Parser                    —— 只管「怎么读懂字节」
    ↓
Raw Tweet                 —— 投影字段 + 原样保留的 raw 证据
    ↓
Storage                   —— 只管「怎么落盘、怎么去重」
```

| 层 | 目录 | 负责 | 明确不负责 |
| --- | --- | --- | --- |
| Provider | `providers/` | 端点、路由、故障转移、HTTP 调用 | 不解析、不落盘、不知道 RSS |
| Parser | `parsers/` | 把响应文本变成 `RawTweet` 列表 | 不联网、不落盘、不发 RSS |
| Storage | `storage/` | 持久化、去重、增量追加、审计记录 | 不知道 Provider / Parser 的存在 |
| 编排 | `app/` | 组合各层、单元级错误隔离、运行报告 | 不含任何业务判断以外的逻辑 |
| 入口 | `main.py` | 参数、退出码、报告输出 | 不读任何 CI 环境变量 |

Phase 1 的完整描述、目录结构、错误分类、Storage 选型论证与测试清单见
**`docs/ARCHITECTURE.md`**（v1.0，仍是 Phase 1 的权威描述）。

Phase 1 的验收结论：抓取 → 解析 → 存储链路可跑；重复运行零新增；单账户失败不影响其他账户；错误按种类区分；Actions 可手动与定时执行；不依赖官方 API、付费服务或 LLM。

---

## 2. Phase 2 —— 进行中

### 2.1 本阶段的目标（一句话）

**建立一条可运行的、来源无关的输入通路：**

```
X + RSS → Provider → Parser → Raw Item → Normalizer → NormalizedItem → Storage
```

并且：

- YouTube 只做**接口与最小契约**，不引入官方 API / API key / yt-dlp / 第三方服务；
- Output 只做**接口**，不实现 Telegram / QQ / RSS 输出；
- Storage 之后没有任何东西。

### 2.2 与 Phase 1 的实质差异

| 维度 | Phase 1 | Phase 2 |
| --- | --- | --- |
| 被追踪对象 | X 账户 | **Unit**：X 账户 **或** RSS 来源 |
| 中间记录 | `RawTweet`（唯一形态） | 每种来源一个 Raw 记录（`RawTweet` / `RawRSSItem` / `RawYouTubeItem`） |
| 落盘记录 | `RawTweet` | **`NormalizedItem`**（跨来源统一契约） |
| 身份 | `tweet_id` | `source_id` + `item_id` → 派生的 `identity_key` |
| 去重边界 | 单账户文件内 | **跨来源命名空间**（`x:12345` 与 `youtube:12345` 并存） |
| 「已见」与「已投递」 | 同一件事 | **两件不同的事**（`items/` 与 `delivery/`） |
| 存储接口 | 一个 | **两个**：`BaseStorage`（契约）+ `RawRecordArchive`（Phase 1 原始记录，仅证据） |
| 配置空间 | 账户 | **账户与 RSS 来源是两个独立配置空间** |

### 2.3 执行顺序与状态

顺序由 `ARCHITECTURE_FREEZE_V3.md` §G 与 Phase 2 指令共同确定。**不得跳步**，尤其不得先做 Telegram / QQ。

| # | 步骤 | 状态 | 产物 |
| --- | --- | --- | --- |
| 1 | `NormalizedItem` 正式接入 | **已完成** | `domain/models/item.py` 从「设计验证模型」升格为正式领域契约；新增 `from_dict` |
| 2 | X Normalizer | **已完成** | `normalizers/{base,x,registry}.py` |
| 3 | Storage 支持 `NormalizedItem` | **已完成** | `storage/{base,jsonl}.py` 重写为双接口；`data/items/` 为规范存储 |
| 4 | Seen / Incremental 边界 | **已完成** | `known_keys()` / `pending_for()` / `mark_delivered()`；`data/delivery/` |
| 5 | 通用 RSS 输入 | **已完成** | `providers/rss.py`、`parsers/rss.py`、`config` 的 `rss_sources` |
| 6 | RSS Normalizer | **已完成** | `normalizers/rss.py`（含 URL 身份回退与 `identity_basis`） |
| 7 | X + RSS 端到端 | **已完成** | `app/runner.py` 统一 Unit 循环；真实 HTTP 端到端测试 |
| 8 | `OutputAdapter` 接口 | **已完成（仅接口）** | `outputs/base.py`；无任何具体适配器 |
| 9 | YouTube 接口 / 最小契约 | **已完成（仅接口）** | `normalizers/youtube.py`、`domain/models/youtube_item.py`；**无 Provider** |
| 10 | RSS-only 配置解耦 | **已完成** | `config/schema.py` 允许空 `provider.order`，改由「至少一个 enabled input source」约束；`config/loader.py` 允许 `accounts: []`；新增 RSS-only 端到端测试 |

### 2.4 Phase 2 新增的目录

```
X-Reader/
├── normalizers/                 来源形态 → 统一契约（纯函数）
│   ├── base.py                  BaseNormalizer：只共享「有效性闸门」
│   ├── x.py                     RawTweet        → NormalizedItem
│   ├── rss.py                   RawRSSItem      → NormalizedItem
│   ├── youtube.py               RawYouTubeItem  → NormalizedItem（接口）
│   └── registry.py              kind → normalizer（按 kind，不按 provider）
├── outputs/
│   └── base.py                  OutputAdapter 接口 + DeliveryResult（无实现）
└── data/
    ├── items/<source_id>.jsonl  ★ 规范存储（NormalizedItem）
    ├── accounts/<unit>.jsonl    Phase 1 原始记录归档（仅证据，X 推文）
    ├── runs/<YYYY-MM-DD>.jsonl  审计
    ├── raw/<unit>/…             原始响应体（可选）
    └── delivery/<output>.jsonl  每个输出的投递状态
```

`data/accounts/` 保留 Phase 1 的名字：重命名一个装着已提交历史的目录只是为美观做数据迁移。它是**每单元的原始记录归档**，今天只有 X 写入，且**不在 Phase 2 数据流上**。

### 2.5 两个配置空间（不可混同）

| 配置 | 文件 | 含义 | 命名空间 |
| --- | --- | --- | --- |
| `accounts` | `accounts.yaml` | X 账户 | `source_id = "x"`（固定） |
| `rss_sources` | `config/config.yaml` | RSS / Atom 来源 | `source_id = <config 里的 id>` |

```yaml
rss_sources:
  - id: visualnovel-interview   # 命名空间，必须是小写 slug，不得为 x / youtube
    url: https://example.com/rss.xml
    enabled: true
```

**绝不把 RSS 来源伪装成 X 账户。** `id` 是命名空间而不是标签：两个站点可以合法地使用同一个数字文章 id，共享一个 `web` 命名空间会导致身份冲突。

两个配置空间各自独立，因此可以只启用其中一个。三种输入模式的完整矩阵见 **§4.1**。

### 2.6 Phase 2 的边界（与冻结契约一致）

- Provider 只管 HTTP / 超时 / 重试 / 故障转移 / 错误映射；
- Parser 只管 XML / HTML 解析，**不联网、不落盘、不去重、不产出输出格式**；
- Normalizer 是**纯函数**：不联网、不落盘、不知道是哪个 Provider 产出的字节；
- Storage 只知道 `NormalizedItem`，**不认识 `RawTweet`**，也不知道 X / RSS / YouTube 的存在；
- 三者互不认识，绑定集中在 `app/registry.py` 的一张表里。

### 2.7 关于 `VisualNovel-Interview-RSS-main`

该项目是**成熟的外部 RSS 来源**，不是待重构对象。

- 不修改它；不修它的历史缺陷；不重设计它的 `Item` 模型；
- 不把它的 Parser / history / RSS 逻辑搬进 X-Reader；
- **不创建 `VisualNovelProvider`**；X-Reader 不依赖它的 Python 代码；
- 它只以一个 URL 的形式出现在 `rss_sources` 里。

**契约适配的责任在 X-Reader 一侧。** 它的真实输出（24 条中 24 条 `guid == link`、仅 12 条有日期、21 条有 enclosure、有 1 条重复 URL）由 X-Reader 的输入契约吸收，而不是要求它改变。

---

## 3. Phase 2 期间做出的决策（需保留）

这些是实施过程中新产生的判断，Phase 1 的 D1–D9 之上叠加：

| 编号 | 决策 | 理由 |
| --- | --- | --- |
| D10 | `identity_key = "<source_id>:<item_id>"` 是**派生属性**，不落字段 | 落字段的副本会在编辑后与自己的分量矛盾；属性不会 |
| D11 | RSS 身份优先级：`guid`（且 `guid != link`）→ URL 归约；`metadata["identity_basis"]` 记录用了哪一个 | 真实 feed 普遍把 `guid` 设成链接，那不是标识符。记录依据让弱情况可见，而不是静默等价 |
| D12 | **绝不用**标题、发布时间或 `hash(标题+描述)` 作为身份 | 三者都会在条目未变时改变 |
| D13 | 时区归属由来源适配层决定，**纯 Normalizer 不猜**；无时区信息时 `published_at = None`，保留 `published_at_raw` | 本工作区的真实上游把 JST 解析后标成 UTC，误差 9 小时 |
| D14 | `published_precision` 必填（second/minute/hour/day/unknown） | 仅给日期的值被提升到午夜后，与「真的在午夜发布」无法区分 |
| D15 | `publisher` 取代 `author` 且可选；**不**从 `[站点名] 标题` 前缀反解来源 | 真实 feed 里 `author` 常缺失；括号前缀是某个渲染器的约定，通用解析会破坏合法标题（如 `[重要] …`） |
| D16 | `fetch.limit` **只作用于 X**，不截断 RSS | 它是为 Nitter「一页一次请求」设计的安全上限。feed 是整份文档，截断只会丢真实内容；默认 `on_error` 留存策略下还不留证据 |
| D17 | `RawRecordArchive` 是**独立接口**，不在 Phase 2 数据流上 | 保证没有任何东西能让 Phase 1 的原始形态回流进规范链路 |
| D18 | 绑定按 **route** 决定 normalizer kind，不按 provider | `nitter` 与 `xtf` 是同一内容的两条获取路径，必须产出同一身份 |

---

## 4. 输入模式与已知限制

### 4.1 输入模式（Phase 2 正式契约）

三种模式**全部合法**，由「至少一个 enabled input source」统一约束：

| 模式 | `provider.order` | `rss_sources` | 结果 |
| --- | --- | --- | --- |
| **X-only** | 非空（如 `[nitter]`） | 空 | 合法 |
| **X + RSS** | 非空 | ≥1 条 `enabled: true` | 合法 |
| **RSS-only** | **空 `[]`** | ≥1 条 `enabled: true` | 合法 |
| 无任何输入 | 空 `[]` | 空或全部 `enabled: false` | **拒绝**：`no input source configured` |

规则从 Phase 1 的「必须有 X」改成「必须有**某种**输入」：

- `provider.order` 非空 → 有 X 输入；
- `provider.order` 为空 → X 被禁用，此时**必须**至少有一个 enabled 的 RSS 来源；
- 两者皆无 → 仍然报错（配置无法产出任何数据）。

X 专属校验**没有被削弱**：`nitter` 只要出现在 `provider.order` 里，就仍然要求 `endpoints` 非空。RSS-only 之所以能跳过 endpoints 校验，是因为它根本没有声明 `nitter`。

### 4.2 已解决的限制

| 限制 | 现状 |
| --- | --- |
| ~~**无法配置「只有 RSS、完全没有 X」**~~ | **Resolved（2026-09-28）**：`provider.order: []` + 至少一条 enabled `rss_sources` 现在合法；`load_accounts` 也允许 `accounts: []`。见 §4.1。原先固定该限制的测试已改写为反映新契约（`test_an_rss_only_config_is_accepted`），未删除 |

### 4.3 仍然存在的限制

| 限制 | 现状 | 说明 |
| --- | --- | --- |
| `fetch.limit` 对 X 仍是单页上限 | 未做分页 | 真实分页是 Phase 3 事项；原始响应体保留，不会不可恢复地丢失。该上限**不作用于 RSS**（D16） |
| `known_keys()` 线性扫描 | 未做索引 | Phase 1 已知的唯一扩展性上限，见 §6.2 |
| 已存条目的**富化**未做 | 首写优先，记录不可变 | 当一条更丰富的路由在一条更贫瘠的路由之后成功时，已存条目不会被更新。见 §6.4 |

---

## 5. 暂时不应该实现

以下事项**现在做会造成实质伤害**，不是「优先级低」，而是「现在做是错的」：

| 事项 | 为什么现在不能做 |
| --- | --- |
| **更多具体输出适配器（Email / Notion / Web UI / 搜索界面）** | 接口已经由 Telegram（Phase 3）与 QQ（Phase 4）**两次**证明可用。再加一个之前先要有真实需求——没有需求时抽象只能靠想象 |
| **把 `markdown` / `telegram_text` / `qq_text` 加进 `NormalizedItem`** | `[from @username](url)` 是渲染器的事，不是数据。一旦入模型，所有适配器和所有已存记录都被绑死 |
| **RSS 输出（生成 XML 并发布到 Pages）** | 它是 `data/` 的读取者，不是写入链路的一环。它必须适应数据层，反过来不行 |
| **AI 摘要接入写入链路** | 会让核心系统依赖 LLM API。冻结契约要求 Summary 可选 |
| **`VisualNovelProvider` 或任何来源专属 Provider** | 会把一个外部项目的一生绑进 X-Reader。通用 RSS 输入已经覆盖它 |
| **把 RSS 当成内部规范模型** | 规范模型是 `NormalizedItem`。让 RSS 的字段形态决定数据层结构，会把其他来源全部排除在外 |
| **为「以后可能有用」加字段** | 字段一旦写入就进入历史，删除成本远高于添加 |
| **重构 Phase 1 目录 / 模块名** | 已有提交历史与测试建立在其上，改名只是为美观付迁移成本 |
| **SQLite / Redis / 数据库服务 / 消息队列 / VPS / NAS / 常驻进程** | 违反冻结契约的运行环境约束。免费 GitHub Actions 上唯一可用的持久化介质是仓库本身 |
| **官方 X API / YouTube Data API / 付费服务 / yt-dlp** | 违反冻结契约的运行环境约束。YouTube 已由公开的频道订阅源覆盖（Phase 4），不需要 API key，也不需要配额 |
| **提前设计「多平台统一模型」** | 现在有三个真实输入（X、RSS、YouTube，均已落地）已足够检验契约；为想象中的第四个平台设计会引入猜测字段 |

---

## 6. 未来可以完成（Phase 3+）

这些是「读取者」或「规模优化」。它们的共同特点是：**只读 Phase 2 的产物，不改变写入链路。**

### 6.1 输出适配器

RSS 输出、Telegram、Email、Notion、Web UI、搜索界面，各自独立，互不依赖，可以按需添加。

- 每个都必须是可选的：没有任何输出适配器时，核心链路照常工作；
- 输入是 `NormalizedItem`，不是 Raw 记录；
- **适配器不自己记录投递状态**：它返回 `DeliveryResult`，由调用方通过 Storage 持久化。否则会出现第二个事实来源。

**状态：Telegram 已于 Phase 3 实现**（`outputs/telegram.py` + `outputs/factory.py`，配置在 `config/config.yaml` 的 `telegram:` 块，凭据只从环境变量读取）。上述三条契约在实际实现中全部成立，未做任何放宽。

**QQ / OneBot 已于 Phase 4 实现**（`outputs/qq.py`），走的是 OneBot v11 的 HTTP API（`POST /send_group_msg`），不是 QQ 官方机器人接口、不是 SDK、不是常驻进程。它复用**完全相同**的一套接口（`OutputAdapter.emit()` + `pending_for()`/`mark_delivered()`），并且与 `telegram` 使用**不同的投递命名空间**（`data/delivery/qq.jsonl` vs `telegram.jsonl`）——Phase 4 为此**没有改动 Storage 一行**，Phase 3 按 output 分命名空间的设计直接生效。

**RSS 输出未实现**：它与 Telegram、QQ 并列，不是替代关系。

### 6.2 Storage 的可查询化与规模上限

保持 `BaseStorage` 接口不变，在**它背后**增加派生索引（SQLite 是自然选择，此时它是派生产物，损坏可重建）。

- **JSONL 是事实来源，索引是可重建的缓存。** 不一致时以 JSONL 为准；
- 索引缺失或损坏时自动重建，不报错给用户；
- 调用方（Runner）不能感知索引的存在。

### 6.3 观测性

- 让 `data/runs/` 成为可查询的失败历史（哪些单元、何时、因为什么、连续失败几次）；
- 定义「连续 N 次失败」的告警口径：单次失败不值得告警，持续失败才值得；
- 明确 `store_raw_response` 在长期运行下的容量策略（按时间清理，或按单元保留最近若干份）。

### 6.4 数据修复与富化

- 明确「重新解析」流程：从留存的历史原始响应重建记录，而不是重新抓取；
- 定义一条**受控的条目富化/修正路径**：保持追加为主的写入方式，避免「全文件重写」，且操作可审计（谁改的、依据什么、改了多少条）。

### 6.5 多平台 Provider

**YouTube Provider 已于 Phase 4 实现**（`providers/youtube.py` + `parsers/youtube.py`），读的是频道公开的 Atom 订阅源而不是 Data API。新增它只动了固定的三处——Provider 模块、`providers/factory.py`、`app/registry.py` 的绑定表——Parser / Normalizer / Storage / Runner 的既有代码**一行未改**，这正是绑定表显式化的收益。

**现在仍不应新增 Provider**：没有真实需求时抽象只能靠想象。契约已由三个真实输入（X、RSS、YouTube）检验过。

### 6.6 记录项：`identity_key` 的落盘残留（Phase 2 审计发现，**Phase 3 未修**）

Phase 2 Final Snapshot 审计记录、Phase 3 明确不处理的一项：

- `NormalizedItem.to_dict()` 会把派生的 `identity_key` 一并写进 JSONL 行；
- `storage/jsonl.py::_scan_items()` 读取时**优先信任**落盘的那个值。

**当前不是缺陷**：写入路径由 `identity_key` 属性派生，落盘值与重新派生的值不可能不一致；只有**外部手工编辑**的行才能构造出矛盾（例如把 `identity_key` 改成另一条记录的键）。

**为什么 Phase 3 不修**：它是 Phase 2 的既有行为，修它属于数据格式变更（要么停止写入该字段，要么改为忽略落盘值），会牵动已提交的历史数据，且与「Telegram 投递」这一阶段目标无关。按最小改动原则延后。

**将来修的时候**：`from_dict()` 已经按 DR-3 忽略传入的 `identity_key`（重新派生），所以只需让 `_scan_items()` 走同一条路径——即不再优先信任落盘值，或干脆不再写入该字段。**这是一个清理项，不是紧急项。**

---

## 7. 各阶段验收标准

| 阶段 | 验收标准 | 状态 |
| --- | --- | --- |
| **Phase 1** | 抓取 → 解析 → 存储链路可跑；重复运行零新增；单单元失败不影响其他单元；错误按种类区分；测试全绿；Actions 可手动与定时执行；不依赖官方 API、付费服务或 LLM | **通过** |
| **Phase 2** | X 与 RSS 两条链路都收敛于 `Normalizer → NormalizedItem → Storage → OutputAdapter`；同一 `item_id` 在不同 `source_id` 下并存；重复运行零新增；Seen 与 Delivery 在模型与接口层面真正分离；输出失败不丢内容；X-only / RSS-only / X+RSS 三种输入模式均可配置；YouTube 与 Output 只到接口；不引入 SQLite / Redis / 付费服务 | **通过**（步骤 1–10 均已落地，见 §2.3 与 §4.1） |
| **Phase 3**（Telegram Output + Delivery） | 链路 `Storage → pending_for("telegram") → TelegramOutput → mark_delivered()` 闭环；适配器不记录自身状态、不改 `NormalizedItem`、不写 `data/`；投递失败不删条目且下次运行重投；`telegram` 与未来 `qq` 命名空间互不影响；长消息分片只有全部分片成功才算投递；未配置时核心链路完全可用 | **通过** |
| **Phase 4**（YouTube Provider + QQ / OneBot Output） | YouTube 走 `Provider → Parser → RawYouTubeItem → Normalizer → NormalizedItem → Storage`，身份为 `youtube:<channel_id>` + 视频 ID，不需要 Data API；QQ 走 `pending_for("qq") → QQOutput → mark_delivered()`，用 OneBot HTTP API，凭据只从环境变量读；两个输出各自一份投递状态，一个失败不影响另一个；三个输入源共用同一个 Storage；`NormalizedItem` 未新增任何字段；Telegram 的 8 项行为全部回归通过 | **通过**（见 Phase 4 Final Report） |
| **Phase 5+** | 每个输出适配器可单独启用/停用；核心链路在没有任何输出适配器时完全可用；接入 LLM 与不接入 LLM 的行为差异仅限摘要产出 | 未开始 |

---

## 8. 风险登记

| 风险 | 影响 | 应对 |
| --- | --- | --- |
| 公共 Nitter 实例持续减少 | 抓取成功率下降 | 多实例故障转移 + `xtf` 走不同上游路径 + 允许自建实例（仅改配置） |
| 上游 HTML 结构变化 | 解析静默产出 0 条 | 「无 `timeline-item` 且无 `timeline`」判定为解析错误而非空结果；`on_error` 留存现场；Phase 3 的连续失败告警 |
| RSS 身份依赖 URL 归约 | 仅靠查询参数区分的两个页面会合并成一条 | 已记录为已知代价；`identity_basis` 让该情况在数据中可见，而不是静默 |
| 无时区信息的时间被误当本地时间 | 时间偏移 | D13：无时区则 `published_at = None`，保留原始字符串，由来源适配层决定 |
| 仓库体积增长 | clone 变慢 | JSONL 追加式 diff 天然增量；原始响应留存策略在 §6.3 定义 |
| 数据规模超过线性扫描能力 | 运行变慢 | §6.2 的派生索引，接口不变 |
| 规范化与原始记录语义漂移 | 下游结果不可解释 | 两者分开存放（`items/` 与 `accounts/`），原始记录不可变，可对照 |
| 过早引入输出需求 | 数据模型被污染 | §5 的禁止清单 |

---

## 9. 下一步动作

Phase 2 的实施步骤已全部落地（§2.3），输入模式已全部放开（§4.1）。**Phase 3 的 Telegram Output + Delivery 已完成**，**Phase 4 的 YouTube Provider + QQ / OneBot Output 已完成**（§6.1、§6.5、§7），验收分别见 Phase 3 / Phase 4 Completion Report。

至此，输入端（X / RSS / YouTube）与输出端（Telegram / QQ）都已有真实实现，冻结契约被三个输入、两个输出共同检验过。

尚未实现、且**没有被批准**的：

1. **RSS 输出**——与 Telegram、QQ 并列的另一个适配器，不是它们的替代；
2. **AI 摘要**——必须保持可选：不接入 LLM 时核心链路的行为不能有任何差异；
3. Storage 的派生索引（§6.2）在数据量真正成为问题之前不做；
4. 观测性（§6.3）与数据富化（§6.4）按实际运维需要再排；
5. `identity_key` 落盘残留（§6.6）是一次**清理**，不是新功能；
6. 第四个输入源或第三个输出适配器——先要有真实需求。

**未经明确批准，不实现上述任何一项。**
