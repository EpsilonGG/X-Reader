# X-Reader 架构说明

**文档版本**: v3.0
**日期**: 2026-09-29
**范围**: Phase 1–4（均已建成）
**依据**: 根目录 `Architecture Freeze v2.md`、`docs/ARCHITECTURE_FREEZE_V3.md`

> **阅读顺序**
> 1. 本文 §1–§14 描述 **Phase 1**：X 内容获取 + 原始数据持久化。这一部分**已建成**，除
>    §15 列出的扩展点外不再变化。
> 2. 本文 **§15** 描述 **Phase 2** 引入的跨来源输入通路，与 §1–§14 并存而非取代。
> 3. 本文 **§16** 描述 Phase 3（Telegram 投递），**§17** 描述 Phase 4（YouTube 输入 + QQ / OneBot 投递）。
> 4. 冻结契约本身（字段、Identity、Time、Media、Storage / Output 边界、字段映射表）在
>    **`docs/ARCHITECTURE_FREEZE_V3.md`**，那是权威来源。本文只描述**实现如何落地**。
> 5. 阶段顺序与状态见 **`docs/NEXT_PHASE_PLAN.md`**。

---

## 1. 这是什么

X-Reader 是 X-rss 的**基础层**独立实现：它只负责把 X/Twitter 上的内容取回来并**可靠地存下来**，到此为止。

它的价值不在于「能抓推文」，而在于**把抓取与解析的边界固定下来**，使得：

- 上游获取方式可以被替换（换实例、换路由、换数据源），而不影响已存数据和下游；
- 已经存下来的数据可以被重新解析，而不需要重新抓取；
- 任何「读取者」（RSS、摘要、通知、界面）都可以独立增删，写入链路不知道它们的存在。

**Phase 1 的数据流只有一条，且到 Storage 结束：**

```
Account
  ↓
Provider / Fetcher        「怎么拿到字节」
  ↓
Raw Response              未加工的响应 + 来源信息
  ↓
Parser                    「怎么读懂字节」
  ↓
Raw Tweet                 投影字段 + 原样保留的证据
  ↓
Storage                   落盘 / 去重 / 增量
```

Phase 2 在这条链路上**插入一个纯函数层**并**泛化被追踪对象**，见 §15。

---

## 2. 目录结构

```
X-Reader/
├── main.py                     入口：参数、退出码、报告
├── config/
│   ├── config.yaml             运行配置（Provider、HTTP、抓取、存储）
│   ├── schema.py               Pydantic 配置模型 + 校验
│   └── loader.py               配置与账户文件加载
├── accounts.yaml               被追踪的账户（账户优先，绝不硬编码）
│
├── domain/                     领域层：不含任何 I/O
│   ├── errors.py               错误分类体系
│   └── models/
│       ├── account.py          被追踪账户（X 账户与 RSS 来源共用此槽位）
│       ├── item.py             NormalizedItem —— 跨来源契约（Freeze v3）
│       ├── media.py            媒体（图片 / 视频 / GIF）
│       ├── tweet.py            RawTweet —— Phase 1 的落盘记录
│       ├── rss_item.py         RawRSSItem
│       └── youtube_item.py     RawYouTubeItem
│
├── infrastructure/             基础设施层
│   └── http/
│       ├── client.py           HTTP 客户端：重试、状态码映射、类型化错误
│       └── response.py         RawResponse —— Fetcher 与 Parser 之间的契约
│
├── providers/                  「怎么拿到字节」
│   ├── base.py                 BaseProvider 抽象
│   ├── nitter.py               Nitter（rss / html 两条路由，多实例故障转移）
│   ├── rss.py                  RSS/Atom 通用 Provider（无来源专属逻辑）
│   ├── youtube.py              YouTube 频道订阅源 Provider（feed 路由）
│   ├── xtf_adapter.py          x-tweet-fetcher 适配器（search 路由，可选）
│   └── factory.py              由配置构造 Provider
│
├── parsers/                    「怎么读懂字节」
│   ├── base.py                 BaseParser 抽象 + 共用工具
│   ├── nitter_html.py          Nitter 时间线 HTML 解析
│   ├── nitter_rss.py           Nitter RSS 解析
│   ├── rss.py                  RSS / Atom 通用解析（见 §15）
│   ├── youtube.py              YouTube 频道 Atom 解析
│   ├── media_url.py            Nitter 媒体代理地址还原
│   └── timeutil.py             时间解析（绝不推算）
│
├── normalizers/                「怎么变成统一契约」
│   ├── base.py                 BaseNormalizer 抽象（纯函数）
│   ├── x.py                    X 推文 → NormalizedItem
│   ├── rss.py                  RSS 条目 → NormalizedItem
│   ├── youtube.py              YouTube → NormalizedItem
│   └── registry.py             kind → normalizer
│
├── storage/                    「怎么落盘」
│   ├── base.py                 BaseStorage + RawRecordArchive + ItemReader
│   │                           （三个接口，见 §15.6 与 §16.5）
│   └── jsonl.py                JSONL 实现（选型理由见模块文档与 §7）
│
├── outputs/                    「投递到哪里」（Phase 3 Telegram / Phase 4 QQ）
│   ├── base.py                 OutputAdapter + DeliveryResult + 共用工具
│   ├── telegram.py             TelegramOutput（Bot API）
│   ├── qq.py                   QQOutput（OneBot HTTP API）
│   └── factory.py              由配置构造输出适配器
│
├── app/                        编排层：唯一知道全部层次的包
│   ├── registry.py             (provider, route) → Binding(parser, normalizer_kind)
│   └── runner.py               运行循环、错误隔离、投递编排、运行报告
│
├── scripts/
│   ├── verify_architecture.py  架构门禁：分层是否仍然成立
│   ├── verify_readme.py        README 的每条声明是否与代码一致
│   ├── verify_reference_projects.py  参考项目只读性（mtime + pyc）
│   ├── verify_storage.py       数据完整性检查
│   ├── smoke_youtube.py        YouTube 链路冒烟（真实 socket）
│   └── smoke_phase3.py         Telegram 链路冒烟（真实 socket）
│
├── data/                       持久化产物（随仓库提交）
│   ├── items/<source_id>.jsonl  规范存储：NormalizedItem
│   ├── accounts/<user>.jsonl    原始推文归档，append-only
│   ├── runs/<date>.jsonl        审计轨迹，每次尝试一行
│   ├── delivery/<output>.jsonl  投递状态（与「已见」分离）
│   └── raw/<user>/              原始响应留存（按策略）
│
├── tests/                      688 个测试 + 自建 fixture
└── .github/workflows/update.yml
```

---

## 3. 各层契约

### 3.1 Provider

**负责**：端点、路由、故障转移、HTTP 调用、把失败翻译成类型化错误。
**产出**：`RawResponse`，仅此而已。

**不负责**：解析、落盘、RSS、摘要、调度。

契约的核心是**它必须返回原始字节而不是模型对象**。这正是 X-Reader 不复用 x-tweet-fetcher 的 `Router` 与模型的原因：x-tweet-fetcher 在内部完成了「获取 + 解析 + 归一化」，直接采用会**跳过 Parser 层并丢弃原始载荷**，从而破坏「原始数据优先」与「可重新解析」这两个前提。适配器只取它的传输层（`xtf.http`）、异常体系（`xtf.exceptions`）与实例配置（`xtf.config`）。

### 3.2 Parser

**负责**：把响应文本变成 `RawTweet` 列表。
**产出**：`RawTweet` 列表。

**不负责**：联网、落盘、调度、输出格式。

两条刻意的设计：

1. **解析器不设置 `provider` / `route`。** 同一个解析器服务两个 Provider（`nitter/html` 与 `xtf/search` 返回同样的 DOM），它无权也无法知道字节来自哪里。来源由编排层标记。
2. **解析器接收 `Account` 作为显式参数。** X-rss 的 `parse(response)` 从响应 URL 反推账户，这在 search 路由、重定向、带路径前缀的自建实例上会失败。

### 3.3 Storage

**负责**：持久化、按 `tweet_id` 去重、增量追加、审计记录、可选原始响应留存。
**产出**：`SaveReport`（收到/新增/重复/无效）与审计记录。

**不负责**：知道 Provider 或 Parser 的存在。

Phase 1 的语义是**首写优先**：已存在的 `tweet_id` 被跳过而非重写，因此记录不可变、追加语义成立、历史可回溯。对已存记录的富化（例如先经 RSS 收入、后来 HTML 成功）留给 Phase 2。

### 3.4 编排层

**负责**：加载配置与账户、构造 Provider、逐账户尝试 `Provider × Route`、解析、落盘、账户级错误隔离、结构化日志与运行报告、退出码。

**不负责**：任何与输出有关的判断。它不知道 `data/` 会有读者。

---

## 4. 路由绑定表

Provider 与 Parser 互不认识，绑定关系集中在一张显式表里：

| Provider | Route | Parser | 说明 |
| --- | --- | --- | --- |
| `nitter` | `rss` | `nitter_rss` | `GET /{user}/rss`。轻量，但**没有**回复/引用/计数。 |
| `nitter` | `html` | `nitter_html` | `GET /{user}`。字段完整。 |
| `xtf` | `search` | `nitter_html` | `GET /search?q=from:{user}&f=tweets`。**不同的上游路径**，因此能在直接时间线坏掉时成功。 |
| `rss` | `feed` | `rss` | 通用 RSS / Atom，无来源专属逻辑（§15.7）。 |
| `youtube` | `feed` | `youtube` | 频道公开 Atom 订阅源（§17.2）。 |

表是显式而非按命名约定推导的：两个 Provider 共用一个 Parser，命名约定会让这件事变得不可见。

绑定同时给出 **normalizer kind**（`Binding(parser, normalizer_kind)`）。kind 绑定在 **route** 上而不是 Provider 上：同一条推文可能由 Nitter 或 xtf 取回，若按 Provider 决定 kind，它的身份就会随「哪个 Provider 先应答」而漂移（决策 D18）。

**新增一个 Provider 需要改动的位置是固定的三处**：Provider 模块、`providers/factory.py`、本表。无需触碰 Storage、Parser 或编排逻辑。

启动时会检查「所有在用的路由是否都有 Parser」，缺失会在任何网络请求发生**之前**报错，而不是表现为一次神秘的空运行。

---

## 5. 数据模型

### 5.1 RawTweet

| 分组 | 字段 |
| --- | --- |
| 身份 | `tweet_id`（主键）、`account`、`url`（规范 X 永久链接） |
| 内容 | `text`、`created_at`（ISO 字符串或 `None`）、`created_at_raw`（源的原样渲染） |
| 作者 | `author_username`、`author_name` |
| 关系 | `is_retweet`、`retweeted_by`、`is_reply`、`reply_to`、`is_quote`、`quoted_tweet_id`、`quoted_author`、`quoted_text` |
| 附加 | `is_pinned`、`media[]`、`stats` |
| 来源 | `provider`、`route`、`fetched_at` |
| 证据 | `raw`（解析器提取内容的原样快照） |

三条设计原则：

1. **原始数据优先。** 每个字段都是「源实际渲染了什么」的投影，未加工的解析结果原样保留在 `raw`。这使得未来的 Normalizer 可以**不重新抓取**就重新推导出更好的模型。
2. **不做过早的统一模型。** 只建模 Nitter 的 RSS 与 HTML 真正暴露的字段。不因为「以后可能有用」而加字段。
3. **时间保持字符串。** `created_at` 在源渲染了绝对时间时是 ISO-8601，否则是 `None`。不做类型强制、不猜时区、不丢信息。

`stats` 的 `None` 与 `0` 语义不同：`None` 表示「源没有渲染这个数字」（Nitter 对无浏览数据的推文渲染空的 views 节点），`0` 表示真的是零。

### 5.2 媒体

`Media(url, type, thumbnail)`，`type ∈ {image, video, gif}`。

Nitter 从不直接提供媒体，而是通过 `/pic/<url-encoded 真实路径>` 代理，图片链接还带一个 `orig/` 段表示原图。解析时**还原为真实地址**，否则存下来的 URL 会随服务它的 Nitter 实例一起失效。还原规则：解码后若路径已带主机名则保留，否则视为 `pbs.twimg.com` 上的路径——「一律加 `pbs.twimg.com` 前缀」这种朴素做法会把所有视频 URL 变成 `pbs.twimg.com/video.twimg.com/...`。

### 5.3 时间

规则只有一条：**源渲染了绝对时间就返回绝对时间，否则返回 `None`。绝不从相对时间推算绝对时间。**

`3h` 会原样记入 `created_at_raw`，而 `created_at` 保持 `None`。推算会把捏造的时间戳写进原始存储，而 Phase 1 存储的全部意义在于存事实而非推断。

---

## 6. 错误处理

### 6.1 分类

```
XReaderError
├── ConfigurationError     配置 / 账户文件不可用
├── ProviderError          获取层失败（携带 HTTP status）
│   └── NetworkError       传输层失败（超时、DNS、5xx、429）
├── ParsingError           拿到响应但读不懂
├── StorageError           落盘失败
└── AllProvidersFailed     某账户的所有 Provider/Route 都失败（携带原因链）
```

每个错误带稳定的机器可读 `kind`，日志与运行报告因此不需要字符串匹配。

`ProviderError` 携带 `status` 是为了区分两种截然不同的失败：

- **404**：账户不存在。其他实例会给出同样结论，因此**短路实例循环**，不再浪费时间预算。
- **403 / 429**：本实例在拒绝我。换下一个实例可能成功。

X-rss 与 x-tweet-fetcher 都会为一个不存在的账户把剩下的实例全部试一遍。

### 6.2 处理范围

| 失败位置 | 处理 |
| --- | --- |
| 单条路由失败 | 换下一条路由 |
| 路由成功但 0 条推文 | 记为「无内容」并继续；若所有路由都是如此，整体判定 `no_tweets`（**不是错误**——一个受保护账户或空时间线是正常状态） |
| 单账户全部失败 | 记录错误与原因链，继续下一个账户 |
| Storage 失败 | 中止**该账户**剩余路由。所有路由写同一个地方，换 URL 修不好磁盘。 |
| 审计记录写入失败 | 降级为警告 |
| 原始响应留存失败 | 降级为警告 |
| Provider / Parser 抛出未预期异常 | 捕获并归类为 `unexpected_error`，该次尝试失败，运行继续 |

后两条的依据是：**丢日志绝不能丢数据。**

---

## 7. Storage 设计

### 7.1 布局

```
data/
├── accounts/<username>.jsonl   原始推文记录，append-only，每行一条
├── runs/<YYYY-MM-DD>.jsonl     审计轨迹，每次尝试一行
└── raw/<username>/<ts>__<provider>__<route>.<ext>   原始响应（按策略）
```

### 7.2 为什么是 JSONL 而不是 SQLite

决定性因素是**持久化介质**：免费 GitHub Actions 上唯一可用的持久化位置是仓库本身，因此数据库文件必须被提交。

| | SQLite | JSONL（选用） |
| --- | --- | --- |
| git diff | 二进制 blob，无法 diff | 每次只追加变化行，diff 极小 |
| 仓库增长 | 每次整文件重提交 | 与**实际新增数据**成正比 |
| 损坏 | 二进制损坏即全部丢失 | 单行损坏只丢一行，其余可读 |
| 可审查 | 不可读 | `git log -p data/accounts/jack.jsonl` 直接看到每次收了什么 |
| 可重解析 | 需要 schema 迁移 | 每行带 `raw`，可重新推导 |
| 去重 | 引擎级主键约束 | 应用级（有专门测试） |
| 查询 | 强 | 需要线性扫描 |

明确接受的代价：去重由应用层保证；`known_ids()` 线性扫描账户文件。后者是唯一的真实扩展性上限，已在 `docs/NEXT_PHASE_PLAN.md` §2.3 安排：在 `BaseStorage` **接口不变**的前提下增加一个可重建的派生索引（此时 SQLite 作为派生产物不再有「二进制不可恢复」的问题，因为事实来源仍是 JSONL）。

### 7.3 容错

`_scan()` 遇到无法解析的行会跳过并计数，而不是抛错。进程在追加中途被杀会留下一行残破的行，那不应该让该账户的其余历史变得不可读。

### 7.4 原始响应留存策略

`storage.store_raw_response`：

| 取值 | 行为 |
| --- | --- |
| `never` | 不留存 |
| `on_error`（默认） | 仅在失败或解析出 0 条时留存 |
| `always` | 每次都留存 |

默认值的理由是：诊断信息恰恰在「什么都没解析出来」的时候最有用。

---

## 8. 配置

```yaml
provider:
  order: [nitter, xtf]          # 尝试顺序；置为 [] 表示禁用 X 输入
  nitter:
    endpoints: [...]            # 实例列表
    routes: [rss, html]         # 路由顺序
  xtf:
    enabled: true
    instances: []               # 留空则复用 nitter 的实例列表（单一事实来源）
    route: search
http:
  timeout: 20
  retries: 1
fetch:
  limit: 20                     # 单账户单次运行的抓取上限（只作用于 X）
storage:
  data_dir: data
  store_raw_response: on_error
telegram:                       # 输出一：凭据只从环境变量读
  enabled: false
  bot_token_env: TELEGRAM_BOT_TOKEN
  chat_id_env: TELEGRAM_CHAT_ID
qq:                             # 输出二：地址与群号不是秘密，令牌才是
  enabled: false
  api_base: "http://127.0.0.1:3000"
  group_id: ""
  access_token_env: ONEBOT_ACCESS_TOKEN
  timeout: 20
rss_sources: []                 # 外部 RSS / Atom 来源，独立配置空间
youtube_channels: []            # YouTube 频道，又一个独立配置空间
```

比 X-rss 严格的两点：

1. **端点在配置校验阶段就被去除首尾空白。** 这是**防御性加固，不是修复真实缺陷**：X-rss 线上配置里那 6 个端点在 `cat -A` 下确实显示尾随空格，但它们是未加引号的 YAML plain scalar，YAML 规范会剥离首尾空白，实测加载后 14 个端点全部干净有效。（早先的分析曾把这一点误判为真实缺陷，已在 `docs/ARCHITECTURE_ANALYSIS.md` 顶部更正。）
2. **未知 Provider 名在加载时即报错**，而不是在运行时表现为一次所有账户失败。

### 8.1 输入模式

X、RSS、YouTube 是**三个互相独立的输入源**（Phase 4 起）。约束只有一条：**至少一个 enabled input source**。

| 模式 | `provider.order` | `rss_sources` | `youtube_channels` | 结果 |
| --- | --- | --- | --- | --- |
| X-only | 非空 | 空 | 空 | 合法 |
| RSS-only | **空 `[]`** | ≥1 条 enabled | 空 | 合法 |
| YouTube-only | **空 `[]`** | 空 | ≥1 条 enabled | 合法 |
| 任意组合 | 非空或空 | 任意 | 任意 | 合法（只要至少一个 enabled） |
| 无任何输入 | 空 `[]` | 空 / 全部 disabled | 空 / 全部 disabled | 拒绝：`no input source configured` |

- `provider.order` 为空 = X 输入被禁用；此时**必须**有至少一条 enabled 的 RSS 来源**或** YouTube 频道，否则 `Config` 报错。
- **X 专属校验没有被削弱**：`nitter` 只要出现在 `provider.order` 里，就仍然要求 `endpoints` 非空。RSS-only / YouTube-only 之所以不需要 endpoints，是因为它们根本没有声明 `nitter`。
- 规则是**数输入源**，不是列举来源：每新增一类输入，同一个谓词多一个子句，没有哪一类被特殊对待。
- 对应 `Config.x_input_enabled` / `enabled_rss_sources` / `enabled_youtube_channels` / `youtube_input_enabled`。

**两个输出空间**同样独立于输入：`telegram` 与 `qq` 各自有 `enabled`，可以只开一个、两个都开或都不开。关掉输出只影响投递，不影响抓取与存储。

`accounts.yaml` 支持三种写法：

```yaml
accounts:
  - jack
  - username: OpenAI
  - username: AnthropicAI
    enabled: false
```

`accounts: []` 是**合法**的（RSS-only 场景）；「完全没有输入源」的兜底由 `Config` 与 `main.py` 共同负责。

账户**永远**通过配置管理，绝不硬编码。

---

## 9. 入口与退出码

```
python main.py [--config PATH] [--accounts PATH] [--data-dir PATH]
               [--account USER] [--limit N] [--json] [--strict] [-q|-v]
```

退出码是与 GitHub Actions 的契约：

| 码 | 含义 |
| --- | --- |
| 0 | 运行完成并产出了可用结果 |
| 1 | 运行完成但**每个**被尝试的账户都失败，或根本没有可用的 Provider |
| 2 | X-Reader 无法启动（配置错误） |

`--strict` 可让「任意账户失败」**或「任意投递失败」**也返回非零。

**投递失败本身不改变退出码**：退出码回答的是「采集成功了吗」，Telegram 挂掉不该把一次成功的抓取变成红色构建。失败以 ERROR 日志、报告中的投递行与 Actions 的 `::warning::` 呈现，条目保持 pending 等下次重投。

**入口刻意不读 `GITHUB_ACTIONS`、`GITHUB_WORKSPACE` 或任何其他 CI 环境变量**（有测试断言）。读取 `TELEGRAM_BOT_TOKEN` 之类的**密钥**不违反这一点：变量名来自配置（`telegram.bot_token_env`），值与 CI 无关，在笔记本上同样工作。业务逻辑必须在笔记本上和 CI 里表现一致；接线是工作流文件的职责，不是程序的职责。

---

## 10. GitHub Actions

`.github/workflows/update.yml`，`workflow_dispatch` + `schedule` 双触发。

工作流**不含业务逻辑**，只做四件事：装依赖 → 跑测试 → 跑 `python main.py` → 提交 `data/`。

关键设计：

- **运行前先跑测试。** 解析器坏了就绝不写入坏数据——数据是要提交进仓库的，坏数据很难回滚。
- **`concurrency` 串行化。** 两次并发运行会同时追加同一文件，然后抢 push。
- **push 前 `git pull --rebase --autostash`。** X-rss 缺这一步，只要有别的提交先落地，它的 push 就会失败。
- **`workflow_dispatch` 支持指定单个账户与上限**，便于人工排查。
- **单账户失败只产生 `::warning::` 标注**，不把整个 job 标红：一个改名或消失的账户不值得每天一次红色构建。全部账户失败才失败。
- **失败时上传诊断产物**（报告 + 原始响应留存）。
- 运行报告写入 `$GITHUB_STEP_SUMMARY`。

Phase 3 追加：

- **Telegram 凭据只经 GitHub repository secrets 注入**，并且是映射成**环境变量**（`env:`）而不是插值进 `run:` 命令行——后者会把密钥写进 shell 与 job log。工作流只**引用变量名**，从不打印、从不写文件、从不放进 artifact。
- **投递失败只产生 `::warning::`**，不把 job 标红：数据已经采集并落盘，未投递的条目保持 pending 等下次重投。
- **`git add data` 同时提交 `data/delivery/`**。这份状态是「什么已经发过」的唯一记录，丢了会把整个积压重发一遍。

业务代码不依赖 GitHub Actions 才能运行，工作流也不是唯一入口。

---

## 11. 测试

688 个测试：

| 文件 | 覆盖 |
| --- | --- |
| `test_parsers_html.py` | HTML 解析全部字段；引用块不泄漏媒体/计数/作者；空计数为 `None` 而非 `0`；非时间线页面报错；空时间线不算错 |
| `test_parsers_rss.py` | RSS 解析；HTML 描述展平；转推识别与作者取自永久链接；媒体还原；重复 enclosure 去重；诚实标注 RSS 不含的字段 |
| `test_identity.py` | Tweet ID 提取、规范 URL、时间解析（含「相对时间返回 `None`」）、媒体地址还原 |
| `test_storage.py` | 持久化、幂等、首写优先、字段完整性、断行容错、审计记录、留存策略 |
| `test_providers.py` | 故障转移、404 短路、记忆成功实例、异常映射、工厂、HTTP 客户端重试与状态码映射 |
| `test_config.py` | 端点空白字符回归、未知 Provider、账户文件形态、线上配置可加载 |
| `test_runner.py` | 成功路径、来源标记、增量、路由/Provider 回退、账户级隔离、Storage 失败、未预期异常、审计、留存、上限 |
| `test_entrypoint.py` | 退出码 0/1/2、`--json` 报告、账户过滤、`--data-dir`、不读 CI 环境变量 |
| `test_integration.py` | **真实本地 HTTP 服务器**跑完整链路，含路由故障转移与二次运行去重 |
| `test_rss_input.py` | RSS 身份（guid / URL 归约）、`identity_basis`、两种输入空间互不干扰 |
| `test_normalizers.py` / `test_normalized_item_contract.py` | Freeze v3 契约的必填项、`identity_key` 派生、时区不归 Normalizer |
| `test_telegram_output.py` | 适配器单元测试（§18 A–J）：成功、400/401/403/429/500、网络异常、分片、HTML 转义、无凭据、密钥脱敏 |
| `test_delivery_integration.py` | 投递闭环：X→Telegram、RSS→Telegram、失败重投**且不重新抓取**、去重、命名空间隔离、真实 socket 往返 |
| `test_youtube_parser.py` | 频道 Atom：`yt:videoId` 身份、`media:group` 的标题/简介/缩略图、命名空间、空简介、缺缩略图、非法 XML、缺 videoId、频道 id 归属 |
| `test_youtube_normalizer.py` | `source_id = youtube:<channel_id>` 的稳定性、`publisher` 取频道名、时间原样保留、`published_precision`、多频道隔离、不新增视频字段 |
| `test_youtube_provider.py` | 由 `channel_id` 生成 feed URL、`url` 覆盖、未知频道、超时/重试/HTTP 错误映射、单频道失败不阻塞其他频道 |
| `test_youtube_input.py` | YouTube 全链路到 Storage、与 X/RSS 共用同一个存储、Runner 集成、入口参数 `--no-youtube` |
| `test_qq_output.py` | OneBot 适配器：请求构造（`/send_group_msg`、Bearer 头）、消息内容、`retcode` 语义、成功/失败映射、凭据脱敏、**本地假 OneBot 服务器的真实 socket 往返** |
| `test_multi_output.py` | 多输出：{X, RSS, YouTube} × {Telegram, QQ} 七种组合、QQ 挂掉不影响 Telegram、每个输出各自的状态与重投、后开启的输出补齐积压 |

`tests/fixtures/` 中的 HTML 与 XML 是自建的，镜像真实 Nitter DOM（选择器经真实抓取页面核对），不依赖参考项目的 fixture 文件。

### 11.1 真实页面验证

自建 fixture 有一个固有弱点：**如果上游标记变了，fixture 和解析器可能一起错，而测试全部通过。** 为了堵住这个缺口，提供 `scripts/validate_against_real_page.py`：指向一张真实抓取的 Nitter 页面，它会打印解析器实际提取到的内容，并在 x-tweet-fetcher 可导入时与它的解析器逐字段交叉比对。

它刻意**不是** pytest 测试：它依赖本项目之外的文件，一个会静默跳过的测试比一个需要主动运行的工具更糟。

**2026-09-28 验证结果**（对一张真实抓取的 74 KB Nitter 时间线页面，21 条推文）：

| 检查项 | 结果 |
| --- | --- |
| 推文 ID 集合 | **完全一致（21/21）** |
| 可比字段值（用户名、回复、转推、点赞、浏览量、是否有媒体） | **126 个值中 1 个不同** |
| 媒体 URL 集合 | **0/21 不同** |
| 日期 | 全部一致（本实现额外归一化为 ISO 并保留原样字符串） |

唯一的分歧恰恰是本项目有意为之的设计：

```
638022429974134784 views: 参考实现=0   本实现=None
```

该推文的浏览量节点在真实页面里是**完全空的**：

```html
<span class="tweet-stat"><div class="icon-container"><span class="icon-views" title=""></span></div></span>
```

参考实现把「源没有渲染数字」强制为 `0`，从而把「未知」与「零」混为一谈。本实现保留 `None`。这不是缺陷，而是 §5.1 中 `None ≠ 0` 原则的直接体现——并且这一点是**由真实页面而非自建 fixture 验证的**。

同一张真实页面还确认了两处实现细节的正确性：

- 图片链接的 `href` 指向原图（`/pic/orig/media%2F...`），而 `src` 是带查询参数的降采样 webp（`...%3Fname%3Dsmall%26format%3Dwebp`）。取 `href` 是对的。
- 确实存在**正文为空、仅有图片**的推文（`<div class="tweet-content media-body" dir="auto"></div>`），因此「文本为空」不等于「解析失败」。

真实状态页（91 KB）同样验证通过：提取出焦点推文（ID `20`）加 35 条回复，回复目标正确归属。

**注意**：本次会话所在环境的出网被限制在允许列表内（`nitter.*` 域名被解析到 Meta 的 IP 段后连接超时），因此**未能对真实 Nitter 实例做在线端到端验证**。上述验证基于磁盘上的真实抓取页面，而非在线请求。上线前仍应在可正常出网的环境执行一次真实抓取。

---

## 12. 与三个参考项目的关系

| 来源 | 借鉴的内容 | 明确没有借鉴的 |
| --- | --- | --- |
| **X-rss** | 目录与模块划分（`app/`、`config/`、`domain/models/`、`infrastructure/http/`）；`RawResponse` 的形状；两条 Nitter 路由（`/{user}/rss`、`/{user}`）；「记住上次成功的实例」；Pydantic 配置 | 无状态设计（没有持久化）；`Runner` 内联构造全部对象；在 processor 阶段用正则从频道标题反推用户名；端点未去空白；`datetime.utcnow()`；未使用的 `feedgen` 依赖 |
| **x-tweet-fetcher** | 多实例故障转移循环；类型化异常体系；重试与退避；永不抛错的探活；`search?q=from:user&f=tweets` 这条不同的上游路由；媒体 `/pic/` 还原规则；转推识别；计数读取 | 它的 `Router` 与模型（会跳过 Parser 层并丢弃原始载荷）；它的 SQLite ledger（选型理由见 §7.2）；它的浏览器后端（GitHub Actions 上不可用） |
| **news-summary** | `RawItem` 抽象体现的「原始项 + 归一化分离」思想；跨源聚合；seen 状态追踪；两阶段（准备 / 生成）流水线；输出适配器模式 | 它的 Twitter 抓取实现；它的全局 JSON 状态设计；任何摘要能力进入 Phase 1 |

三个参考项目**均未被修改**。

---

## 13. Phase 1 刻意缺失的能力

以下均**未实现**，且不是遗漏：

AI 总结、LLM、Digest、Telegram、Email、Notion、Web UI、搜索界面、推荐、可视化、复杂分析、RSS 生成。

RSS 在 Phase 1 **不是**核心功能。兼容性设计（规范 URL 在解析阶段生成）已经就位，但 RSS 不得反过来决定数据层结构。后续把 RSS 作为 `data/` 的一个普通读取者加上去即可——详细路线见 `docs/NEXT_PHASE_PLAN.md`。

---

## 14. 如何新增一个 Provider

1. 在 `providers/` 新增模块，实现 `BaseProvider`（`name`、`routes_`、`fetch`）。
2. 在 `providers/factory.py` 中构造它。
3. 在 `app/registry.py` 中为它的每条路由绑定 Parser（若无现成 Parser，则新增一个）。
4. 在 `config/config.yaml` 的 `provider.order` 中加入它的名字，并在 `config/schema.py` 的 `KNOWN_PROVIDERS` 中登记。

无需改动 Storage、`RawTweet`、编排逻辑或任何测试基础设施。若必须改动其中任何一项，说明抽象选错了——这本身是一个有用的信号。

---

# 15. Phase 2：来源无关的输入通路

> §1–§14 描述 Phase 1 并保持有效。本节描述 Phase 2 **叠加**上去的部分。
> 契约细节以 `docs/ARCHITECTURE_FREEZE_V3.md` 为准；本节只讲实现如何落地。

## 15.1 一句话

把 Phase 1 的「账户 → RawTweet → 落盘」泛化为：

```
Unit（X 账户 或 RSS 来源）
  ↓
Provider / Fetcher        怎么拿到字节（HTTP、超时、重试、故障转移）
  ↓
Raw Response
  ↓
Parser                    怎么读懂字节（HTML / RSS 2.0 / Atom）
  ↓
Raw 记录                  每种来源一个形态：RawTweet / RawRSSItem / RawYouTubeItem
  ↓
Normalizer                纯函数：来源形态 → 统一契约
  ↓
NormalizedItem            ★ 唯一落盘形态
  ↓
Storage                   data/items/<source_id>.jsonl
```

**关键点：Pipeline 是同一条，只有 Parser 与 Normalizer 的 kind 不同。** Unit 是「被追踪的东西」，不是「X 账户」——这是 Phase 2 与 Phase 1 最大的概念差异。

## 15.2 新增目录

```
X-Reader/
├── normalizers/                 来源形态 → 统一契约（纯函数，无 I/O）
│   ├── base.py                  BaseNormalizer：只共享「有效性闸门」
│   ├── x.py                     RawTweet       → NormalizedItem
│   ├── rss.py                   RawRSSItem     → NormalizedItem
│   ├── youtube.py               RawYouTubeItem → NormalizedItem（仅接口）
│   └── registry.py              kind → normalizer
├── outputs/
│   └── base.py                  OutputAdapter 接口 + DeliveryResult（无实现）
└── data/
    ├── items/<source_id>.jsonl  ★ 规范存储（NormalizedItem）
    ├── accounts/<unit>.jsonl    Phase 1 原始记录归档（仅证据）
    ├── runs/<YYYY-MM-DD>.jsonl  审计
    ├── raw/<unit>/…             原始响应体（可选）
    └── delivery/<output>.jsonl  每个输出的投递状态
```

`data/accounts/` **保留 Phase 1 的名字**：重命名一个装着已提交历史的目录只是为美观做数据迁移。它的角色是「每单元的原始记录归档」——今天只有 X 写入，且**不在 Phase 2 数据流上**。

## 15.3 三个组件互不认识，绑定集中在一张表

| 组件 | 目录 | 负责 | 明确不负责 |
| --- | --- | --- | --- |
| Provider | `providers/` | 端点、路由、HTTP、超时、重试、故障转移、错误映射 | 不解析、不归一化、不落盘 |
| Parser | `parsers/` | 字节 → Raw 记录（HTML / RSS 2.0 / Atom / RDF） | 不联网、不落盘、不去重、不产出输出格式 |
| Normalizer | `normalizers/` | Raw 记录 → `NormalizedItem` | 不联网、不落盘、不知道是哪个 Provider 产出的字节 |
| Storage | `storage/` | 落盘、按 `identity_key` 去重、增量、审计、投递状态 | **只知道 `NormalizedItem`**，不认识 Raw 记录，不知道 X / RSS / YouTube |

绑定表在 `app/registry.py`：

```python
_BINDINGS: dict[tuple[str, str], Binding] = {
    ("nitter", "rss"):    Binding(NitterRssParser,  "x"),
    ("nitter", "html"):   Binding(NitterHtmlParser, "x"),
    ("xtf",    "search"): Binding(NitterHtmlParser, "x"),   # 同样的字节，同样的读者
    ("rss",    "feed"):   Binding(RssParser,        "rss"),
}
```

**normalizer kind 绑在 route 上，不绑在 provider 上。** 否则同一条推文会因为「哪个 provider 先应答」而获得两个身份——正是身份规则要防止的失败。

## 15.4 身份

```
identity_key = f"{source_id}:{item_id}"      # 派生属性，不落字段
```

- `source_id` 说明**内容是什么**，而不是**怎么拿到的**。`nitter` 与 `xtf` 都是 `source_id = "x"`。
- 保留命名空间：`x`、`youtube`。其他一律是 web / RSS 来源，用站点 slug（小写）。
- 同一个 `item_id` 在不同 `source_id` 下**必须并存**（`x:12345` 与 `youtube:12345` 是两件事）。

| 来源 | `item_id` 来源 | 依据记录 |
| --- | --- | --- |
| X | `tweet_id` | —— |
| RSS | `guid`/`id`，**仅当它与链接不同**；否则 URL 归约 | `metadata["identity_basis"] = "guid" \| "url"` |
| YouTube | `video_id` | —— |

`identity_basis` 存在的意义：真实 feed 普遍把 `guid` 设成链接（本工作区的真实 feed 是 24/24），那不是标识符。把「用了弱的那个」记录下来，比让它静默等价于强的情况要好。

## 15.5 时间：`published_at` 与 `fetched_at` 是两件事

| 字段 | 含义 | 谁提供 |
| --- | --- | --- |
| `published_at` | 来源声称的发布时间，ISO-8601，有时区则带时区 | 来源 |
| `published_at_raw` | 来源的原始渲染，**逐字保留** | 来源 |
| `published_precision` | `second` / `minute` / `hour` / `day` / `unknown` | Parser 从字符串实际内容推导 |
| `fetched_at` | **我们**看到它的时间 | Runner（来自 `RawResponse.fetched_at`） |

两条规则：

1. **绝不用相对时间推算绝对时间。** Nitter 把一条新推文渲染成 `3h`，那不是时间戳，`published_at` 保持 `None`。
2. **绝不发明时区。** 只给日期的值保持 `YYYY-MM-DD`（不提升到午夜）；无时区的日期时间保持无时区。时区归属是**来源适配层**的决定，不是纯 Normalizer 的猜测——本工作区的真实上游把 JST 解析后标成 UTC，误差 9 小时。

## 15.6 Storage：两个接口，Seen 与 Delivery 分离

```python
class BaseStorage(ABC):          # 规范存储，只知道 NormalizedItem
    known_keys(source_id=None)   # 「我见过吗？」
    save(items)                  # 幂等；首写优先
    record_run(record)
    capture_raw(scope, provider, route, response)

    delivery_state(output)       # 「投递给某个输出了吗？」—— 不同的问题
    pending_for(output, keys)
    mark_delivered(output, keys, status, detail)

class RawRecordArchive(ABC):     # Phase 1 原始记录，仅证据
    known_ids(unit)
    archive_raw_tweets(unit, tweets)
```

**为什么必须分开。** 本工作区的参考聚合器把**抓到的全部**链接写进 `history.json`（即「已见」），但只发布前 `MAX_ITEMS=50` 条。于是第 51 条之后既不会发布，也不会重试——永久丢失。把「已见」当成「已投递」，是内容丢失的经典成因。

分开之后：投递失败**不会**删除条目，重新投递**不需要**重新抓取，而且一个输出失败不影响另一个。

## 15.7 RSS 输入是通用的，没有来源专属 Provider

```yaml
rss_sources:
  - id: visualnovel-interview   # 命名空间，小写 slug，不得为 x / youtube
    url: https://example.com/rss.xml
    enabled: true
```

- 任何能产出标准 RSS / Atom 的项目，只要把 URL 列进来就成为来源。
- **没有 `VisualNovelProvider`。** X-Reader 不依赖 `VisualNovel-Interview-RSS-main` 的 Python 代码，也不修改它。
- `accounts` 与 `rss_sources` 是**两个独立配置空间**。绝不把 RSS 来源伪装成 X 账户。

**契约适配的责任在 X-Reader 一侧。** 那个真实 feed 的 24 条里 `guid == link` 24 次、只有 12 条有日期、21 条有 enclosure、有 1 条重复 URL——这些由 X-Reader 的输入契约吸收，而不是要求它改变。

## 15.8 Output 只到接口

`outputs/base.py` 定义 `OutputAdapter.emit(items) -> DeliveryResult`。

> **Phase 3 更新**：本节描述的是 Phase 2 的状态——当时**没有任何具体实现**。Phase 3 实现了第一个适配器（Telegram），见 §16。本节的契约与推论**未做任何放宽**：适配器仍然不记录自身状态、不改模型、不写 `data/`。QQ / OneBot 与 RSS 输出**仍未实现**。

依赖方向必须是单向的：

```
NormalizedItem → OutputAdapter → DeliveryResult
```

而**不是** `OutputAdapter → NormalizedItem → … → Storage`。

两条推论：

1. **适配器不自己记录投递状态。** 它返回 `DeliveryResult`，由调用方通过 Storage 持久化；否则会出现第二个事实来源。
2. **排版是渲染器的事，不是模型的事。** Telegram 把一条渲染成 `[from @username](canonical_url)` + 内容；YouTube 与网站用同一组字段（`publisher`、`canonical_url`、`content`）拼出各自的形态。往 `NormalizedItem` 里加 `telegram_text` / `markdown` 字段，等于让一个适配器的展示需求决定所有适配器和所有已存记录。

## 15.9 YouTube：只到接口与最小契约

`normalizers/youtube.py` 与 `domain/models/youtube_item.py` 存在，用于**证明冻结契约能吸收一种形态完全不同的来源**（有频道、有标题、有缩略图、有时长与播放量）。

**没有 YouTube Provider。** 不引入官方 API、API key、yt-dlp 或任何第三方服务。每一条不需要 API 的路线都需要运维决策，不是本阶段可以替用户做的。

YouTube 也是「`publisher` 这个名字选对了」的证据：频道既不是 author，也不是 account。

## 15.10 失败隔离（Phase 2 扩展）

Phase 1 的隔离规则（§6）保持不变，新增两处：

| 失败位置 | 处理方式 |
| --- | --- |
| Normalizer 抛异常 | 归为 `normalizer_error`（**我们**的 bug，不是上游的），记入该路由的 attempt，换下一条路由；不影响其他 Unit |
| 一批记录归一化后为 0 条有效项 | 当作「空页」处理：试下一条路由，**不**声称成功。诚实报告比虚假成功重要 |

## 15.11 `fetch.limit` 只作用于 X

`fetch.limit` 是为 Nitter「一页一次请求」设计的安全上限（见 §6 与 `app/runner.py` 注释）。**它不截断 RSS**：feed 是整份文档，截断只会丢真实内容；而在默认 `on_error` 留存策略下还不留证据，被丢掉的条目会一直不可达，直到 feed 恰好把它们轮转回窗口内。本工作区的真实 feed 有 24 条，默认上限 20 会静默截断 4 条。

## 15.12 Phase 2 的验收

X 与 RSS 两条链路都收敛于 `Normalizer → NormalizedItem → Storage → OutputAdapter`：

- 同一 `source_id` + `item_id` 重复运行零新增；
- 同一 `item_id` + 不同 `source_id` 必须并存；
- Seen 与 Delivery 在模型与接口层面真正分离，输出失败不丢内容；
- **X-only / RSS-only / X+RSS 三种输入模式均可配置**（§8.1），「无任何输入」被拒绝；
- YouTube 与 Output 只到接口；
- 不引入 SQLite / Redis / 数据库服务 / 消息队列 / 付费服务 / 官方 X API。

验收状态与后续顺序见 `docs/NEXT_PHASE_PLAN.md`（v2.0）。

---

# Phase 3 现状（Telegram Output + Delivery）

## 16.1 一句话

给 Phase 2 的链路接上**第一个真实的输出适配器**，并把「已投递」真正落到存储上，从而证明冻结契约能吸收一个**真实的外部平台**，而不需要改动数据模型、Provider、Parser 或 Normalizer。

## 16.2 完整链路

```
Unit（X 账户 | RSS 来源）
  → Provider → RawResponse → Parser → Raw 记录
  → Normalizer（纯函数） → NormalizedItem
  → Storage                        ← 采集链路的终点
  → pending_for("telegram")        ← 投递链路从这里开始
  → TelegramOutput.emit()
  → mark_delivered()
```

**投递在采集全部结束之后执行一次**，不是每个 Unit 一次。原因不是性能：一次运行的产出属于这次运行，按 Unit 投递会把同一份内容发好几遍。

## 16.3 新增的目录

```
outputs/
├── base.py       OutputAdapter + DeliveryResult（Phase 2 接口，Phase 3 扩了 DeliveryResult）
├── telegram.py   TelegramOutput —— 第一个真实适配器
└── factory.py    配置 → 适配器（与 providers/factory.py 同形）
```

## 16.4 投递状态与「已见」是两件事，且都落在存储上

| 问题 | 接口 | 文件 |
| --- | --- | --- |
| 「我见过这条吗？」 | `known_keys()` | `data/items/<source_id>.jsonl` |
| 「这条投递出去了吗？」 | `delivery_state()` / `pending_for()` / `mark_delivered()` | `data/delivery/<output>.jsonl` |

- **投递失败不删条目**：条目仍在 `data/items/` 里，所以它仍是「已见」的，不会被重复抓取；同时它不是 `sent`，所以会被重投。这是 §16 引用的那个真实缺陷（参考项目把抓到的全部标记为已见、却只发布被截断的前 N 条）的正面回答。
- **命名空间按适配器隔离**：`data/delivery/telegram.jsonl` 与将来的 `data/delivery/qq.jsonl` 互不影响。同一条内容可以既投递到 Telegram 又投递到 QQ。
- 状态只有 `pending` / `sent` / `failed` / `skipped`，**`sent` 是唯一的终态成功**。

## 16.5 为投递新增的两个最小扩展（都不是放宽冻结契约）

1. **`DeliveryResult` 携带 `identity_key` 列表**（`delivered_keys` / `failed_keys` / `skipped_keys`），计数由列表派生。
   原因：投递状态是按 `identity_key` 记录的，只有计数无法告诉调用方**哪些**成功。让计数从列表派生，两者在构造上不可能不一致——和 `identity_key` 是属性而不是字段是同一个理由。
2. **`storage.base.ItemReader`**（第三个接口，仿 `RawRecordArchive` 的先例）。
   原因：投递失败是在**下一次运行**重试的，那时条目早已不在内存里，必须从磁盘读回。把它加进 `BaseStorage` 等于为一个下游消费者的需要去加宽**采集**契约，所以它单独成接口。只读——没有写方法。

## 16.6 Runner 只做编排

`app/runner.py` 里**没有** `httpx`、没有 `api.telegram.org`、没有 4096、没有 HTML 转义。它只做四件事：

```python
pending = storage.pending_for(output.name, sorted(storage.known_keys()))
items   = [i for i in reader.load_items() if i.identity_key in set(pending)]
result  = output.emit(items)
storage.mark_delivered(...)   # 在 emit 之后
```

关于 Telegram 的一切规则都在 `outputs/` 里。

**投递失败不会让运行失败。** 退出码回答的是「采集成功了吗」；Telegram 挂掉不该把一次成功的抓取变成红色构建。失败以 ERROR 日志、报告中的一行、以及 Actions 的 `::warning::` 呈现；需要更严口径的运维可以用 `--strict`。两种情况下条目都保持 pending，下次运行重投，**不需要重新抓取**。

## 16.7 凭据

- 配置里只有**变量名**：`telegram.bot_token_env` / `telegram.chat_id_env`；
- 值只从**环境变量**读取——本地是 shell 变量，CI 是 GitHub repository secret；
- **永不**写进 `config.yaml`、`data/`、日志、报告或 Actions artifact；
- Telegram 的 token 是**请求 URL 的一部分**，所以异常字符串是它最容易泄漏的地方——每一条错误路径都过 `_redact()`；
- `TelegramConfig` 用 `extra="forbid"`：把 `bot_token:` 写进配置会**启动报错**，而不是被静默忽略后显示「token 缺失」。这也让「提交一个 secret 来让它工作」在结构上不可能。

## 16.8 长消息

Telegram 上限 4096，且**按 UTF-16 code unit 计**（表情与生僻汉字算 2）。`len()` 数的是 code point，所以按 `len()` 切分会让一条「看起来没超」的消息被拒——本项目的正文大量是日文，这个差距是真实的。

- 切分在**转义之前**对原始文本做，所以任何一片都不会把一个 HTML 标签切成两半；
- **只有全部分片都被接受，该条目才算投递成功**。前一片成功、后一片失败 ⇒ 该条目仍未投递、下次重投。半个摘要不是投递，把它标记为完成会静默丢掉剩下的部分。

### 16.8.1 连续失败主动中止（Phase 3 实测发现）

发送是**串行**的，所以「网络不通」这类系统性问题会让**每一条**都白等一次超时。Phase 3 的端到端实测把这个代价量化了：**31 条待发送 + 不可达主机 = 62 秒**。真实积压到几百条时，这个数字会超出工作流 20 分钟的上限，job 会在**写入途中**被杀死——那才是真正会丢东西的场景。

因此：**连续 5 条失败即中止本轮**，剩余全部保持 pending。

- 中止**不丢任何东西**：未发送的条目状态不变，下次运行全部补发；
- **一条成功即重置计数器**，所以偶发的单条问题不会毒化整批；
- 报告里会明确写出中止原因与剩余条数。

## 16.9 Phase 3 的边界

> **本节是 Phase 3 结束时的快照。** Phase 4 之后，QQ / OneBot 与 YouTube Provider 已经实现（见 §17）；仍未实现的只剩 AI 摘要 / LLM API、RSS 输出、Markdown 渲染器、数据库 / 消息队列。

**当时没有实现**：QQ / OneBot、YouTube Provider、AI 摘要 / LLM API、RSS 输出、Markdown 渲染器、数据库 / 消息队列。

**没有修改**：`NormalizedItem` 的字段、Provider / Parser / Normalizer / Storage 的既有接口、四个参考项目。

`NormalizedItem` 上**没有** `telegram_text` / `telegram_html` / `telegram_message_id` / `telegram_chat_id` / `markdown` / `rendered_text`——排版是渲染器的事。

## 16.10 Phase 3 的验收

- 链路闭环：`Storage → pending_for("telegram") → TelegramOutput → mark_delivered()`；
- 投递失败不丢条目，且**不重新抓取**即可重投（`test_a_failed_delivery_is_retried_without_refetching` 在**没有任何 Provider** 的情况下完成重投）；
- `telegram` 与 `qq` 命名空间互不影响；
- 长消息分片：全部分片成功才算投递；
- 未配置 Telegram 时，核心链路的行为与 Phase 2 完全一致（无适配器、无 `data/delivery/`、报告不出现投递行）；
- 适配器不写 `data/`、不读 Provider、不改 `NormalizedItem`；
- 不引入 SDK、数据库、消息队列或付费服务。

验收状态见 `docs/NEXT_PHASE_PLAN.md` §7 与 Phase 3 Completion Report。

---

# Phase 4 现状（YouTube 输入 + QQ / OneBot 输出）

## 17.1 一句话

Phase 4 **同时**加了第三个输入端（YouTube）和第二个输出端（QQ），两者都没有新开一条通路：YouTube 走的是 Phase 2 就冻结的 `Provider → Parser → Raw 记录 → Normalizer → NormalizedItem → Storage`，QQ 走的是 Phase 3 就冻结的 `pending_for(output) → OutputAdapter.emit() → mark_delivered()`。

因此 Phase 4 的实质是**证明这两条通路是通用的**，而不是新增架构。

## 17.2 YouTube 输入

链路：

```
youtube_channels 配置（channel_id）
  → YoutubeProvider.fetch(unit, "feed")
  → GET https://www.youtube.com/feeds/videos.xml?channel_id=<channel_id>
  → YoutubeParser.parse() → list[RawYouTubeItem]
  → YoutubeNormalizer.normalize() → list[NormalizedItem]
  → JsonlStorage.save() → data/items/youtube_<channel_id>.jsonl
```

**为什么是订阅源而不是 Data API。** 频道订阅源是公开的、匿名的、免费的，没有 key、没有配额、没有凭据要管；Data API 三样都要。冻结契约禁止付费基础设施，也要求 CI 上可长期无人维护地运行，所以订阅源是唯一站得住的选择。

**身份。** `source_id = "youtube:<channel_id>"`，`item_id = <11 字符视频 ID>`，因此 `identity_key = "youtube:<channel_id>:<video_id>"`。

- `source_id` 用 `channel_id` 而**不是**频道名：频道名可以随时改，用它做身份会在改名那一刻把整份历史重新识别一遍。这与 RSS 的 `id` 是同一条原则（§15.4）。
- `item_id` 用视频 ID 而**不是**标题或链接：标题会改、链接参数会变，两者都不是身份。
- 频道 id 缺失时（畸形 feed）回退为裸 `youtube`，这样条目至少是可寻址的，而不是被静默丢弃。

**字段映射。** 标题 → `title`；简介 → `content`；频道名 → `publisher`（展示用，不参与身份）；`published` → `published_at`（源给的就是带时区的 ISO-8601，**不需要时区适配层**，DR-13 无需 shim）；`published` 原样 → `published_at_raw`；缩略图 → `media`；`channel_id` / `duration` / `view_count` → `metadata`。

**没有给 `NormalizedItem` 加任何视频字段。** `video_id`、`channel_id`、`thumbnail`、`duration`、`view_count` 全部落在既有字段或 `metadata` 里——`scripts/verify_architecture.py` 会把这些名字列为禁用字段，防止以后有人「顺手」加上去。

**不抓视频页面。** 简介只取订阅源里给的。抓页面慢、容易触发风控，而且订阅源已经够用。

**解析器复用与不复用。** 时间解析复用 `parsers/rss.py::parse_feed_time`（同一份实现，避免 DR-13 出现两个版本）；除此之外 YouTube 解析器独立于 RSS 解析器——`yt:` / `media:` 命名空间、`media:group` 结构与通用 RSS 差异足够大，把两者塞进一个解析器只会让两边都难改。

## 17.3 QQ / OneBot 输出

链路：

```
pending_for("qq") → 读回 NormalizedItem → QQOutput.emit()
  → POST <api_base>/send_group_msg
  → mark_delivered("qq", keys, sent|failed)
```

**为什么是 OneBot 而不是 QQ 官方机器人 API。** 官方接口要 AppID/Secret、要审核、要一个能接收回调的常驻服务；OneBot 只需要一个**已经存在的** HTTP 端点。X-Reader 因此是 OneBot 的**客户端**，永远不是 QQ 机器人服务器、不是长连接客户端、不是常驻进程——这三样都会撞上「免费 Actions、无 VPS、无 NAS」的冻结约束。

**四个概念被刻意分开命名**（它们是运维事故的高发区）：

| 名字 | 是什么 | 是秘密 |
| --- | --- | --- |
| `qq.group_id` | 消息发到哪个群 | 否 |
| `qq.api_base` | OneBot HTTP 接口在哪 | 否 |
| `qq.access_token_env` | 存令牌的**变量名** | 否 |
| `ONEBOT_ACCESS_TOKEN` | 令牌本身 | **是** |

群号与令牌无关；`access_token_env` 与令牌本身无关。`access_token_env` **在 enabled 时也不是必填**——不设令牌的 OneBot 是正常部署，强制填一个名字只会逼出占位符。

**成功只有一个判据：`retcode == 0`。** HTTP 2xx 但 `retcode` 非 0、或响应体解析不出来，一律算失败。这个方向是刻意选的：假失败最多下次重发一条，假成功会永久丢掉内容。

**消息是纯文本。** 标题用 `【】` 包裹，正文与来源/时间/链接分行。**不发 CQ 码**：CQ 码是 OneBot 的富文本形式，从渲染器里直接吐 CQ 码，正是「标题里的 `[CQ:at,qq=...]` 被当成真的 @」这类事故的来源。载荷因此是单个 `text` 段，内容是字面量，由构造保证。

**失败隔离与 Telegram 完全一致**：连续 5 条失败即中止本轮，剩余保持 pending。理由是串行发送下网络问题会让每条都白等一次超时，可能拖垮工作流时限；中止不丢内容。

## 17.4 两个输出如何共存

Telegram 与 QQ 是**两个独立的 `OutputAdapter`**，各自有命名空间、各自有投递状态文件：

```
data/delivery/telegram.jsonl
data/delivery/qq.jsonl
```

因此「同一条内容：Telegram=sent、QQ=failed」是**两个文件里的两个事实**，不是一个被折中的状态。下一次运行：

- Telegram 看到 `sent` → 跳过；
- QQ 看到 `failed` → 重发。

一个输出挂掉既不会让另一个重发，也不会让任何条目被误标为「两边都发过了」。这正是 Phase 3 把 Seen 与 Delivery 分开、并按 output 分命名空间的收益——**Phase 4 没有改动 Storage 一行**就得到了多输出语义。

两个适配器**互不 import**，共享的只有 `outputs/base.py` 里那三个与协议无关的东西：`MAX_CONSECUTIVE_FAILURES`、`redact()`、`record_batch_failure()`。抽取标准是「两个适配器都需要，且与协议无关」，而不是「看起来有点像」。

## 17.5 Phase 4 新增的目录

```
providers/youtube.py            YouTube 频道订阅源 Provider
parsers/youtube.py              YouTube Atom 解析
outputs/qq.py                   QQ / OneBot 适配器
scripts/smoke_youtube.py        YouTube 链路冒烟（真实 socket）
tests/fixtures/youtube_*.xml    自建频道 feed（两个频道，用于隔离测试）
```

`outputs/base.py` 新增了三个共用工具（不是新接口）；`outputs/telegram.py` 改为使用它们，行为不变。`domain/models/item.py` **未改动**。

## 17.6 Phase 4 的边界

**仍未实现**：AI 摘要 / LLM、RSS 输出渲染器、GitHub Pages feed、Markdown 渲染器、数据库 / 消息队列 / Celery、Web 服务、QQ SDK、Telegram SDK、`OutputManager` / `SourceManager` 层。

**没有修改**：`NormalizedItem` 的字段与语义、`BaseProvider` / `BaseParser` / `BaseNormalizer` / `BaseStorage` / `OutputAdapter` 的既有接口、Phase 1–3 的既有模块行为、四个参考项目。

**没有走捷径**：YouTube 没有绕过 `NormalizedItem`（它经过 Normalizer）；QQ 没有绕过 `OutputAdapter`（它是适配器）；没有把 YouTube 塞进 RSS Provider（`parsers/rss.py` 与 `parsers/youtube.py` 是两份实现）；没有把 Telegram 适配器复制一份改个 URL 变成 QQ（两者只在 `outputs/base.py` 的协议无关工具上重合）。

## 17.7 Phase 4 的验收

- 三个输入源（X / RSS / YouTube）在同一次运行里共用**一个** Storage，各自一份文件，互不干扰；
- 两个输出（Telegram / QQ）各自一份投递状态，一个失败不影响另一个；
- YouTube：≥18 个解析/归一化/Provider/集成用例，全部离线（fixture，不发真实网络请求）；另有 `scripts/smoke_youtube.py` 用**真实 socket** 跑通整条链路，`--live` 模式直连 `youtube.com`；
- QQ：适配器单元测试 + 一个**本地假 OneBot HTTP 服务器**的真实 socket 往返；**不伪造成功**——没有真实 OneBot 时用 mock 服务器验证传输层，而不是把测试标记成通过；
- Telegram 回归：8 项行为（enabled / disabled / 成功 / 失败 / 重投 / 去重 / 令牌脱敏 / 分片）全部保持；
- `scripts/verify_architecture.py` 增加了 Phase 4 专项断言：YouTube 不 import storage/outputs、`QQOutput` 必须是 `OutputAdapter` 子类、Telegram 与 QQ 互不 import、`NormalizedItem` 上不得出现视频/消息字段；
- `scripts/verify_readme.py` 要求 README 的示例运行报告与 `render_report()` 的输出**逐字节一致**。

验收状态见 Phase 4 Final Report。
