# X-Reader 架构冻结 v3

**文档版本**: v3.0
**日期**: 2026-09-28
**依据**: 根目录 `Architecture Freeze v2.md`（Phase 1 约束，继续有效）、`AGENTS.md`、用户下发的 v3 冻结要求
**适用范围**: 数据契约（Raw Item / NormalizedItem / Identity / Time / Media / Metadata）、Storage 边界、Output 边界
**性质**: 设计冻结文档。**本次不实现 Normalizer、不实现任何 Provider、不实现任何 Output。**

> **实施状态（2026-09-28 追加，不修改下文任何内容）**
>
> 本文件是**冻结记录**，正文逐字保持不变，作为后续实现的对照基准。
>
> 其中描述的契约已于 **Phase 2** 落地：`NormalizedItem` 已成为正式领域契约，
> X / RSS 两个 Normalizer 已实现，Storage 已按 §11 拆成
> `BaseStorage` + `RawRecordArchive` 两个接口，Seen 与 Delivery 已分离，
> `OutputAdapter` 与 YouTube 已按 §13 / §14 只做到接口。
>
> 落地细节与执行顺序见 `docs/ARCHITECTURE.md` §15 与 `docs/NEXT_PHASE_PLAN.md`（v2.0）。
> 实现过程中新增的三条判断（`fetch.limit` 不截断 RSS、Atom `<author>` 的命名空间、
> 「只有 RSS、没有 X」的配置限制）记录在 `NEXT_PHASE_PLAN.md` 的决策表与已知限制中，
> **不是对本文件的修改**。

> 本文件不取代 `docs/ARCHITECTURE.md`（Phase 1 现状说明）。二者关系：
> `ARCHITECTURE.md` 描述**已经建成的东西**；本文件描述**将要建成的契约**。
> `docs/NEXT_PHASE_PLAN.md` 中的 Phase 2 事项顺序由本文件 §G 收窄并覆盖。

---

## 1. v3 背景

Phase 1 已完成并冻结：X 内容获取 + 原始数据持久化，链路到 Storage 结束，222 个测试通过（本次新增 27 个契约验证测试，合计 249）。

Phase 1 的数据模型 `RawTweet`（`domain/models/tweet.py`）是为**单一来源**设计的，字段名直接对应 Nitter 的渲染结果。一旦引入第二个来源，问题立刻出现：

| 问题 | 具体表现 |
| --- | --- |
| 字段名绑死来源 | `tweet_id`、`author_username`、`is_retweet` 只在 X 语境下成立 |
| 必填字段假设错误 | `RawTweet.text` 有默认值但语义上是必填；而 YouTube 有 `title` 无 `text`，网站两者都有 |
| 身份概念混在一起 | `tweet_id` 既是来源内 ID，又被当作全局唯一键 |

v3 要解决的不是「把 `NormalizedTweet` 改名为 `NormalizedItem`」，而是：

> **用 X、YouTube、`VisualNovel-Interview-RSS-main` 三个真实来源，验证一个足够小、稳定、长期可维护的统一数据契约。**

注意「三个真实来源」的**证据强度并不相同**，本文件全程区分：

| 来源 | 证据强度 | 依据 |
| --- | --- | --- |
| **X** | 强（真实字节） | `tests/fixtures/nitter_timeline.html` 走 Phase 1 真实解析器 |
| **网站 / RSS** | 强（真实产物） | `VisualNovel-Interview-RSS-main` 的 11 个解析器、`models/item.py`、已提交的 `rss.xml`（24 条真实条目，原样复制为 `tests/fixtures/vnovel_rss_sample.xml`）、`history.json`（3277 条） |
| **YouTube** | 弱（仅接口） | 工作区内无任何真实样本。仅按 YouTube 最小暴露字段验证契约**是否够用**，不代表已对接真实数据 |

---

## 2. 当前架构（Phase 1，不改动）

```
Account → Provider/Fetcher → Raw Response → Parser → RawTweet → Storage
```

| 层 | 位置 | 职责 | 不得做的事 |
| --- | --- | --- | --- |
| Provider | `providers/` | 决定「怎么拿到数据」：endpoint、route、failover、HTTP | 不认识 tweet 结构，不解析 |
| Parser | `parsers/` | 决定「怎么理解来源数据」：把 bytes 变成 `RawTweet` | 不做网络 I/O，不碰 Storage |
| Registry | `app/registry.py` | 唯一的 `(provider, route) → parser` 绑定表 | 不含业务逻辑 |
| Storage | `storage/` | 决定「怎么保存」：JSONL 追加、去重、审计 | 不认识 provider / RSS / Summary |
| Runner | `app/runner.py` | 编排一轮抓取，隔离单账户失败 | 不产出 RSS |

**必须保留**（v3 不重新设计）：`config`、`domain`、`infrastructure/http`、`providers`、`parsers`、`storage`、`app`、GitHub Actions、JSONL storage、账户隔离、retry / failover、run report、全部现有测试。

**v3 新增一层**，插在 Parser 与 Storage 之间：

```
Provider → Parser → Raw Item → Normalizer → NormalizedItem → Storage → Output
```

---

## 3. 多来源目标

```
                    ┌── X
                    ├── YouTube
Sources ────────────┼── Website / RSS
                    └── Future Providers
                           ↓
                       Provider      「怎么拿到」
                           ↓
                        Parser       「怎么理解」
                           ↓
                      Raw Item
                           ↓
                      Normalizer     「怎么转换成统一内容」
                           ↓
                  NormalizedItem
                           ↓
                       Storage       「怎么保存和追踪」
                           ↓
                   New / Updated Items
                           ↓
                     Output Layer    「怎么发送/展示」
                    ┌──────┼──────┐
                   RSS     TG     QQ
```

**本次只设计 Output 接口，不实现 Telegram / QQ。**

一条贯穿原则：**下游不得反向污染上游数据模型。** 任何让 `NormalizedItem` 的字段为了某个 Bot 的渲染需求而改变的改动，都属于越界。

---

## 4. Raw Item 定义

Raw Item 是「解析器对来源字节的投影」，**来源特定，不统一**。

| Raw 类型 | 状态 | 说明 |
| --- | --- | --- |
| `RawTweet` | **已存在** | `domain/models/tweet.py`，Phase 1 产物，不改 |
| `RawYouTubeItem` | 待建（Phase 2） | 建议字段：`video_id` / `channel` / `channel_id` / `title` / `description` / `published_at` / `thumbnail` / `duration` / `view_count` / `raw` |
| `RawWebItem` | 待建（Phase 2） | 建议字段：`site` / `guid` / `title` / `summary` / `url` / `published_raw` / `image_url` / `section` / `raw` |

Raw Item 的三条不变量（沿用 Phase 1 已验证的原则）：

1. **Raw 必须保留来源原样。** `RawTweet.raw` 保存解析器看到的原始结构，`created_at_raw` 保存来源自己的时间渲染字符串。这样未来可以**不重新抓取**而重新推导。
2. **Raw 不做跨来源统一。** 统一是 Normalizer 的职责。Raw 层出现「为了通用而存在的字段」就是设计错误。
3. **Raw 的类型可以增加，不能修改。** 新增来源 = 新增 Raw 类型 + 新增 Normalizer，不改 `RawTweet`。

---

## 5. NormalizedItem 定义

模型定义见 `domain/models/item.py`（**已建，但未接入任何链路**：`app/`、`providers/`、`parsers/`、`storage/` 均不 import 它）。

### 5.1 字段表

| Field | Type | Required | Meaning / 为什么存在 |
| --- | --- | --- | --- |
| `source_id` | `str` | **是** | 内容属于哪个来源命名空间。`"x"` / `"youtube"` / 网站 slug（如 `"gamer"`）。**它是「内容是什么」，不是「怎么拿到的」**——同一条推文经 `nitter` 或 `xtf` 抓取必须得到同一个 `source_id`，否则同一内容会被存两次 |
| `item_id` | `str` | **是** | 来源自己的条目 ID：推文 ID / 视频 ID / feed guid。来源内唯一，跨来源不唯一 |
| `title` | `str \| None` | 否 | 标题。X 恒为 `None`（推文没有标题）；YouTube 为视频标题；网站为文章标题。**必须可空**，否则真实 X 数据会被契约拒绝 |
| `content` | `str \| None` | 否 | 正文/摘要。X 为推文文本；YouTube 为 description；网站为导语/摘要 |
| `publisher` | `str \| None` | 否 | 来源内的发布主体：X 为 `@handle`，YouTube 为频道，网站为站点名。**取代 `author`**，理由见 §5.2 |
| `canonical_url` | `str \| None` | 否 | 给人看的永久链接。类型上可空，实践中三个来源都能给出 |
| `published_at` | `str \| None` | 否 | **内容发布时间**，ISO-8601，能确定时区时带时区 |
| `published_at_raw` | `str` | 否 | 来源自己的时间渲染原样保留（如 `"Sep 10, 2024 · 3:05 PM UTC"`、`"3h"`）。用于未来重解析 |
| `published_precision` | `str` | 否 | `published_at` 的真实精度：`second` / `minute` / `hour` / `day` / `unknown`。**存在理由见 §7** |
| `fetched_at` | `str` | **是** | **我们观察到它的时间**。永远不能替代 `published_at` |
| `media` | `list[MediaItem]` | 否 | 附件列表，见 §9 |
| `metadata` | `dict` | 否 | 来源特有信息，见 §10 |

### 5.2 为什么是 `publisher` 而不是 `author`

这是本契约最重要的一个字段决策，依据来自真实代码：

- `VisualNovel-Interview-RSS-main/README.md` 明确宣称「保留头图、**作者**、精确发布时间与文章标签」；
- 但它的真实模型 `models/item.py` **根本没有 author 字段**（只有 `site` / `category` / `title` / `link` / `description` / `image_url` / `pub_date` / `tags`）；
- 它的 11 个解析器里，`site` 全部硬编码（`site="GameWatch"` 等），没有任何一个抓取作者。

即：**「作者」在这套真实系统里根本不存在。** 若把 `author` 设为核心必填字段，第三个来源立刻无法映射。

因此冻结为 `publisher`，语义为「来源内的发布主体」，且**允许为空**：

| 来源 | `publisher` |
| --- | --- |
| X | `@username`（推文作者；转发时是原作者，不是被追踪的账号） |
| YouTube | channel |
| 网站 | 站点名（如 `GameWatch`） |

### 5.3 必填规则

只有 3 个字段必填：`source_id`、`item_id`、`fetched_at`。

此外有一条**意义性约束**：`title` 与 `content` 至少一个非空，否则该条目没有任何内容，判定为无效（`is_meaningful()`）。

**刻意不要求 `canonical_url`**：身份是 `source_id:item_id`，没有 URL 的条目仍然完全可以去重、可以入库。

### 5.4 身份是派生属性，不是字段

```python
@property
def identity_key(self) -> str:
    return f"{self.source_id}:{self.item_id}"
```

`identity_key` **不作为字段存储**。存下来的副本可能在编辑后与自身组成部分不一致；属性不可能。

---

## 6. Identity 规则

### 6.1 三个概念的职责

| 概念 | 职责 | 是否唯一 | 示例 |
| --- | --- | --- | --- |
| `source_id` | 命名空间，决定 `item_id` 如何解释 | 否 | `x`、`youtube`、`gamer` |
| `item_id` | 来源内条目 ID | **来源内**唯一 | `1000000000000000001`、`dQw4w9WgXcQ`、`https://.../84674` |
| `canonical_url` | 人类可读永久链接 | 否 | `https://x.com/jack/status/...` |
| `identity_key` | **跨来源唯一去重键**（派生） | **全局**唯一 | `x:1000000000000000001` |

### 6.2 七个必答问题

**Q1：`source_id` 如何定义？**
来源命名空间，取值来自**显式配置**，**不得在运行时从展示名推导**。

> 反例：网站来源的展示名是硬编码字符串（`"Game*Spark"`、`"Dengeki Online"`）。若用 `lower().replace("*","")` 之类规则现场推导 slug，一旦上游改名（`Game*Spark` → `Game Spark`）身份就会漂移，历史数据全部对不上。
> 因此 slug 必须在来源配置里显式声明，且**一旦发布不得修改**。

`"x"` 与 `"youtube"` 为保留名（`RESERVED_SOURCE_IDS`），网站 slug 不得占用。

**Q2：`item_id` 从哪里来？**
按以下优先级取，取到即停：

1. 来源自带的独立 ID：X 的 `tweet_id`、YouTube 的 `video_id`、RSS 的 `<guid>`（**当且仅当 guid ≠ link**）；
2. 若 guid 缺失或退化为 URL，则从 `canonical_url` 推导；
3. 都不可得 → 该条目不产生（`item_id` 为空 = 不可去重，拒绝入库）。

**Q3：RSS 没有可靠 GUID 怎么办？**
降级到 URL 推导，并在 `metadata["identity_basis"]` 中记录 `"guid"` 或 `"url"`，**让弱点在数据里可见，而不是被隐藏**。

真实情况比预期更糟：`VisualNovel-Interview-RSS-main` 的 24 条真实条目中，`guid` **全部等于** `link`（实测 `guid == link` 对所有条目成立）。也就是说这个来源**实际上没有任何独立 ID**，身份 100% 建立在 URL 上。

**Q4：URL 是否可以作为 fallback identity？**
可以，但**只能用于推导 `item_id`**，不能把 URL 本身当作 `identity_key`。理由是 Q5。

**Q5：URL 改变后是否仍认为是同一条内容？**
- `item_id` 来自**来源独立 ID**（推文 ID / 视频 ID）→ **是**，URL 改写不产生新条目（有测试 `test_identity_survives_a_url_change`）。
- `item_id` 来自 **URL 推导** → **否**，会被判定为新条目。

这是**被显式接受**的降级代价，不是疏忽。缓解手段是 `metadata["identity_basis"]`，以及未来若某个来源提供真实 ID，改用真实 ID。

**Q6：是否需要 `identity_key`？**
需要。仅有 `item_id` 不够：两个网站完全可能用同一个数字 ID（`.../article/123`）。`identity_key` 提供跨来源唯一性，且**派生而非存储**，杜绝漂移。

**Q7：X / YouTube / 网站能否共用同一套 dedup 接口？**
能，且已验证。统一接口只依赖一个字符串：

```python
def known_ids(...) -> set[str]        # 返回已存在的 identity_key 集合
def save(items: list[NormalizedItem]) # 按 identity_key 去重后追加
```

三个来源不需要任何来源特有知识。

### 6.3 去重必须在**批次内**也生效

真实证据（`test_web_real_feed_contains_a_duplicate_item`）：

- `VisualNovel-Interview-RSS-main` 已提交的 `rss.xml` 中，`https://dengekionline.com/article/202609/84674` **出现了两次**（标题、pubDate 完全相同）；
- 它的 `history.json` 有 **3277 条链接，其中仅 3184 条唯一**（93 条重复）。

根因：`main.py` 只用 `item.link not in history` 过滤，**从不与同批次比较**；而 `utils/dedup.py` 里的 `remove_duplicates()` **定义了但从未被任何代码调用**（实测全仓库无调用点）。

**冻结结论：去重是共享层的职责，按 `identity_key` 对批次与历史同时生效，不得下放给各来源自己实现。**

---

## 7. Time 语义

### 7.1 字段职责

| 字段 | 代表什么 | 谁产生 |
| --- | --- | --- |
| `published_at` | **原内容发布时间**（来源的说法） | 来源 |
| `published_at_raw` | 来源自己的时间字符串 | 来源 |
| `published_precision` | `published_at` 的真实精度 | 来源 |
| `fetched_at` | **我们抓到它的时间**（我们的说法） | X-Reader |

**铁律：抓取时间不得冒充发布时间。**

Phase 1 已经遵守这条：`RawTweet.created_at` 与 `fetched_at` 是两个字段，且相对时间**绝不**推导为绝对时间。

### 7.2 `published_precision` 为什么必须存在

真实证据（`test_web_date_only_upstream_is_indistinguishable`）：

- 24 条真实条目中只有 **12 条**有 `pubDate`；
- 有日期的 12 条中，**1 条的 `pubDate` 恰好是 `00:00:00 +0000`**；
- 上游 `gamewatch.py` 只正则提取 `(YYYY/M/D)`（`r"\((\d{4})/(\d{1,2})/(\d{1,2})\)"`），**不含时间**，随后 `main.py` 把 naive datetime 补成 UTC → 变成「午夜零点」。

结果：**「当天发布」被伪装成「精确到秒的午夜发布」**，而下游从 feed 里**无法分辨**。若没有 `published_precision`，一个 `published_at` 会同时承载两种完全不同的可信度。

X 侧同样需要：真实 fixture 里 `1000000000000000008` 的时间渲染是 `3h`（相对时间），解析器正确地给出 `created_at = None`——**这条推文没有可用的发布时间**（`test_x_relative_time_is_never_promoted_to_an_absolute_date`）。

### 7.3 时区规则（基于发现的真实缺陷）

**冻结规则：`published_at` 必须是真实时刻（正确时区换算后的 ISO-8601）。若来源时区无法确定，则 `published_at = None`，保留 `published_at_raw`，`published_precision = unknown`。绝不允许把未知时区的本地时间直接标注为 UTC。**

该规则不是假想。实测 `VisualNovel-Interview-RSS-main` 存在真实缺陷：

```python
# parsers/fourgamer.py:47-54（dengekionline.py:40 同样）
dt = datetime.strptime(text, fmt)     # 解析的是日本站点的 JST 本地时间
return dt.replace(tzinfo=timezone.utc)  # 却直接标注为 UTC
```

日本站点 JST = UTC+9，因此**时间戳整体偏移 9 小时**。真实产物可复现：`4Gamer` 条目写成 `Mon, 28 Sep 2026 11:45:00 +0000`；若该文章发布于 JST 11:45，真实时刻应为 `02:45 UTC`。`parsers/gamespark.py:131` 用 `tzinfo=None` 后由 `main.py` 补 UTC，属同类问题。

**边界归属：时区解析属于「来源适配层」（它知道这个站点在日本），不属于 Normalizer。** Normalizer 必须是纯函数，它不可能知道站点所在时区；因此它接收到的应当已经是换算正确的时刻，或者明确的 `None`。

### 7.4 `updated_at`：本次不纳入

三个真实来源都不提供条目级更新时间（RSS 的 `<atom:updated>` 在这批 feed 里不存在；X 无更新概念）。**纳入即为推测字段**，故推迟到「有来源真的暴露它」时再评估。见 DR-4。

---

## 8. Content 与 Title 语义

| 来源 | `title` | `content` |
| --- | --- | --- |
| X | `None`（推文没有标题） | 推文文本 |
| YouTube | 视频标题 | 视频 description |
| 网站 | 文章标题 | 导语/摘要（**不是正文全文**） |

**不得假设所有内容都是 `title + content`。**

网站来源的 `content` 需要诚实标注：`VisualNovel-Interview-RSS-main` 的 `description` 是列表页的导语（`p.outline` / feed `summary`），**不是文章正文**。契约允许它作为 `content`，但不应被任何输出方描述为「全文」。若未来需要全文，属于新的抓取能力，不改变本契约。

**网站标题需要一次转换**：VNovel 的输出把站点名塞进标题（`[Gamer] 标题`），因为它自己的 RSS 渲染层没有别的地方放站点。`NormalizedItem` **有**地方放（`publisher`），因此映射时把前缀还原到 `publisher`——这正是统一模型的价值所在。

---

## 9. Media 语义

### 9.1 结构

```python
MediaItem(url, type, mime_type=None, width=None, height=None, thumbnail=None)
```

`type` 取值域封闭：`image` / `video` / `gif`。

### 9.2 与建议的最小形状的差异，及理由

建议的最小形状是 `{url, type, mime_type?, width?, height?}`。本契约**只增加一个字段 `thumbnail`**，且是证据驱动的：

- X 真实 fixture 中视频被渲染为 `<video>` 加一张**独立封面图**，Phase 1 的 `domain/models/media.py` 已经在采集它（`Media.thumbnail`）。删掉 `thumbnail` 会**静默丢失真实信息**。
- YouTube 的缩略图同样需要它。

**不增加 `audio`，也不引入通用 `enclosure` 类型。** 三个真实来源都不产生音频；把一个没有生产者的枚举成员写进封闭取值域，属于推测设计。见 DR-7。

`width` / `height` 保留为可选：目前无来源提供，但 RSS `<enclosure>` 与 YouTube 缩略图接口都能提供，且不需要新类型。

### 9.3 三个来源的媒体差异

| 来源 | 实际形态 | 映射 |
| --- | --- | --- |
| X | 0..N 个附件，可为图 / 视频（带封面）/ GIF | `list[MediaItem]`，视频保留 `thumbnail` |
| YouTube | 1 张缩略图（视频本身由 `canonical_url` 表达） | `[MediaItem(type=image)]` |
| 网站 | 0..1 张头图（feed `<enclosure>`） | `list[MediaItem]`，长度 0 或 1 |

真实样本中 21/24 条有 `enclosure`，3 条没有 → **`media` 必须允许为空列表**。

---

## 10. Metadata 边界

`metadata: dict` 用于保存「真实存在但尚不属于共享契约」的来源特有信息。

### 10.1 当前归属

| 来源 | 进入 `metadata` 的内容 |
| --- | --- |
| X | `account`（被追踪的账号，非作者）、`author_name`、`is_retweet`、`retweeted_by`、`is_reply`、`reply_to`、`is_quote`、`quoted_*`、`stats{replies,retweets,likes,views}`、`provider`、`route` |
| YouTube | `channel_id`、`duration`、`view_count` |
| 网站 | `category`、`identity_basis` |

两个需要解释的归属决定：

- **`provider` / `route` 属于 metadata，不是正式字段。** 它们是「怎么拿到的」而非「内容是什么」；同一条内容在不同轮次可能由不同 provider 抓到。把它们做成正式字段会诱导下游按 provider 分支，从而污染模型。审计信息已在 `data/runs/` 中独立保存。
- **`category` 属于 metadata。** VNovel 的 11 个解析器**全部硬编码** `category="interview"`，即该字段当前是常量，零信息量。同理 `tags` 几乎全为空（仅 `gamebiz` / `gamer` 填充），且**从未进入 RSS 输出**（实测 `rss.xml` 中 `<category>` 出现 0 次）。把常量或无消费者的字段提升为正式字段是过度设计。

### 10.2 晋升规则（metadata 不是垃圾桶）

一个字段从 `metadata` 晋升为 `NormalizedItem` 正式字段，必须**同时**满足：

1. 至少有 **2 个来源**能提供它；或
2. 至少有 **1 个 Output Adapter** 真正消费它，且该消费不是来源特有的；且
3. 它能给出**与来源无关的稳定语义**（不依赖某个站点的页面结构）。

不满足则留在 `metadata`。反向也成立：一个正式字段若长期只有单一来源填充，应考虑退回 `metadata`。

---

## 11. Storage 边界

**方向必须单向：`Provider → Parser → Normalizer → Storage`，绝不允许 `Storage → X / YouTube / RSS`。**

Storage 只认识：

- `NormalizedItem`（以及 `identity_key`）；
- 通用持久化接口。

### 11.1 接口（Phase 2 目标形态）

```python
class BaseStorage(ABC):
    def known_keys(self) -> set[str]: ...                      # 已存在的 identity_key
    def save(self, items: list[NormalizedItem]) -> SaveReport: ...   # 幂等，按 identity_key 去重
    def record_run(self, record: RunRecord) -> None: ...       # 审计，失败不影响数据
    def capture_raw(self, ...) -> str | None: ...              # 原始响应留存
```

与 Phase 1 的 `BaseStorage`（`known_ids(account)` / `save(account, tweets)`）相比，变化只有一处：**不再以 `account` 为主键**。Phase 1 的 `account` 是 X 专有概念（被追踪的 X 账号），YouTube 频道与网站不是「账号」。`account` 因此降级为 X 来源的 `metadata["account"]`。

> **兼容性说明**：这是 Phase 2 的**破坏性接口变更**，需要一次数据迁移或双写过渡。当前 `data/accounts/<username>.jsonl` 的目录布局可保留（X 来源仍按账号分文件），但接口签名必须改变。**本次不实施。**

### 11.2 存储技术不变

继续 JSONL，**不引入 SQLite / 数据库 / 消息队列 / Redis**。Phase 1 的决策依据继续有效（免费 GitHub Actions 下唯一持久化是仓库本身；二进制库无法 diff / delta 压缩 / 合并，损坏即全损）。

Phase 1 已知的唯一扩展性上限是 `known_ids()` 全文件线性扫描；该问题留给 Phase 3（在同一个 `BaseStorage` 接口之后加派生索引），不因 v3 改变。

---

## 12. Output Adapter 边界

### 12.1 接口

```python
class OutputAdapter(ABC):
    name: str

    def emit(self, items: list[NormalizedItem]) -> OutputReport: ...
```

统一输入只有 `NormalizedItem` 列表。Output 层：

- **不得修改** `NormalizedItem`（只读消费）；
- **不得**触发抓取、解析或规范化；
- **不得**成为 Storage 的前置条件（Storage 是事实来源，Output 是下游消费者）。

### 12.2 各输出的需求

| Output | 需要 `NormalizedItem` 的哪些字段 |
| --- | --- |
| `RSSOutput` | `title`、`content`、`canonical_url`、`published_at`、`media`、`publisher`（用于标题前缀）、`identity_key`（作为 guid） |
| `TelegramOutput` | `publisher`、`canonical_url`、`content`、`title`、`media` |
| `QQOutput` | 同 Telegram（经由 OneBot 渲染） |

### 12.3 `[from @username](url)` 属于渲染层

```
[from @username](canonical_url)

content
```

**这是 Telegram 的渲染格式，不是 `NormalizedItem` 的内部结构。**

- `NormalizedItem` 里**没有** `from`、**没有** markdown、**没有** `@` 前缀（`publisher` 存裸 handle，不带 `@`）。
- 「`[from @账号](链接)`」由 `TelegramRenderer` 从 `publisher` + `canonical_url` 现场拼装。
- YouTube 渲染为 `[from YouTube / Channel](url)`，网站渲染为 `[from Website Name](url)`——**同一套字段，三种文案**，全部属于渲染层。

若把这种字符串存进模型，就等于让 Telegram 的排版需求污染了所有其他输出。见 DR-8。

### 12.4 RSS 是输出，不是中间模型

```
X → RSS → Telegram        ✅ 允许，作为一种 Output Chain
所有来源 → RSS XML → 再解析 RSS → Telegram    ❌ 禁止
```

事实模型只有一个：

```
NormalizedItem
      ├── RSS
      ├── Telegram
      └── QQ
```

禁止「先渲染成 RSS 再从 RSS 解析出内容」，因为那会把 RSS 的表达能力当作数据契约的上限（RSS 无法表达 `stats`、`is_quote`、`published_precision` 等）。

---

## 13. X → NormalizedItem 映射

| NormalizedItem | X（`RawTweet`） |
| --- | --- |
| `source_id` | `"x"`（常量） |
| `item_id` | `tweet_id` |
| `title` | `None` |
| `content` | `text` |
| `publisher` | `author_username`（**转发时是原作者**） |
| `canonical_url` | `url` |
| `published_at` | `created_at`（ISO 字符串，可能为 `None`） |
| `published_at_raw` | `created_at_raw` |
| `published_precision` | `created_at` 存在 → `second`；否则 `unknown` |
| `fetched_at` | `response.fetched_at`（由 runner 盖章） |
| `media` | `[MediaItem(url, type, thumbnail)]` |
| `metadata` | `account`、`author_name`、`is_retweet`、`retweeted_by`、`is_reply`、`reply_to`、`is_quote`、`quoted_*`、`stats`、`provider`、`route` |

验证：`tests/test_normalized_item_contract.py` 用真实 `nitter_timeline.html` 走真实 `NitterHtmlParser`，8 条推文全部映射成功，其中含视频（保留封面）、GIF、图片，以及 1 条只有相对时间（`3h`）的推文。

---

## 14. YouTube → NormalizedItem 映射（仅接口）

| NormalizedItem | YouTube |
| --- | --- |
| `source_id` | `"youtube"` |
| `item_id` | `video_id` |
| `title` | 视频标题 |
| `content` | description |
| `publisher` | channel |
| `canonical_url` | `https://www.youtube.com/watch?v=<video_id>` |
| `published_at` / `published_at_raw` | 发布时间 |
| `published_precision` | `second`（有发布时间时） |
| `fetched_at` | 采集时间 |
| `media` | `[MediaItem(url=缩略图, type=image)]` |
| `metadata` | `channel_id`、`duration`、`view_count` |

**最小输入字段**（判定「新视频」的依据）：`video_id` + `published_at`。二者齐备即可完成身份与增量判断。

**本次不引入 YouTube API。** 待确认项见 §19-R3（是否使用 RSS / channel feed / playlist feed）。

**证据强度提示**：工作区内**没有**任何真实 YouTube 样本，上表是按 YouTube 最小暴露字段推出的，只证明契约**够用**，不证明已对接。

---

## 15. VisualNovel-Interview-RSS → NormalizedItem 映射

### 15.1 该项目实际产生的一条内容是什么

**不是**从项目名猜的。实测结论：

> 一条内容 = **某个日本游戏媒体网站上的一篇访谈/专栏文章的列表页条目**。
> 由「一站一解析器」的 11 个解析器之一从列表页（或站点 feed）抓取，产出 `Item`，再由 `main.py` 渲染进 `rss.xml`。

| 项 | 实测结果 |
| --- | --- |
| 目录结构 | `models/`（1 文件）、`parsers/`（11 个站点解析器 + `example.txt` 模板）、`storage/history.json`、`tools/`（6 个维护工具）、`main.py`、`.github/workflows/`（`update-rss.yml`、`debug-parser.yml`、`Parser Validation`） |
| 输入源 | 11 个站点：AUTOMATON、BugBug、denfaminicogamer、Dengeki Online、Famitsu、4Gamer、GAMEBIZ、Gamer、Game\*Spark、GameWatch、NookGaming |
| 抓取方式 | 10 个用 `requests` + `BeautifulSoup` 抓列表页；1 个（`nookgaming_feed.py`）用 `feedparser` 读站点 RSS |
| Parser / Extractor | 每站一个模块，统一暴露 `parse() -> list[Item]`；`main.py` 用 `pkgutil.iter_modules` **自动发现** |
| 数据模型 | `models/item.py`：`Item(site, category, title, link, description, image_url, pub_date, tags)` —— **8 个字段，没有 author** |
| 唯一 ID / GUID | **没有**。`Item` 无 id 字段；11 个解析器**全部**不读 `guid` / `entry.id`（实测无匹配）；RSS 输出里 `guid == link`（24/24 成立） |
| URL | `link`，绝对地址，同时也是唯一身份 |
| 标题 | `title`，输出时前缀站点名 → `[Gamer] ...` |
| 正文/摘要 | `description` = 列表页导语（`p.outline`）或 feed `summary`，**非全文** |
| 作者/网站/栏目 | 网站 = `site`（硬编码）；栏目 = `category`（**恒为 `"interview"`**）；**作者不存在** |
| 发布时间 | `pub_date`，可为 `None`；`gamewatch.py` 只解析到**日**精度 |
| 图片 | `image_url`，单张，可空；输出为 `<enclosure type="image/jpeg">`（mime **硬编码**） |
| RSS 输出字段 | `title`（带站点前缀）、`link`、`guid`(=link)、`pubDate`（可选）、`description`（`<img><br>` + 导语）、`enclosure`（可选）。**`category` 与 `tags` 从不输出** |
| 增量更新判断 | `item.link not in history`，history 为**扁平 URL 列表**（3277 条 / 3184 唯一） |
| 去重逻辑 | 仅对 history 过滤；**批次内不去重**；`utils/dedup.py` 的 `remove_duplicates()` **定义了但从未被调用** |
| state | `storage/history.json`，单文件 JSON 数组 |
| 配置方式 | **无配置文件**。URL、选择器、站点名全部硬编码在各解析器内 |
| CI | `.github/workflows/update-rss.yml`：`schedule: cron "0 0 * * *"` + `workflow_dispatch`，Python 3.11，跑 `python main.py`，提交 `rss.xml` 与 `storage/history.json` |

**一处必须记录的真实缺陷**（`main.py:117-127` + `215-218`）：

```python
all_new_items = new_items.copy()
MAX_ITEMS = 50
new_items = new_items[:MAX_ITEMS]          # 只有前 50 条进入 RSS
...
history.extend(item.link for item in all_new_items)   # 但全部新条目都记入 history
```

单轮新增超过 50 条时，第 51 条之后**被标记为已见却从未发布**，且因 `link not in history` 永远不会再被发布 —— **静默丢失**。这正好是 §16 要求把「已抓取」与「已推送」分开的直接依据。

### 15.2 字段映射表

| NormalizedItem | X | YouTube | VisualNovel-Interview-RSS |
| --- | --- | --- | --- |
| `source_id` | `"x"` | `"youtube"` | 站点 slug，如 `"gamer"`（**需显式配置，见 §6.2-Q1**） |
| `item_id` | `tweet_id` | `video_id` | `<guid>`（实际等于 URL）→ URL 推导 |
| `publisher` | `author_username` | channel | 站点名（从 `[Site] ` 前缀还原） |
| `title` | `None` | 视频标题 | 文章标题（去前缀后） |
| `content` | 推文文本 | description | `description`（导语/摘要） |
| `canonical_url` | `url` | `watch?v=` | `link` |
| `published_at` | `created_at` | 发布时间 | `pubDate`（12/24 有；1 条被上游抹成午夜） |
| `published_at_raw` | `created_at_raw`（含 `"3h"`） | 原始字符串 | `pubDate` 原文 |
| `published_precision` | `second` / `unknown` | `second` | 名义 `second`，**但不可信**（见 §7.2、R2） |
| `fetched_at` | 采集时间 | 采集时间 | 采集时间 |
| `media` | 0..N（图/视频/GIF，视频带封面） | 1 张缩略图 | 0..1（`<enclosure>`） |
| `metadata` | `stats`、关系、`account`、`provider`、`route` | `channel_id`、`duration`、`view_count` | `category`、`identity_basis` |

**没有任何一个来源需要为映射而补字段。** 唯一「不自然」的一格是网站的 `published_precision`——但它的问题**不在契约**，而在上游已丢失精度。按 §16 要求，问题归属分析如下：

| 现象 | 归属 | 决策 |
| --- | --- | --- |
| 网站 `published_precision` 不可信 | **上游 Parser 信息不足**（`gamewatch.py` 只取到日，`main.py` 补成午夜） | 不补字段、不猜测。契约如实承载 `second`；同时记录为风险 R2，并规定来源适配层应尽量直连站点以保留原始精度 |
| 网站无独立 GUID | **上游 Provider 信息不足**（该 feed 没有可用 ID） | 不补字段。降级到 URL 推导 + `metadata["identity_basis"]` 标记 |
| 网站无作者 | **不是问题**，是真实世界 | `publisher` 可选，`author` 不进入契约 |
| X 无标题 | **不是问题** | `title` 可选 |
| `category` 恒为 `interview`、`tags` 为空 | **该字段本来就不该进正式模型** | 留在 `metadata` |
| YouTube 无真实样本 | **待验证**，非设计错误 | 列为风险 R3 |

---

## 16. 增量 / Seen / Delivery State 设计

### 16.1 三个概念必须分开

| 概念 | 含义 | 存储位置 |
| --- | --- | --- |
| **Seen（已抓取）** | 这个 `identity_key` 已在 Storage 中存在 | 数据本体（JSONL）即事实来源 |
| **New（本轮新增）** | 本轮 `save()` 实际追加的条目 | `SaveReport.inserted` |
| **Delivery（已推送）** | 该条目已成功投递到某个输出端 | **各输出独立**的投递状态 |

**关键约束：Storage 是事实来源，Output 是下游消费者。**

```
item → storage → delivery state
```

因此：

- Telegram 推送失败 **绝不**能让内容从 Storage 消失；
- 新增一个输出端（QQ）**绝不**需要重新抓取或重新规范化；
- 输出端的投递记录必须能独立重放，不依赖「本轮是否有新内容」。

### 16.2 为什么必须分开：真实反例

`VisualNovel-Interview-RSS-main` 把「已见」与「已发布」**合并成一个 `history.json`**，后果已在 §15.1 记录：单轮新增超过 50 条时，多出的条目被永久标记为已见、永不发布。**这个缺陷正是本节存在的原因。**

### 16.3 目标状态布局（Phase 2 设计，本次不实现）

```
data/
  items/<source_id>.jsonl          # 事实来源：NormalizedItem，按 identity_key 去重
  runs/<YYYY-MM-DD>.jsonl          # 审计（沿用 Phase 1）
  raw/<source_id>/<ts>__*.html     # 原始响应留存（沿用 Phase 1）
  delivery/<output>.jsonl          # 投递状态：identity_key + status + attempts + ts
```

- `items/` 只增不改（`first write wins`，沿用 Phase 1 语义）。
- `delivery/` 记录 `pending` / `sent` / `failed`，允许重试；**它的存在与否不影响 `items/`**。
- 投递状态按输出端分文件，使新增输出端是**纯增量**操作。

### 16.4 富化（enrichment）留待 Phase 3

Phase 1 的 `first write wins` 意味着「先以较弱的 rss 路由写入，之后更强的 html 路由成功」不会更新已存记录。这是**已知且刻意**的取舍（append-only 保证），本次不改变。见 DR-6。

---

## 17. 明确暂不实现的内容

| 项 | 状态 |
| --- | --- |
| Telegram Bot / `TelegramOutput` | **不实现**，仅冻结接口（§12） |
| QQ Bot / OneBot / NapCat / `QQOutput` | **不实现**，仅冻结接口 |
| `RSSOutput` | **不实现**（Phase 1 已明确 RSS 不是核心功能） |
| YouTube Provider | **不实现**，仅映射设计（§14） |
| VisualNovel-Interview-RSS Provider | **不实现**，仅映射设计（§15） |
| 任何 Normalizer 的实际接入 | **不实现**。`domain/models/item.py` 已建但**未接入链路** |
| AI Summary / LLM | **不实现**，且永远不得成为基础运行时依赖 |
| SQLite / 数据库 / 消息队列 / Redis | **不引入** |
| 常驻服务器 / VPS / NAS / 守护进程 | **不引入** |
| Storage 接口改造 | **不实施**（§11.1 只是目标形态） |
| 对现有 X Provider / Parser / Storage 职责边界的修改 | **不做** |
| 目录名 / 模块名变更 | **不做** |
| 删除任何现有测试 | **不做** |
| `updated_at`、`audio`、通用 `enclosure` 等推测字段 | **不加入** |

本次**新增**的内容仅限于：架构文档、数据模型设计、字段映射分析、契约验证测试与真实 fixture、Decision Record。

---

## 18. Decision Records

| ID | 决策 | 理由 | 若未来推翻的代价 |
| --- | --- | --- | --- |
| **DR-1** | 统一模型命名为 `NormalizedItem`（不是 `NormalizedTweet`） | 内容已不限推文；`Tweet` 会把来源绑死在名字里 | 低（重命名） |
| **DR-2** | 用 `publisher` 取代 `author`，且可选 | VNovel 的 README 宣称有作者，其真实模型 `models/item.py` **没有** author 字段，11 个解析器也从不抓作者 | 中（需重映射三个来源） |
| **DR-3** | `identity_key` 是派生属性，不存储 | 存储副本会与组成部分漂移；派生不可能漂移 | 低 |
| **DR-4** | `updated_at` 本次不纳入 | 三个真实来源都不提供条目级更新时间，纳入即为推测字段 | 低（新增字段） |
| **DR-5** | `published_precision` **纳入**正式字段 | 真实数据同时存在秒精度（X、NookGaming）与日精度（GameWatch，被上游抹成午夜）；缺此字段则两种可信度无法区分 | 中（历史数据无法回溯补精度） |
| **DR-6** | 沿用 `first write wins`，不做记录富化 | append-only 是 JSONL 的核心价值；富化需要重写行，破坏该性质 | 低（Phase 3 可在接口后加派生索引） |
| **DR-7** | Media 的 `type` 取值域**不含** `audio` | 三个真实来源都不产生音频；封闭枚举中的无生产者成员属于推测设计 | 低（枚举加成员对宽容消费者是加法） |
| **DR-8** | `[from @username](url)` 属于渲染层，不进入模型 | 它是 Telegram 的排版需求；写进模型会让单一输出端污染全部输出端 | 低 |
| **DR-9** | `source_id` 必须是显式配置，不得从展示名运行时推导 | 网站展示名是硬编码字符串（`"Game*Spark"`），上游改名会导致身份漂移、历史数据对不上 | 中（需数据迁移） |
| **DR-10** | 去重按 `identity_key` 在**批次内与历史同时**生效，属共享层职责 | 真实证据：VNovel 的 `rss.xml` 含重复条目、`history.json` 93 条重复、其 `remove_duplicates()` 是死代码 | 低 |
| **DR-11** | `provider` / `route` 归入 `metadata`，不作正式字段 | 它们是「怎么拿到的」；作正式字段会诱导下游按 provider 分支，污染模型 | 低 |
| **DR-12** | Storage 接口去 `account` 主键，`account` 降级为 X 的 metadata | YouTube 频道与网站不是「账号」；保留会使 Storage 依赖 X 概念 | 中（破坏性接口变更，需迁移或双写） |
| **DR-13** | 时区解析归来源适配层，不归 Normalizer；时区未知则 `published_at=None` | Normalizer 是纯函数，不可能知道站点时区；实测上游存在 JST 当 UTC 的 9 小时偏移缺陷 | 低 |
| **DR-14** | 继续 JSONL，不引入数据库 | 免费 GitHub Actions 下唯一持久化是仓库本身；二进制库无法 diff / 合并，损坏即全损 | 高（数据迁移），故需慎重 |

---

## 19. 风险与待验证事项

### 已识别的风险

| ID | 风险 | 影响 | 当前处理 |
| --- | --- | --- | --- |
| **R1** | **YouTube 无真实样本** | 契约对 YouTube 只证明「够用」，未证明「已对接」。真实 YouTube feed 可能带来意外字段 | 列为待验证；实现 YouTube Provider 时用真实数据回归本契约 |
| **R2** | **网站来源时间精度已被上游抹平** | `pubDate` 名义为秒精度，实际可能是「日」被补成午夜。若下游按时间排序或去重，可能出现同一天多条同序 | 契约如实承载 `published_precision`；来源适配层应直连站点以保留原始精度。**不可从 feed 反向恢复** |
| **R3** | **YouTube 接入方式未定** | 决定 `published_at` 精度与「新视频」判断方式 | 待验证：RSS / channel feed / playlist feed 各自能力，明确禁止引入官方 API 作为必需依赖 |
| **R4** | **网站 `source_id` slug 尚无显式配置载体** | 若用展示名推导，上游改名会导致身份漂移（DR-9） | Phase 2 需在来源配置中显式声明 slug，且发布后不可改 |
| **R5** | **Storage 接口变更为破坏性** | `account` 主键移除会影响现有 `data/accounts/` 布局与调用方 | Phase 2 需设计迁移或双写过渡；本次不实施 |
| **R6** | **VNovel 的真实缺陷可能被误当作契约需求** | 例如把「已见 = 已推送」的错误设计照搬过来 | 已在 §16 显式对照说明，作为反面证据保留 |

### 待验证事项（Phase 2 开始前或期间必须解决）

1. 用**真实 YouTube 数据**回归本契约（关闭 R1）。
2. 确定 YouTube 的**无 API 接入方式**（关闭 R3）。
3. 决定网站 slug 的**配置载体与命名规范**（关闭 R4）。
4. 设计 `data/items/` 与现有 `data/accounts/` 的**过渡方案**（关闭 R5）。
5. 确认网站来源能否**直连站点**获取精确发布时间，以缓解 R2。
6. 确认 `metadata` 中 X 的 `stats` 是否有任何 Output 真正需要——若无，考虑是否应留在 metadata（本契约已如此决定）。

---

## G. Phase 2 实施顺序（冻结后，按此顺序，本次不执行）

1. **`NormalizedItem` 落地为正式模型** —— 模型已在 `domain/models/item.py`，需确认无字段增删后转为正式契约。
2. **X Normalizer** —— `RawTweet → NormalizedItem`，按 §13 映射。此步**不改变**任何现有 Provider / Parser。
3. **Storage 支持 `NormalizedItem`** —— 按 §11.1 调整接口（破坏性），并完成数据过渡。
4. **State / Incremental 边界** —— 按 §16.3 落地 `items/` 与 `delivery/` 分离。
5. **YouTube / RSS 输入接口** —— 新增 Raw 类型 + Normalizer（不新增 Output）。
6. **`OutputAdapter` 接口** —— 只定义接口与 `OutputReport`，不实现具体输出。
7. **`RSSOutput`** —— 第一个具体输出，用于验证 Output 边界。
8. **Telegram** —— 含 `TelegramRenderer`（`[from @username](url)` 在此层）。
9. **QQ / OneBot** —— 最后，且必须复用 Telegram 已验证的投递状态机制。

> 顺序理由：先冻结数据契约，再打通读取链路，最后才接输出端。任何把 7/8/9 提前的改动，都会诱导数据模型为单一输出端服务。

---

## H. Freeze 判断

### `Architecture Freeze v3：是否可以冻结？`

```
Architecture Freeze v3: READY
```

**判定依据（三条全部满足）**

1. **三个来源全部映射成功，无需补字段。** §15.2 的映射表没有一格是「为了让契约成立而虚构的字段」。唯一不自然的格子（网站时间精度）已定位为**上游信息丢失**，而非契约设计错误。
2. **契约已在真实数据上执行验证。** `tests/test_normalized_item_contract.py` 的 27 个测试全部通过，其中 X 侧走真实 Phase 1 解析器、网站侧用真实 `rss.xml`（24 条）。契约不是纸面设计。
3. **所有「无法自然映射」的现象都已归类并给出决策**（§15.2 的归属表 + DR-1..DR-14），不存在悬而未决的设计问题。

**READY 的含义边界（避免误读）**

- READY 表示**数据契约可以冻结**，供 Phase 2 依赖；
- READY **不表示** YouTube / 网站适配器已设计完成，也不表示 Storage 改造方案已定；
- R1–R6 是**实施期风险**，不阻塞契约冻结。其中 **R5（Storage 接口破坏性变更）必须在实施第 3 步之前解决**，否则该步无法开始。

**本次未做的事**：未实现任何 Normalizer、未实现任何 Provider、未实现任何 Output、未修改 Storage、未接入 `NormalizedItem` 到任何链路、未开始 Phase 2。
