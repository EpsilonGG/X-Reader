# X-Reader

> 把你在意的 **X（推特）账号**、**网站的 RSS / Atom 订阅** 和 **YouTube 频道** 里的新内容，每天自动抓回来、去掉重复、按统一格式存进你自己的 GitHub 仓库，并且**可以把新内容自动推送到你的 Telegram 和／或 QQ**。

**当前状态：抓取 → 整理 → 存储 → 推送，整条链路可用。** 输入端支持 X / RSS / YouTube，输出端支持 Telegram / QQ。

这份文档是给**普通使用者**看的：照着做就能在自己的 GitHub 上跑起来，不需要读别的文档，也不需要写代码。想了解程序内部怎么组织，见 [附录 C](#c-想深入了解)。

---

## 快速开始

假设你有一个 GitHub 账号，全程在网页上点鼠标就能完成。**这一节先不涉及推送**，等抓取跑通了，再去 [Part II](#part-ii--telegram-接入实操)（Telegram）或 [Part III](#part-iii--qq--onebot-接入实操)（QQ）配置推送。

1. **Fork**：打开本项目的 GitHub 页面 → 右上角 **Fork** → **Create fork**。接下来所有操作都在你自己那份里做。
2. **允许工作流运行**：进入你的仓库 → **Actions** 标签页 → 如果看到 *"Workflows aren't being run on this forked repository"*，点 **I understand my workflows, go ahead and enable them**。左侧列表里应能看到 **Fetch X, RSS and YouTube data**。
3. **打开写权限**：**Settings** → 左侧 **Actions** → **General** → 拉到底部 **Workflow permissions** → 选 **Read and write permissions** → **Save**。（跳过这步，数据提交回仓库时会失败。）
4. **填配置**：在仓库里点开文件、点铅笔图标编辑，改完点 **Commit changes**。
   - 要抓哪些 X 账号 → `accounts.yaml`（见 [第 6 节](#6-输入来源x--rss--youtube)）
   - 要抓哪些网站 RSS / YouTube 频道 → `config/config.yaml`（见 [第 6 节](#6-输入来源x--rss--youtube)）
   - 只想要 X，跳过这一项即可——默认就是「只要 X」。
5. **手动跑一次**：**Actions** → 左侧 **Fetch X, RSS and YouTube data** → 右侧 **Run workflow** → 再点绿色 **Run workflow**。第一次建议在弹窗的 `account` 里只填一个账号名，几十秒就能看到结果。
6. **看结果**：点进那条运行记录，展开 **Fetch, store and deliver** 和 **Summarise the run** 两步，就能看到抓了多少、存了多少（见 [第 13 节](#13-几个固定行为)）。数据会出现在仓库的 `data/` 目录里（见 [第 12 节](#12-data-目录里有什么)）。
7. **然后就等着**：它每天会自己跑一次（见 [第 11 节](#11-自动运行github-actions)）。

---

## 目录

**Part I — X-Reader 项目**

1. [这个项目是做什么的](#1-这个项目是做什么的)
2. [它解决什么问题](#2-它解决什么问题)
3. [能力现状（支持矩阵）](#3-能力现状支持矩阵)
4. [它怎么工作](#4-它怎么工作)
5. [为什么所有来源都统一成 NormalizedItem](#5-为什么所有来源都统一成-normalizeditem)
6. [输入来源：X / RSS / YouTube](#6-输入来源x--rss--youtube)
7. [输出去向：Telegram / QQ](#7-输出去向telegram--qq)
8. [存储、去重与投递状态](#8-存储去重与投递状态)
9. [目录结构](#9-目录结构)
10. [最小可运行配置](#10-最小可运行配置)
11. [自动运行（GitHub Actions）](#11-自动运行github-actions)
12. [`data/` 目录里有什么](#12-data-目录里有什么)
13. [几个固定行为](#13-几个固定行为)
14. [想自己扩展它](#14-想自己扩展它)

**Part II — Telegram 接入实操**

15. [开始之前你需要什么](#15-开始之前你需要什么)
16. [第 1 步：用 BotFather 创建机器人](#16-第-1-步用-botfather-创建机器人)
17. [第 2 步：把机器人加进群组或频道](#17-第-2-步把机器人加进群组或频道)
18. [第 3 步：拿到 Chat ID](#18-第-3-步拿到-chat-id)
19. [第 4 步：把凭据存进 GitHub Secrets](#19-第-4-步把凭据存进-github-secrets)
20. [第 5 步：打开 Telegram 开关](#20-第-5-步打开-telegram-开关)
21. [第 6 步：手动跑一次验证](#21-第-6-步手动跑一次验证)
22. [推送出来的消息长什么样](#22-推送出来的消息长什么样)
23. [Telegram 排查：现象 → 检查 → 如何修复](#23-telegram-排查现象--检查--如何修复)

**Part III — QQ / OneBot 接入实操**

24. [OneBot 是什么，X-Reader 用它的什么](#24-onebot-是什么x-reader-用它的什么)
25. [第 1 步：准备好你自己的 OneBot](#25-第-1-步准备好你自己的-onebot)
26. [第 2 步：确认 HTTP API 可用](#26-第-2-步确认-http-api-可用)
27. [第 3 步：确定 `api_base`](#27-第-3-步确定-api_base)
28. [第 4 步：确定 `group_id`](#28-第-4-步确定-group_id)
29. [第 5 步：决定要不要 access token](#29-第-5-步决定要不要-access-token)
30. [第 6 步：把 token 存进 GitHub Secrets](#30-第-6-步把-token-存进-github-secrets)
31. [第 7 步：打开 QQ 开关](#31-第-7-步打开-qq-开关)
32. [第 8 步：手动跑一次验证](#32-第-8-步手动跑一次验证)
33. [推送出来的消息长什么样](#33-推送出来的消息长什么样)
34. [投递状态与重复发送](#34-投递状态与重复发送)
35. [QQ 排查：现象 → 原因 → 检查 → 修复](#35-qq-排查现象--原因--检查--修复)

**附录**

- [A. 在电脑上本地运行（可选）](#a-在电脑上本地运行可选)
- [B. 常见问题](#b-常见问题)
- [C. 想深入了解](#c-想深入了解)

---

## Part I — X-Reader 项目

### 1. 这个项目是做什么的

你给它三份清单：

- **要关注的 X（推特）账号**（写在 `accounts.yaml` 里）；
- **要关注的网站的 RSS / Atom 地址**（写在 `config/config.yaml` 的 `rss_sources` 里）；
- **要关注的 YouTube 频道**（写在 `config/config.yaml` 的 `youtube_channels` 里）。

它每天自动运行一次，把这三类来源的新内容取回来，去掉已经存过的重复内容，按**同一套格式**存进仓库的 `data/` 目录并自动提交。如果你打开了推送，它还会把**这次新增的内容**发到你的 **Telegram** 和／或 **QQ**（两个可以都开、都关，也可以只开一个）。

一句话：**它负责把内容收好，并且（可选地）帮你发出去。**

它**不**生成 RSS 文件，**不**做 AI 总结。这些在代码里没有实现，所以也配置不了——请不要按它们去改配置。

**不需要任何官方 API key，不需要付费服务，不需要服务器。** 抓 X 走的是公开的 Nitter 镜像站（和 X 官网不同的第三方页面）；抓 RSS 就是普通的网页请求；抓 YouTube 读的是频道公开的订阅源（Atom 格式），**不需要 YouTube Data API，也不需要 Google 账号**；推送 Telegram 用的是 Telegram 官方的免费机器人接口；推送 QQ 走的是你自己已经跑起来的 OneBot HTTP 接口（见 [Part III](#part-iii--qq--onebot-接入实操)）。

---

### 2. 它解决什么问题

社交账号和网站的内容散落在各处，想「每天看一眼有没有新的」通常意味着：

- **逐个打开**：十几个账号、几个站点，每天点一遍，很累；
- **平台自己的通知不可靠**：推送会漏、会被算法排序、无法按来源分开看；
- **想看的东西想留档**：平台上的内容会被删、会被改，过一阵就找不回来了；
- **想接到自己顺手的工具里**：有人用 RSS 阅读器，有人用 Telegram 群，有人用 QQ 群。

X-Reader 的做法是：**每天固定抓一次 → 去重 → 按统一格式落进你自己的仓库 → （可选）推到你想看的地方**。

三个结果：

1. **不遗漏**：每天一次，抓到的都存下来；存过的不会重复存。
2. **不丢失**：内容先落盘再推送。推送失败只是「还没发出去」，下次运行自动补发，**不需要重新抓取**。
3. **可留档**：所有内容都在你自己的 GitHub 仓库里，是纯文本（JSONL），看得见、改得动、能回滚。

---

### 3. 能力现状（支持矩阵）

这张表是照着**代码实际实现**填的，不是照着计划填的。

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| 抓取 X / Twitter 账号 | ✅ **已实现** | 走公开 Nitter 镜像站，多实例自动切换 |
| 读取网站 RSS / Atom 订阅 | ✅ **已实现** | 通用解析，不需要为每个网站写规则 |
| 抓取 YouTube 频道 | ✅ **已实现** | 读频道公开的 Atom 订阅源，**不需要 API key** |
| 去重、增量存储、运行记录 | ✅ **已实现** | 重复运行不会重复存 |
| 输入来源任意组合（X / RSS / YouTube） | ✅ **已实现** | 三者互相独立 |
| 推送到 Telegram | ✅ **已实现** | 见 [Part II](#part-ii--telegram-接入实操) |
| 推送到 QQ（OneBot） | ✅ **已实现** | 需要你自己跑一个 OneBot 实现，见 [Part III](#part-iii--qq--onebot-接入实操) |
| 投递状态记录（防止重复发送） | ✅ **已实现** | 每个输出各记各的，见 [第 8 节](#8-存储去重与投递状态) |
| 推送失败自动重试 | ✅ **已实现** | 失败内容保持「待发送」，下次自动补发，**不需要重新抓取** |
| 生成 RSS 输出文件 | ⛔ **未实现** | |
| AI 摘要 / 翻译 | ⛔ **未实现** | 代码里完全没有接入任何 AI 服务 |
| Markdown 富文本排版 | ⛔ **未实现** | Telegram 用 HTML 基础排版，QQ 用纯文本 |

> ⛔ 的项目在代码里**不存在**。请不要按它们去配置，配置了也不会有效果——甚至可能让程序启动时报错。

还有两件事不是「没做」，而是**根本不需要做**：抓 X **不需要**官方 X API key，抓 YouTube **不需要** YouTube Data API key，也不需要 Google 账号或任何配额。

---

### 4. 它怎么工作

```
   你填的三份清单
   ├── accounts.yaml              要关注的 X 账号
   └── config/config.yaml
       ├── rss_sources            要关注的网站 RSS / Atom
       ├── youtube_channels       要关注的 YouTube 频道
       └── telegram / qq          要不要推送、推到哪里
              │
              ▼
   X-Reader 每天自动运行一次（GitHub Actions）
              │
      ┌───────┼────────┐
      ▼       ▼        ▼
   抓取 X   抓取网站   抓取 YouTube
   账号      RSS       频道
      └───────┼────────┘
              ▼
      整理成统一格式（同一套字段）
              ▼
      去掉重复（只留新内容）
              ▼
      写入仓库 data/ 目录
              ▼
      查询「哪些还没发过」（data/delivery/）
              ▼
      ┌───────┴────────┐
      ▼                ▼
   发送到 Telegram    发送到 QQ
   （如果打开了）     （如果打开了）
      └───────┬────────┘
              ▼
      记录「这些已经发过了」（每个输出各记各的）
              ▼
      Git 自动提交（你随时能看到存了什么、发过了什么）
```

它每次跑完都会在仓库里留下记录，所以**你不需要盯着它**——想确认时去看一眼运行结果就行（见 [第 13 节](#13-几个固定行为)）。

> 顺序很重要：**先把内容存好，再发送。** 所以即使推送那边出问题，内容也不会丢——它只是「还没发出去」，下次运行会补上。
>
> 两个输出是**互相独立**的：可以只开 Telegram、只开 QQ，也可以两个都开。一个挂了不影响另一个（见 [第 8 节](#8-存储去重与投递状态)）。

---

### 5. 为什么所有来源都统一成 NormalizedItem

X 推文、RSS 条目、YouTube 视频，三者的原始形态完全不同。如果每个来源各存一套格式，下游（推送、将来的检索）就得为每个来源写一遍逻辑。

所以 X-Reader 在中间加了一步：不管来自哪里，最终都变成**同一个结构**，叫 `NormalizedItem`。它一共 **12 个字段**：

| 字段 | 含义 |
| --- | --- |
| `source_id` | **来源身份**。X 是 `x`，RSS 是你填的 `id`，YouTube 是 `youtube:<频道ID>`。 |
| `item_id` | **条目身份**，在来源内唯一。X 是推文 ID，RSS 是链接或 guid，YouTube 是视频 ID。 |
| `title` | 标题（可能为空） |
| `content` | 正文 |
| `publisher` | 发布者：X 账号名 / 网站名 / YouTube 频道名。**只用于展示，不参与身份。** |
| `canonical_url` | 原始链接 |
| `published_at` | 发布时间（标准化后的） |
| `published_at_raw` | 来源给出的**原始时间字符串**（原样保留） |
| `published_precision` | 时间精度：`second` / `day` / `unknown` |
| `fetched_at` | **我们什么时候抓到的** |
| `media` | 附件（比如缩略图） |
| `metadata` | 该来源特有的补充信息 |

必填的只有 `source_id`、`item_id`、`fetched_at`，再加上一条「至少要有标题或正文」。

**身份是 `source_id` + `item_id` 合起来算的**，形如 `x:12345`、`my-favorite-site:https-...`、`youtube:UCxxxx:dQw4w9WgXcQ`。这意味着：

- 同一条内容重复抓到 → 身份相同 → 跳过；
- 不同来源里碰巧 `item_id` 相同（比如两边都是 `12345`）→ **身份不同，两条都保留**。

设计上有两条刻意的规矩：

1. **时间不猜。** 来源只说「3 小时前」或者只给日期，就如实记录为精度不足，绝不伪造一个精确时间点。
2. **推送格式不进模型。** `NormalizedItem` 里没有 `telegram_text`、没有 `qq_text`、没有 `markdown`——「怎么显示」是推送那一步的事，不能反过来污染所有来源共用的数据结构。

> 想了解完整字段定义与边界，见 [`docs/ARCHITECTURE_FREEZE_V3.md`](docs/ARCHITECTURE_FREEZE_V3.md)。

---

### 6. 输入来源：X / RSS / YouTube

X、RSS、YouTube 是**三个互相独立的输入来源**，可以任意组合。**唯一的硬性要求是：至少要有一个可用的来源。**

它们在同一次运行里一起处理，各存各的文件，互不干扰——一个来源挂了，不影响另外两个。

#### 6.1 要抓哪些 X 账号

打开仓库根目录的 `accounts.yaml`。**这是声明 X 账号的唯一地方**，程序里没有任何硬编码的账号。

最简单的写法——一行一个账号：

```yaml
accounts:
  - mimoriaino
  - mimo_chorion
  - re_re_tsubame
```

也可以写成带 `username` 的形式（两种写法可以混用）：

```yaml
accounts:
  - username: mimoriaino
  - username: mimo_chorion
  - username: 322yui3
    enabled: false        # 暂时不抓这个账号，但保留这行
```

| 字段 | 必填 | 含义 |
| --- | --- | --- |
| `username` | 是 | X 账号的用户名。**不带 `@`**，就是网址 `x.com/` 后面那串。 |
| `enabled` | 否 | 写 `false` 就跳过这个账号（默认 `true`）。想临时停抓某个账号时用它，比删掉整行更安全。 |

- **不要改动 `accounts:` 这个键名**，也不要改变缩进层级。
- 用户名大小写照抄即可，程序判断重复时不区分大小写。
- 想完全不抓 X，写成 `accounts: []` 是**允许**的。
- **不需要任何 X API key、token 或密码。**

> ⚠️ 抓 X 依赖第三方公开镜像站（Nitter），这类站点时好时坏。某个账号偶尔抓不到是正常现象，下次运行会再试；单个账号失败**不会**影响其他账号，也**不会**让整次运行算失败。

#### 6.2 要抓哪些网站 RSS

RSS 来源写在 `config/config.yaml` 的 `rss_sources` 里。默认是空列表：

```yaml
rss_sources: []
```

要加来源，改成这样（**注意缩进，`rss_sources` 顶格写**）：

```yaml
rss_sources:
  - id: my-favorite-site
    url: "https://example.com/feed.xml"
    enabled: true
```

| 字段 | 必填 | 含义 |
| --- | --- | --- |
| `id` | 是 | 这个来源的**名字标签**。只能用小写字母、数字和 `. _ -`；**不能用 `x` 或 `youtube`**（这两个名字被系统占用了）。 |
| `url` | 是 | RSS / Atom 地址，必须以 `http://` 或 `https://` 开头。 |
| `enabled` | 否 | 写 `false` 就暂时不抓这个来源（默认 `true`）。 |

> **`id` 一旦用过就别再改。** 它是这个来源里**每一条内容**的归属标记。改了 `id`，程序会认为这是一个全新的来源，之前存过的内容会被重新算作「没见过的」，从而重复存储。**一次选定，之后不要动。**

其他要点：

- 可以加任意多条，每条缩进对齐即可。
- RSS 2.0 和 Atom 两种格式都支持，程序**通用解析**，不需要为每个网站写专门的规则。
- 只给日期的内容（比如「2026-09-28」）会被如实记录为「只精确到天」，不会被伪装成「当天零点发布」。

**举个例子**：本工作区里的 `VisualNovel-Interview-RSS-main` 项目会把日本游戏媒体的访谈汇总成一个标准 RSS 文件，每天更新。对 X-Reader 来说它**就是一个普通的 RSS 来源**——拿到那个 `rss.xml` 的可访问网址填进去即可：

```yaml
rss_sources:
  - id: visualnovel-interview
    url: "https://raw.githubusercontent.com/<对方用户名>/<对方仓库名>/main/rss.xml"
    enabled: true
```

X-Reader **不会**去改那个项目，**不依赖**它的代码，也没有为它写专门的解析器。它只是一个 URL。那个 RSS 里的一些「不完美」（同一篇文章出现两次、有的条目没日期）由 X-Reader 这边吸收：重复的合并，没日期的照样收下。

#### 6.3 要关注哪些 YouTube 频道

YouTube 频道写在 `config/config.yaml` 的 `youtube_channels` 里。默认是空列表：

```yaml
youtube_channels: []
```

要加频道，改成这样（**注意缩进，`youtube_channels` 顶格写**）：

```yaml
youtube_channels:
  - channel_id: UCabcdefghijklmnopqrstuv
    enabled: true
```

| 字段 | 必填 | 含义 |
| --- | --- | --- |
| `channel_id` | 是 | 频道的 **ID**，`UC` 开头的一串字符。**不是频道名，也不是频道网址。** |
| `url` | 否 | 覆盖订阅源地址。**正常情况不要填。** |
| `enabled` | 否 | 写 `false` 就暂时不抓这个频道（默认 `true`）。 |

**它读的是什么。** YouTube 频道公开的订阅源（`https://www.youtube.com/feeds/videos.xml?channel_id=<频道ID>`）——就是 YouTube 自己提供给 RSS 阅读器的那份文件。**不需要 YouTube Data API，不需要 API key，不需要 Google 账号，也不消耗任何配额。**

**怎么找到 `channel_id`。** 它不是频道名，也不是 `@handle`。常见找法：

- 打开频道主页 → 右键「查看网页源代码」→ 搜索 `channelId`，后面 `"UC..."` 那一串就是；
- 或者打开该频道任意一个视频页面 → 查看源代码 → 搜 `channelId`；
- 也可以用第三方工具把 `@handle` 转成 `UC...`。

> ⚠️ **不要填频道名（比如「某某的频道」）或 `@handle`。** 程序会在启动时报错拒绝——因为这两样东西都**不稳定**：频道随时可以改名，用名字当身份会让已存的内容全部被当成新内容重存一遍。

**为什么身份是 `channel_id` 而不是频道名。** 每条 YouTube 内容的身份是 **`youtube:<channel_id>`**。所以频道**改名**或换 `@handle` → 身份不变，已存内容不会重复；只有你**换了 `channel_id`**（也就是换了另一个频道）→ 才算新来源。**`channel_id` 一旦填上就别再改**，这和 RSS 来源的 `id` 是同一个道理。

**抓回来的内容长什么样。**

| 你在 YouTube 上看到的 | 存成什么 |
| --- | --- |
| 视频标题 | `title` |
| 视频简介 | `content` |
| 频道名 | `publisher`（**只是展示用**，不参与身份） |
| 发布时间 | `published_at`（源里给的就是带时区的标准时间，原样保留） |
| 视频链接 | `canonical_url`，形如 `https://www.youtube.com/watch?v=<视频ID>` |
| 封面图 | `media`（缩略图） |

**视频 ID 就是身份**（`item_id`，11 个字符），**不是**标题，也**不是**链接。标题改了、链接参数变了，都还是同一条内容。简介为空的视频照样收下（只要有标题）；没有封面图的也照样收下。程序**不会**去抓 YouTube 视频页面补简介——抓页面慢、容易触发风控，而订阅源里的信息已经够用。

**两个容易踩的坑：**

1. **`url` 不要填。** 它只是留给自建镜像或测试用的逃生口。填了它等于你手工指定订阅源地址，而这个地址和你填的 `channel_id` 可能对不上——那会造成「抓回来的视频其实属于另一个频道」，非常难查。留空，让程序自己拼。
2. **订阅源只给最近的视频**（大约 15 条）。这是 YouTube 那边的限制，不是程序截断。所以如果你**第一次**就加一个更新很勤的频道，只有最近这些会被收进来，更早的历史内容拿不到——这是正常的。加进去之后每天跑，就不会漏了。

> 现实提醒：`youtube.com` 在某些网络环境下可能访问不通。如果日志里出现 `network_error`，先确认这个地址能不能打开。**单个频道抓不到不会影响其他频道，也不会让整次运行算失败。**

#### 6.4 常见组合

| 组合 | 怎么配 | 适合 |
| --- | --- | --- |
| **只要 X**（默认） | `accounts.yaml` 里写账号，`rss_sources: []`、`youtube_channels: []` | 开箱即用 |
| **只要 RSS** | `provider.order: []` + `accounts: []` + `rss_sources` 填来源 | 只看网站 / 博客 |
| **只要 YouTube** | `provider.order: []` + `accounts: []` + `youtube_channels` 填频道 | 只看几个频道 |
| **全都要** | 三个都填 | 什么都看 |

三种「只要一种」的写法是同一件事：**把另外两种留空**。RSS 和 YouTube 本来就是空的；要让 X 留空，得把 `provider.order` 写成空列表。具体例子见 [第 10 节](#10-最小可运行配置)。

---

### 7. 输出去向：Telegram / QQ

推送是**输出**，和输入组合**互不影响**。你可以只抓一个来源、推到两个地方；也可以抓三个来源、一个都不推（那就只是存起来）。

| 输出 | 需要什么 | 需要常驻程序吗 | 配置在哪 |
| --- | --- | --- | --- |
| **Telegram** | 一个机器人令牌 + 一个 Chat ID | **不需要** | [Part II](#part-ii--telegram-接入实操) |
| **QQ（OneBot）** | 一个你自己跑着的 OneBot 实现 | **需要**（但那是你的程序，不是 X-Reader 的一部分） | [Part III](#part-iii--qq--onebot-接入实操) |

两个输出**完全独立**：

- 可以只开 Telegram、只开 QQ、两个都开（同一条内容两边都发），或者两个都关；
- 一个挂了不影响另一个——它们的投递状态是**两份不同的文件**（见 [第 8 节](#8-存储去重与投递状态)）；
- 每个输出只支持**一个**目标。想换目标就改配置里的那个值（已发过的不重发到新目标）。

> **X-Reader 自己不需要常驻进程。** 它每天跑几分钟就退出。唯一需要常驻的是 QQ 那侧的 OneBot 实现——那是你自己的程序。不推 QQ 就完全不需要它。

---

### 8. 存储、去重与投递状态

#### 两个不同的问题

仓库里有两组文件，回答的是**两个不同的问题**：

| | 回答的问题 | 存在哪 | 行为 |
| --- | --- | --- | --- |
| `data/items/` | 「这条内容我**见过**吗？」 | 每个来源一个文件 | 见过就不再重复抓取、不再重复存储 |
| `data/delivery/` | 「这条内容我**发出去**了吗？」 | **每个输出一个文件** | 发过就不再重复发送 |

**为什么必须分开？** 因为这是两个独立的答案：

- **推送失败了** → 内容仍在 `items/` 里（所以**见过**，不会重复抓取），但不在 `delivery/` 的「已发送」里（所以**下次会补发**）。
- 如果只用一个标记，推送失败就会被当成「处理完了」，那条内容**永远发不出去**，而且没人知道。这正是这个项目要避免的失败方式。

#### 投递状态按输出分开

```
data/delivery/telegram.jsonl
data/delivery/qq.jsonl
```

每条内容在每个输出里各有一份状态，取值是 `sent`（已发送）、`failed`（发送失败，保持待发送）、`skipped`（适配器拒绝处理，不算已发也不算失败）。

所以你会看到这样的行为：

- 第一次打开推送 → 仓库里**所有**已存内容都会被发一遍（它们都还没发过）；
- 之后每次运行 → **只发这次新增的**；
- 某次 Telegram 挂了 → 那批内容保持「待发送」，**下次运行自动补发**，**不需要重新抓取**；
- 想换一个群 → 改 `TELEGRAM_CHAT_ID`。已经发过的内容**不会**重发到新群。如果你希望全部重发一遍，把 `data/delivery/telegram.jsonl` 删掉即可（会重新发全部内容，谨慎操作）。

#### 两个输出为什么互不影响

因为**每个输出各记各的账**。同一条内容完全可以处在「Telegram 已发、QQ 没发」的状态：

```
data/delivery/telegram.jsonl   →  这条是 sent
data/delivery/qq.jsonl         →  这条是 failed
```

下一次运行的行为就很明确了：

- Telegram → 看到 `sent`，**跳过**，不重发；
- QQ → 看到 `failed`（不是 `sent`），**重发**。

**一个输出挂了，绝不会让另一个重发**，也绝不会让某条内容被误标成「两边都发过了」。这就是投递状态必须按输出分开存、而不是共用一个「已处理」标记的原因。

> `data/delivery/` 会被自动提交进仓库。这是必要的：它是「什么已经发过了」的唯一记录，丢了会把整个积压重发一遍。

---

### 9. 目录结构

下面是仓库里实际存在的结构。**你只需要关心加粗的那几项**，其余是程序内部。

```
X-Reader/
├── main.py                     命令行入口（--no-rss / --no-youtube / --json / --strict …）
├── accounts.yaml               ★ 要抓的 X 账号（唯一来源）
├── requirements.txt            运行依赖：httpx / selectolax / PyYAML / pydantic
├── pyproject.toml              项目元数据与测试配置
│
├── config/
│   ├── config.yaml             ★ RSS 来源、YouTube 频道、Telegram / QQ 开关
│   ├── loader.py               读取并校验配置；不合法就拒绝启动
│   └── schema.py               配置的结构定义（多写一个字段就是启动报错）
│
├── app/
│   ├── registry.py             (provider, route) → parser + normalizer 的绑定表
│   └── runner.py               整条流水线：抓取 → 归一化 → 存储 → 投递
│
├── domain/
│   ├── errors.py               错误分类（每种错误有稳定的 kind）
│   └── models/
│       ├── item.py             ★ NormalizedItem：跨来源统一格式
│       ├── account.py          一个「被追踪单元」（X 账号 / RSS 来源 / YouTube 频道）
│       ├── media.py            附件
│       ├── tweet.py            X 解析后的原始记录
│       ├── rss_item.py         RSS 解析后的原始记录
│       └── youtube_item.py     YouTube 解析后的原始记录
│
├── infrastructure/http/
│   ├── client.py               超时 / 重试 / 错误映射
│   └── response.py             RawResponse（原始响应 + 抓取时间）
│
├── providers/                  「怎么到达」一个来源
│   ├── nitter.py               X：公开 Nitter 镜像，多实例故障转移
│   ├── xtf_adapter.py          X：可选的 x-tweet-fetcher 后备
│   ├── rss.py                  网站 RSS / Atom
│   ├── youtube.py              YouTube：频道公开 Atom 源
│   └── factory.py              按配置装配 provider
│
├── parsers/                    「怎么读懂字节」
│   ├── nitter_rss.py            X：Nitter 的 RSS 形态
│   ├── nitter_html.py           X：Nitter 的 HTML 形态
│   ├── rss.py                   RSS / Atom 通用解析（含时间解析）
│   ├── youtube.py               YouTube Atom 解析
│   ├── media_url.py  timeutil.py
│   └── base.py
│
├── normalizers/                「怎么变成统一格式」（纯函数，不做任何 I/O）
│   ├── x.py  rss.py  youtube.py
│   ├── registry.py             按 kind 取 normalizer
│   └── base.py
│
├── storage/
│   ├── base.py                 三个接口：BaseStorage / RawRecordArchive / ItemReader
│   └── jsonl.py                JSONL 落盘实现
│
├── outputs/                    「怎么发出去」
│   ├── base.py                 OutputAdapter 接口 + DeliveryResult + 凭据打码
│   ├── telegram.py             Telegram Bot API
│   ├── qq.py                   QQ / OneBot HTTP API
│   └── factory.py              按配置装配启用的输出
│
├── data/                       ★ 运行数据（自动提交回仓库，见第 12 节）
│   ├── items/<来源>.jsonl
│   ├── accounts/<单元>.jsonl
│   ├── runs/<日期>.jsonl
│   ├── raw/<单元>/
│   └── delivery/<输出>.jsonl
│
├── scripts/                    验证与冒烟脚本（见第 14 节）
│   ├── verify_architecture.py  verify_storage.py
│   ├── verify_readme.py        verify_reference_projects.py
│   ├── smoke_phase3.py         smoke_youtube.py
│   └── validate_against_real_page.py
│
├── tests/                      688 个自动化测试
└── docs/                       ARCHITECTURE.md / ARCHITECTURE_FREEZE_V3.md / NEXT_PHASE_PLAN.md
```

---

### 10. 最小可运行配置

`config/config.yaml` 的默认内容就是一份**可以直接跑**的最小配置——只抓 X，不推送：

```yaml
provider:
  order:
    - nitter
    - xtf
  nitter:
    endpoints:
      - https://nitter.poast.org
      - https://xcancel.com
    routes:
      - rss
      - html
  xtf:
    enabled: true
    instances: []
    route: search

http:
  timeout: 20
  retries: 1

fetch:
  limit: 20

storage:
  data_dir: data
  store_raw_response: on_error

telegram:
  enabled: false

qq:
  enabled: false

rss_sources: []

youtube_channels: []
```

配合 `accounts.yaml`：

```yaml
accounts:
  - mimoriaino
```

这就够了。`provider.order` 是 X 的抓取顺序（`nitter` 是 X-Reader 自己的实现，`xtf` 是可选后备，装了才生效）。

#### 只要 RSS，完全不抓 X

`config/config.yaml`：

```yaml
provider:
  order: []          # 空列表 = 不启用 X 输入
  nitter:
    endpoints:       # 这段可以留着不管，也可以整段删掉
      - https://nitter.poast.org
  xtf:
    enabled: true
http:
  timeout: 20
  retries: 1
fetch:
  limit: 20
storage:
  data_dir: data
  store_raw_response: on_error

rss_sources:
  - id: my-favorite-site
    url: "https://example.com/feed.xml"
    enabled: true

youtube_channels: []
```

`accounts.yaml` 写成 `accounts: []`。这样运行时**根本不会去碰 X**，也不会因为没填 Nitter 地址而报错。

#### 只要 YouTube，完全不抓 X 和 RSS

```yaml
provider:
  order: []
  nitter:
    endpoints:
      - https://nitter.poast.org
  xtf:
    enabled: true
http:
  timeout: 20
  retries: 1
fetch:
  limit: 20
storage:
  data_dir: data
  store_raw_response: on_error

rss_sources: []

youtube_channels:
  - channel_id: UCabcdefghijklmnopqrstuv
    enabled: true
```

`accounts.yaml` 同样写成 `accounts: []`。

> 反过来，如果 `provider.order` 是空的、`rss_sources` 和 `youtube_channels` 也都是空的（或者全部写了 `enabled: false`），程序会直接拒绝启动并报错 `no input source configured`——因为这样就真的没有任何东西可抓了。

---

### 11. 自动运行（GitHub Actions）

#### 每天自动运行

工作流里写好的定时规则是：

```yaml
schedule:
  - cron: "10 17 * * *"
```

这是 **UTC 时间（世界标准时间）**，不是北京时间：

| | 时间 |
| --- | --- |
| 定时触发（UTC） | 每天 **17:10** |
| 换算成北京时间（UTC+8） | 每天**次日 01:10** |
| 换算成日本时间（UTC+9） | 每天**次日 02:10** |

所以你会看到它在**凌晨一点多**更新。想改成别的时间，把 `10 17` 换成你要的「分 时」（仍是 UTC）即可。

> GitHub 的定时任务在高峰期可能延迟几分钟到几十分钟，这是正常的，不是故障。

#### 手动运行（随时想跑就跑）

**Actions** → 左侧 **Fetch X, RSS and YouTube data** → 右侧 **Run workflow**。弹窗里有四个可填项：

| 输入项 | 含义 | 注意 |
| --- | --- | --- |
| `account` | 只抓**这一个** X 账号，留空 = 抓全部启用的账号 | 填的是 `accounts.yaml` 里的用户名，不带 `@` |
| `limit` | 这次运行每个 X 账号**最多**保留多少条 | **只影响 X**，不影响 RSS 和 YouTube（它们都是整份订阅一次取回，不做截断） |
| `no_rss` | 勾上 = 这次跳过所有 RSS 来源 | 布尔开关 |
| `no_youtube` | 勾上 = 这次跳过所有 YouTube 频道 | 布尔开关 |

> 为什么 `limit` 只影响 X：这个上限本来是为了防止 X 镜像站「一次请求只给一页」时抓太多而设的安全帽。RSS 和 YouTube 都是**一整份文件**一次性拿回来的，截断只会白白丢掉真实内容，所以对它们不生效。

> 手动运行**同样会触发推送**（如果你已经打开了）。所以第一次配置推送时，建议先按 [第 21 节](#21-第-6-步手动跑一次验证)（Telegram）或 [第 32 节](#32-第-8-步手动跑一次验证)（QQ）用一次专门的运行来验证。

#### 工作流里还有什么

- **运行前先跑测试**：任何解析器被改坏，都不会把坏数据写进仓库（仓库是提交的，坏数据很难撤回）。
- **只把密钥当环境变量传进去**，绝不拼进命令行——否则密钥会留在日志里。
- **两个触发方式**：定时 + 手动。
- **不并发**：两次运行同时写同一批文件会互相打架，所以同一时间只允许一个在跑。
- **失败不会让整个任务变红**：单个账号或来源失败只记一条警告（见 [第 13 节](#13-几个固定行为)）。

---

### 12. `data/` 目录里有什么

所有数据都在仓库的 `data/` 目录里，运行结束后由工作流自动提交，你在 GitHub 网页上就能直接看：

```
data/
├── items/<来源>.jsonl      ★ 主要数据：抓到的内容（统一格式，一行一条）
├── accounts/<单元>.jsonl   原始记录存档（X 推文的原始形态，仅作证据）
├── runs/<日期>.jsonl       运行日志：每次尝试一行，谁成功谁失败
├── raw/<单元>/             原始网页/响应存档（默认只在出错时保留）
└── delivery/<输出>.jsonl   投递状态：每个输出各一份（见第 8 节）
```

说明：

- **`items/` 是你要的东西。** 每个来源一个文件：
  - `items/x.jsonl` —— 所有 X 账号的内容；
  - `items/my-favorite-site.jsonl` —— 那个 RSS 来源的内容；
  - `items/youtube_UCabcdefghijklmnopqrstuv.jsonl` —— 那个 YouTube 频道的内容（`source_id` 里的冒号会被换成下划线，因为文件名不能带冒号）。
- **`accounts/` 不是数据流的一部分**，它只是「抓到的原始推文长什么样」的存档，方便日后追溯。**目前只有 X 有这份存档**，RSS 和 YouTube 没有。
- 文件是 **JSONL 格式**（每行一个完整的 JSON），不是数据库文件。好处是 git 能看清每次**新增了哪几行**，体积也不会失控。
- 每一行里都有 `source_id` 和 `item_id` 用来标识身份，还有 `fetched_at`（我们什么时候抓到的）。**同一条内容重复抓到不会重复存**。
- **`delivery/` 只在你打开了推送之后才会出现**，而且**每个输出各一份**：`delivery/telegram.jsonl`、`delivery/qq.jsonl`（见 [第 8 节](#8-存储去重与投递状态)）。

> `data/README.md` 里也有一份同样的说明，是给直接在仓库里翻文件的人看的。

---

### 13. 几个固定行为

这几条是**设计上刻意的**，知道它们能省下很多困惑。

**1. 先存后发，推送失败不丢内容。** 内容先落进 `data/items/`，再尝试推送。推送失败只是「还没发出去」，下次运行**从仓库里读回来重发**，**不需要重新抓取**。这是**至少一次**投递：宁可偶尔重发一条，也不让内容永久丢失。

**2. 单点失败不拖垮整体。** 一个 X 账号、一个 RSS 来源、一个 YouTube 频道失败，只会被记录，其余照常。只有**所有**单元都失败，整次运行才算失败。

**3. 推送失败不算抓取失败。** 内容已经存好了，所以运行报告里推送是单独一行，失败也不会让整个任务变红。

**4. 时间不猜。** 来源只给日期，就记成「只精确到天」；只给相对时间，就留空并把原始字符串保留下来。绝不会伪造一个精确时间点。

**5. `limit` 只作用在 X 上。** RSS 和 YouTube 是一整份文件取回的，截断只会丢内容。

**6. 退出码。**

| 码 | 含义 |
| --- | --- |
| `0` | 运行完成并产出了可用结果 |
| `1` | 所有单元都失败，或没有可用的来源 |
| `2` | 无法启动（配置有问题） |

**推送失败不会让退出码变成 1**。如果你希望「只要有东西没发出去就报错」，在本地运行时加上 `--strict`。

#### 看运行状态

**Actions** 页面里每条运行记录左边的图标：

| 图标 | 含义 | 你该做什么 |
| --- | --- | --- |
| ✅ 绿色对勾 | 成功 | 不用管 |
| ⚠️ 黄色感叹号 | 有单元失败或推送没发完，但整体算完成 | 看一眼是哪个账号/来源/推送出了问题，通常下次会自己恢复 |
| ❌ 红色叉号 | 所有单元都失败了，或程序无法启动 | 需要处理，见 [第 23 节](#23-telegram-排查现象--检查--如何修复) / [第 35 节](#35-qq-排查现象--原因--检查--修复) |

#### 看运行报告

点进那条运行记录，看这两个步骤：

- **Summarise the run** —— 会打印一份完整的 JSON 报告（同时也会写进 `report.json` 并显示在运行的 Summary 页面）。
- **Fetch, store and deliver** —— 会打印 `python main.py` 的输出。

报告长这样（**没有配置推送时，`output:` 那几行不会出现**）：

```
run 20260928T171000Z-abcd1234  (1234 ms)

  unit        kind   status     provider/route      new  dup fetched
  ------------------------------------------------------------------
  mimoriaino  x      ok         nitter/rss            4    0       4
  my-site     rss    ok         rss/feed             23    1      24
  UCabcdefg   youtube ok         youtube/feed          3    0       3

  units:  3 ok, 0 empty, 0 failed, 0 skipped
  items:  30 new, 1 duplicate (31 normalized from 31 fetched)
  output: telegram — ok: 30 delivered, 0 failed, 0 skipped (30 pending)
  output: qq — ok: 30 delivered, 0 failed, 0 skipped (30 pending)
```

- `unit` / `kind`：处理的是哪个来源，以及它是 `x`、`rss` 还是 `youtube`。
- `status`：`ok` 成功、`no_tweets` 成功但这次没新内容、`error` 失败、`skipped` 被配置跳过。
- `new` / `dup` / `fetched`：新增多少条 / 重复多少条 / 总共取回多少条。
- `output:` **每个输出一行**（Telegram、QQ 各一行）。`ok` 表示全部发完；`FAILED` 表示有内容没发出去（后面会写明原因）。
- 最后两行的汇总：几个单元成功、总共新增多少条。

**第二次运行如果显示 `0 new`，那是正常的**——说明内容都已经存过了，去重生效了。

---

### 14. 想自己扩展它

这一节给愿意读代码的人。**普通使用者可以跳过。**

程序按**固定的单向数据流**组织，每一层只认识相邻的一层：

```
单元（X 账号 / RSS 来源 / YouTube 频道）
  → Provider    只负责「怎么到达」：HTTP、超时、重试、故障转移
  → RawResponse 原始响应
  → Parser      只负责「怎么读懂字节」，不做网络、不做存储
  → 原始记录     各来源自己的形态（RawTweet / RawRSSItem / RawYouTubeItem）
  → Normalizer  纯函数，「怎么变成统一格式」，没有任何 I/O
  → NormalizedItem
  → Storage     唯一的落盘出口
  → OutputAdapter 只负责「怎么发出去」，不写自己的状态
```

**加一个新输入来源**，固定要改三处：新增 Provider 模块、在 `providers/factory.py` 里装配、在 `app/registry.py` 的绑定表里登记 `(provider, route) → (parser, normalizer)`。

**加一个新输出去向**，只需要实现一个 `OutputAdapter` 子类并在 `outputs/factory.py` 里装配——不用碰 `NormalizedItem`、不用碰 Storage、不用碰 Runner。这正是「投递状态按输出分开」换来的性质。

**仓库自带四个验证门禁**（都在 `scripts/` 里，各自返回非 0 表示失败）：

```bash
python scripts/verify_architecture.py          # 分层边界没有被破坏
python scripts/verify_storage.py               # data/ 里的数据没有结构问题
python scripts/verify_readme.py                # 这份文档和代码没有脱节
python scripts/verify_reference_projects.py    # 参考项目没被改动
```

**还有两个冒烟脚本**，用真实的 HTTP 请求跑通整条链路（不会假装成功）：

```bash
python scripts/smoke_youtube.py            # YouTube：本地假频道 + 真实 socket
python scripts/smoke_youtube.py --live     # YouTube：真实访问 youtube.com（需要能连上）
python scripts/smoke_phase3.py             # Telegram：本地假服务器 + 真实收发
```

QQ 那侧的真实收发在测试里用一个本地假 OneBot 服务器验证（`python -m pytest -q tests/test_qq_output.py`）。

想了解为什么这样分层、为什么用 JSONL 而不是数据库、数据字段的完整定义，见 [附录 C](#c-想深入了解)。

---

## Part II — Telegram 接入实操

### 15. 开始之前你需要什么

从这一节开始讲「把内容发到 Telegram」。

**整个配置一共 6 步，大约 10 分钟**，全程在浏览器里完成：

| 步骤 | 做什么 | 在哪一节 |
| --- | --- | --- |
| 1 | 跟 Telegram 的 **@BotFather** 说一句话，得到一个机器人 | [第 16 节](#16-第-1-步用-botfather-创建机器人) |
| 2 | 把这个机器人**拉进**你要收消息的群组或频道 | [第 17 节](#17-第-2-步把机器人加进群组或频道) |
| 3 | 找出这个群组/频道的 **Chat ID** | [第 18 节](#18-第-3-步拿到-chat-id) |
| 4 | 把「机器人令牌」和「Chat ID」存进 GitHub Secrets | [第 19 节](#19-第-4-步把凭据存进-github-secrets) |
| 5 | 在 `config/config.yaml` 里把 `telegram.enabled` 改成 `true` | [第 20 节](#20-第-5-步打开-telegram-开关) |
| 6 | 手动跑一次，确认消息真的到了 | [第 21 节](#21-第-6-步手动跑一次验证) |

开始之前，你需要：

- 一个 Telegram 账号（能正常收发消息）；
- 一个**要收消息的地方**——可以是你和机器人的**私聊**，也可以是一个**群组**或**频道**（推荐群组或频道，见 [第 17 节](#17-第-2-步把机器人加进群组或频道)）；
- 前面 [快速开始](#快速开始) 的抓取已经跑通（至少要能成功抓到东西）。

> ⚠️ **重要：本节描述的「代码行为」是经过测试验证的**（包括用一个本地假服务器完整跑通收发），但**「Telegram 网页/客户端上的操作步骤」无法在本项目里自动验证**——那取决于 Telegram 自己的界面。凡是依赖 Telegram 界面细节的地方，下面都标了 **未验证**。如果你发现界面和描述不一样，以 Telegram 显示的为准，流程逻辑不变。

---

### 16. 第 1 步：用 BotFather 创建机器人

机器人（Bot）就是「代替你发消息的程序账号」。创建它是免费的，也不需要审核。

1. 打开 Telegram，在搜索框里搜 **`@BotFather`**。
   > **未验证**：Telegram 上叫 BotFather 的账号有多个仿冒品。请确认你点的是**带官方认证标记**的那个。仿冒的 BotFather 会骗走你的令牌。
2. 点开对话，点 **START**（或发送 `/start`）。
3. 发送命令：

   ```
   /newbot
   ```

4. BotFather 会问你**机器人的显示名字**（随便起，中文也可以，比如 `我的订阅推送`）。发过去。
5. 接着问你**机器人的用户名**。这个必须是**全球唯一**的，而且**必须以 `bot` 结尾**，例如：

   ```
   my_reader_2026_bot
   ```

   如果名字被占用，它会让你换一个，重试即可。
6. 成功之后，BotFather 会回一段话，里面有一行类似这样：

   ```
   Use this token to access the HTTP API:
   数字:字母
   ```

   这一长串就是**机器人令牌（Bot Token）**。

> 🔑 **这一串就是密码。** 拿到它的人可以完全控制你的机器人，能以机器人的身份发消息。所以：
>
> - **不要**发到任何聊天里、不要贴到 issue 里、不要写进 `config/config.yaml`；
> - **不要**写进这个仓库的任何文件（仓库是公开的，写进去等于公开）；
> - 只按 [第 19 节](#19-第-4-步把凭据存进-github-secrets) 把它存进 GitHub Secrets。
>
> 如果一不小心泄露了：回到 BotFather，发 `/revoke`，选你的机器人，它会给你一个新令牌，旧的立刻作废。然后按第 19 节更新 GitHub 里的值。

> **未验证**：BotFather 的对话措辞和按钮位置可能随 Telegram 改版而变化。上面是标准流程；如果你的界面略有不同，按提示走完即可——**最终你需要的只有那一串令牌**。

---

### 17. 第 2 步：把机器人加进群组或频道

现在你有机器人了，但它还不在任何地方，没法收消息。你需要把它**拉进**你要收消息的地方。

你有三种选择，**推荐第 2 或第 3 种**：

| 收消息的地方 | 怎么做 | 适合 |
| --- | --- | --- |
| **① 和机器人的私聊** | 什么都不用做——在 Telegram 里搜你刚才起的机器人用户名，点开，点 START | 只给自己看，最省事 |
| **② 一个群组** | 新建一个群（或用一个已有的），把机器人**作为成员加进去** | **推荐**：可以拉上别人一起看，也能随时退出 |
| **③ 一个频道** | 新建频道，把机器人加为**管理员** | 只想单向广播给一批人 |

#### 怎么把机器人加进群组

1. 在 Telegram 里新建一个群组（点右上角铅笔 → **新建群组**），或者打开一个已有的群。
2. 进入群组 → 点群组标题 → **添加成员**（Add members）。
3. 在搜索框里输入你刚创建的**机器人用户名**（比如 `my_reader_2026_bot`），选中它，添加。
4. 添加完成后，在群里**随便发一条消息**（这很重要，见 [第 18 节](#18-第-3-步拿到-chat-id)）。

#### 怎么把机器人加进频道

1. 新建频道，或打开已有频道。
2. 频道 → **管理员**（Administrators）→ **添加管理员**。
3. 搜索你的机器人用户名，选中，**给「发布消息」权限**（默认就会有），保存。

> ⚠️ **「机器人进了群」不等于「机器人能发消息」。** 这两件事必须分开确认：
>
> - **在群里**：它只是成员。
> - **能发消息**：在群里，它需要有**发消息权限**；在频道里，它**必须是管理员**——普通成员机器人无法在频道发言。
>
> 如果你配好了但一条消息都收不到，而日志里写的是 `HTTP 403: Forbidden: bot is not a member of the chat` 或 `chat not found`，那就是这一步没做全。见 [第 23 节](#23-telegram-排查现象--检查--如何修复)。
>
> **未验证**：群组/频道的权限界面（哪些开关叫什么是 Telegram 决定的）无法在本项目里自动核对。请以 Telegram 实际显示的为准。

#### 私聊、群组、超级群、频道的区别

| 类型 | 机器人要怎么加入 | 机器人能发消息吗 | Chat ID 长相 |
| --- | --- | --- | --- |
| **私聊** | 不用加，搜到点 START 就行 | ✅ 能（前提是你先说过话） | 正数，如 `123456789` |
| **普通群组**（老群） | 作为成员加入 | ✅ 能（需要有发言权限） | 负数，如 `-123456789` |
| **超级群**（新建的群基本都是） | 作为成员加入 | ✅ 能（需要有发言权限） | 负数，`-100` 开头，如 `-1001234567890` |
| **频道** | **必须设为管理员** | ✅ 能（**管理员**才行） | 负数，`-100` 开头，如 `-1001234567890` |

**建议**：第一次配置时，**先用自己的私聊验证一遍**（搜机器人 → START → 看能不能收到）。私聊通了，说明令牌、Chat ID、网络、代码全都是对的；这时再换成群组或频道，如果收不到，就一定是权限问题，排查范围一下子小了很多。

---

### 18. 第 3 步：拿到 Chat ID

**Chat ID 是「往哪里发」的地址**，和机器人令牌是**两回事**：

| | 机器人令牌（Bot Token） | Chat ID |
| --- | --- | --- |
| 回答的问题 | **谁在发** | **发给谁** |
| 长相 | `123456789:AAExample...`（含冒号，很长） | 一串数字，通常是**负数**，比如 `-1001234567890` |
| 会变吗 | 只有你主动 revoke 才会变 | 不变 |
| 存哪 | GitHub Secret `TELEGRAM_BOT_TOKEN` | GitHub Secret `TELEGRAM_CHAT_ID` |

> ❗ **最常见的错误就是把这两个弄混**：把令牌填进 `TELEGRAM_CHAT_ID`，或者把 Chat ID 填进 `TELEGRAM_BOT_TOKEN`。症状是运行时收到 `HTTP 401: Unauthorized` 或 `Bad Request: chat not found`。

#### 怎么查 Chat ID

1. 先确认 [第 17 节](#17-第-2-步把机器人加进群组或频道) 做完了——机器人已经在群/频道里，而且你在群里发过至少一条消息。
2. 在浏览器里打开下面这个网址（**把 `<你的令牌>` 换成第 16 节拿到的那一串，冒号一起换进去**）：

   ```
   https://api.telegram.org/bot<你的令牌>/getUpdates
   ```

3. 你会看到一段 JSON。在里面找 `"chat"` 下面的 `"id"`：

   ```json
   {
     "ok": true,
     "result": [
       {
         "message": {
           "chat": {
             "id": -1001234567890,
             "title": "我的订阅群",
             "type": "supergroup"
           },
           "text": "随便发的消息"
         }
       }
     ]
   }
   ```

   这个 `-1001234567890` 就是 **Chat ID**（**连负号一起复制**）。

关于负号，解释一下免得你以为是出错：

| Chat ID 长相 | 是什么 |
| --- | --- |
| 正数，如 `123456789` | 你和机器人的**私聊** |
| 负数，如 `-123456789` | 老的**普通群组** |
| 负数且以 `-100` 开头，如 `-1001234567890` | **超级群** 或 **频道**（现在新建的群基本都是这种） |

**负号是 ID 的一部分，必须原样保留。**

#### 如果 `getUpdates` 返回的是空的 `"result": []`

按顺序排查：

1. **你还没在群里发过消息。** 先去群里发一条（比如 `/start@你的机器人用户名`），再刷新这个网址。机器人在群里默认**只能看到提到它的消息或命令**，所以随便发一句不带它的，可能什么都不会出现。
2. **消息太久了。** `getUpdates` 只显示最近的更新，等太久就翻不到了。再发一条即可。
3. **在私聊里查。** 最省事的办法：直接搜你的机器人，点 START，然后刷新 `getUpdates`。私聊里一定会有。
4. **确认你用的令牌是对的**，没有多余的空格或换行。
5. **确认机器人确实在群里**（[第 17 节](#17-第-2-步把机器人加进群组或频道)）。

> 🔒 **安全提醒**：`getUpdates` 的网址里**含有你的令牌**。所以：
>
> - 不要在公共电脑上打开这个网址；
> - 不要把网址截图发到任何地方；
> - 浏览器可能会把这条记录存进历史，用完之后建议清一下。
>
> 万一泄露了，`/revoke` 换一个新令牌就好。

---

### 19. 第 4 步：把凭据存进 GitHub Secrets

**不要**把令牌或 Chat ID 写进 `config/config.yaml`。仓库是公开的，写进去就等于公开。（而且程序会**直接报错拒绝启动**，因为配置里只允许写「变量名」，见 [第 20 节](#20-第-5-步打开-telegram-开关)。）

正确做法是把它们存成 GitHub 的 **Secrets**（加密的仓库级变量，只有工作流运行时才读得到，日志里会自动打码）：

1. 打开你 Fork 后的仓库页面。
2. 点 **Settings**（仓库自己的设置，不是账号设置）。
3. 左侧栏找到 **Secrets and variables** → 点开 → 选 **Actions**。
4. 点右边绿色的 **New repository secret** 按钮。
5. 添加**第一个**：
   - **Name** 填：`TELEGRAM_BOT_TOKEN`
   - **Secret** 填：第 16 节拿到的机器人令牌（`123456789:AAExample...`）
   - 点 **Add secret**
6. 再点一次 **New repository secret**，添加**第二个**：
   - **Name** 填：`TELEGRAM_CHAT_ID`
   - **Secret** 填：第 18 节拿到的 Chat ID（**连负号**，如 `-1001234567890`）
   - 点 **Add secret**

完成后这一页应该能看到两条：

```
TELEGRAM_BOT_TOKEN    Updated ... ago
TELEGRAM_CHAT_ID      Updated ... ago
```

需要注意：

- **名字必须完全一致**：`TELEGRAM_BOT_TOKEN` 和 `TELEGRAM_CHAT_ID`，全大写、下划线。多一个空格、小写一个字母都会导致读不到。
- 存好之后**看不到原值**了（这是正常的，GitHub 只允许覆盖，不允许查看）。想改就再点一次 Update。
- 如果名字想用别的，也可以——但要同时改 `config/config.yaml` 里的 `bot_token_env` / `chat_id_env`（见 [第 20 节](#20-第-5-步打开-telegram-开关)）。
- 如果你把 Secret 的名字写错了，运行时会看到 `telegram is enabled but bot token is missing`，见 [第 23 节](#23-telegram-排查现象--检查--如何修复)。

> ✅ 工作流文件里已经写好了这两行（你**不需要**改它）：
>
> ```yaml
> env:
>   TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
>   TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
> ```
>
> 它们是把密钥**作为环境变量**传进去，而不是拼进命令行——后者会把密钥留在日志里。

---

### 20. 第 5 步：打开 Telegram 开关

打开 `config/config.yaml`，找到 `telegram` 这一段（默认是关着的）：

```yaml
telegram:
  enabled: false
  bot_token_env: TELEGRAM_BOT_TOKEN
  chat_id_env: TELEGRAM_CHAT_ID
```

把 `enabled` 改成 `true`：

```yaml
telegram:
  enabled: true
  bot_token_env: TELEGRAM_BOT_TOKEN
  chat_id_env: TELEGRAM_CHAT_ID
```

改完 **Commit changes**。这就是全部——**配置里不需要、也不允许写令牌本身**。

| 字段 | 必填 | 含义 |
| --- | --- | --- |
| `enabled` | 否 | `true` 打开推送，`false` 关闭（默认 `false`）。关掉之后整个项目照常运行，只是不推送。 |
| `bot_token_env` | 否 | **存令牌的那个环境变量的名字**，默认 `TELEGRAM_BOT_TOKEN`。这里填的是**名字**，不是令牌。 |
| `chat_id_env` | 否 | **存 Chat ID 的那个环境变量的名字**，默认 `TELEGRAM_CHAT_ID`。同样填名字。 |

#### 为什么这里只有「名字」

因为配置**要提交进仓库**。把密钥本身写进来，等于把它公开。所以程序的设计是：

- 配置里只写「去哪个环境变量里找」；
- 真正的值由 GitHub Secrets 在运行时注入。

而且，如果你**真的**把令牌写进了这一段，程序**不会**默默忽略它——它会**启动时报错**并指出这个多余的字段。这是故意的：否则你会对着配置里明明白白的令牌，收到「bot token 缺失」的提示，根本不知道问题在哪。

> 常见错误示范（**会启动报错**，错误信息会直接点名 `bot_token`）：
>
> ```yaml
> provider:
>   order: [nitter]
>   nitter:
>     endpoints: [https://nitter.poast.org]
> telegram:
>   enabled: true
>   bot_token: "123456789:AA..."    # ✗ 不允许：多余字段
>   chat_id: "-1001234567890"       # ✗ 不允许：多余字段
> ```
>
> 报错内容类似：`telegram.bot_token Extra inputs are not permitted`。
> 看到这个就说明：**你的令牌放错地方了**，请按 [第 19 节](#19-第-4-步把凭据存进-github-secrets) 放进 GitHub Secrets。

#### 关掉推送

把 `enabled` 改回 `false` 即可。**关掉之后：**

- 项目照常抓取和存储，行为和 [快速开始](#快速开始) 完全一样；
- 不会创建 `data/delivery/` 目录；
- 运行报告里不会出现 `output:` 那一行。

---

### 21. 第 6 步：手动跑一次验证

配置完成后，先手动跑一次，确认消息真的能到。

1. 点 **Actions** → 左侧 **Fetch X, RSS and YouTube data** → 右侧 **Run workflow**。
2. **第一次验证建议只抓一个账号**，避免一次发太多消息：在 `account` 里填 `accounts.yaml` 中的某一个账号名，然后运行。
3. 等运行结束（绿勾或黄叹号），去看你的 Telegram 群组/频道。

**你应该看到**：一条或多条消息，格式类似：

```
标题（如果有）

正文内容……

来源：某个账号或网站
时间：2024-09-11T19:31:00+00:00
https://example.com/...

附件：https://example.com/image.jpg
```

**如果没看到消息**，先别急着改配置，按顺序做这三件事：

1. **看运行报告里有没有 `output: telegram` 这一行**（见 [第 13 节](#13-几个固定行为)）。
   - **完全没有这一行** → 说明配置没生效：`telegram.enabled` 是不是真的改成 `true` 了？改动 Commit 了吗？
   - **有这一行，且写着 `ok`** → 说明程序认为发出去了。那问题在 Telegram 那边（比如你看错群了）。再检查一遍 [第 17 节](#17-第-2-步把机器人加进群组或频道)、[第 18 节](#18-第-3-步拿到-chat-id)。
   - **有这一行，但写着 `FAILED`** → 后面会跟着原因。对照 [第 23 节](#23-telegram-排查现象--检查--如何修复) 的表格处理。
2. **确认你看的是同一个群/频道**——特别是如果你有多个群。
3. **确认机器人还在群里**（有人可能把它移出了）。

> **第一次运行可能会一次发很多条**，因为仓库里已经存过的内容如果从没推送过，都算「待发送」。这是设计如此（不会漏发）。如果你只想让**以后的新内容**推送，可以先让它发完这一轮，之后就只发增量了。

---

### 22. 推送出来的消息长什么样

一条内容会被渲染成「标题 + 正文 + 来源 + 时间 + 链接 + 一个附件链接」的形式：

```
<b>标题</b>

正文内容

来源：xxx
时间：2024-09-11T19:31:00+00:00
https://example.com/...

附件：https://example.com/image.jpg
```

- 用的是 Telegram 的 **HTML** 模式，只做**基础排版**（标题加粗）。**不是 Markdown**，也不做复杂样式。
- 正文里的 `<`、`>`、`&` 会被**自动转义**。所以哪怕原文里有一堆 HTML 标签或数学符号，也不会把你的消息搞乱，更不会因为一个多余的尖括号导致整批发不出去。
- 源数据里如果**没有**可靠的时间，程序不会瞎猜，而是把来源给的原样文字显示出来并标注「（原始值）」。
- 一条内容里如果有多个附件，只显示**第一个**的链接——否则一条消息会被图片链接淹没。

#### 太长的内容会被自动分片

Telegram 单条消息的上限是 **4096 个字符**。超过的内容会被**自动切成多条**依次发出。

有两个细节值得知道：

1. **只有所有分片都发送成功，这一条内容才算「已发送」。** 如果第 1 片成功、第 2 片失败，整条内容仍然算「待发送」，下次会**整条重发**。这是刻意的：半个摘要不算发出去，把它标记成完成会静默丢掉剩下的部分。
2. 切分时会**优先在段落或换行处断开**，所以消息读起来还是完整的，不会把一个词从中间劈开。

> 顺便说：Telegram 的 4096 是按它自己的计数方式算的，一个表情符号或生僻汉字可能算 2 个。程序按同一套规则计算，所以不会出现「看起来没超但被拒」的情况。

#### 连续失败时会主动停下来

如果**连续 5 条**都发失败，程序会**停止这一轮发送**，把剩下的全部留到下次运行。

为什么要这样：消息是一条一条发的，所以「网络不通」这种问题会让每一条都白白等一次超时。假如你有几百条待发送、而 Telegram 正好连不上，程序会一条一条地耗下去，最后可能超出工作流的时间上限而被强行终止。

**停下来不会丢任何东西**——没发出去的条目依然是「待发送」，下次运行会全部补发。而一旦有一条发送成功，计数器就归零，所以偶尔一条内容有问题不会影响后面的。

运行报告里会明确写出来：

```
output: telegram — FAILED: 0 delivered, 31 failed, 0 skipped (31 pending)
        -> timeout after 2s: timed out; aborted after 5 consecutive failures; 26 item(s) left pending for the next run
```

---

### 23. Telegram 排查：现象 → 检查 → 如何修复

在 **Summarise the run** 步骤或本地运行的输出里找 `output: telegram` 那一行，它后面写的原因对应下表。

| 现象（你看到的原因） | 检查什么 | 如何修复 |
| --- | --- | --- |
| `telegram is enabled but bot token is missing` | 配置说打开推送，但程序在环境里找不到令牌 | 检查 [第 19 节](#19-第-4-步把凭据存进-github-secrets)：Secret 的名字是不是**完全**等于 `TELEGRAM_BOT_TOKEN`；如果改了名字，`config/config.yaml` 的 `bot_token_env` 要跟着改。**在本地运行时**，环境变量要先设好（见 [附录 A](#a-在电脑上本地运行可选)）。 |
| `telegram is enabled but chat id is missing` | 同上，缺的是 Chat ID | 检查 Secret 名 `TELEGRAM_CHAT_ID` 与 `chat_id_env`。 |
| `HTTP 401: Unauthorized` | 令牌不对，或者**你把 Chat ID 填进了令牌的位置** | 回 [第 16 节](#16-第-1-步用-botfather-创建机器人) 重新复制令牌。特别注意令牌里有个**冒号**，要一起复制。必要时用 BotFather 的 `/revoke` 换一个新的。 |
| `HTTP 400: Bad Request: chat not found` | Chat ID 不对，或者**你把令牌填进了 Chat ID 的位置**，或者机器人**不在**那个群/频道里 | 回 [第 18 节](#18-第-3-步拿到-chat-id) 重新取 Chat ID（**负号要带上**）。确认机器人确实在群里（[第 17 节](#17-第-2-步把机器人加进群组或频道)）。 |
| `HTTP 403: Forbidden: bot was kicked...` / `bot is not a member of the chat` | 机器人被移出群了，或者它**没有发消息的权限** | 把机器人重新加进群。如果是**频道**，必须把它设成**管理员**（见 [第 17 节](#17-第-2-步把机器人加进群组或频道)）。 |
| `HTTP 403: Forbidden: bot can't initiate conversation with a user` | 想发私聊，但你**从没跟这个机器人说过话** | 在 Telegram 里搜到你的机器人，点 **START**。Telegram 不允许机器人主动给没打过招呼的人发消息。 |
| `HTTP 429: Too Many Requests` | 发得太快，被限流了 | **不用管**。程序会自动等待并重试。这类内容会保持「待发送」，下次运行继续。 |
| `HTTP 500` / `502` / 超时 / `connection error` | Telegram 那边临时出问题，或者网络不通 | **不用管**。内容已经存好了，保持「待发送」，下次运行自动补发。**不会丢内容。** |
| `part 2/3 HTTP 400: message is too long` | 某条内容太长，分片后仍被拒 | 少见。这一条会保持「待发送」。如果持续出现，请把这条内容的情况记下来反馈。 |
| `aborted after 5 consecutive failures; N item(s) left pending` | 连续 5 条都发不出去，程序主动停止了这一轮 | **不用管**。真正的原因在前面那句（通常是网络或权限问题），先按上面的表格修那个；没发出去的会在下次运行全部补发。 |

**记住一件事：任何推送失败都不会丢内容。** 内容已经存进 `data/items/` 了，推送状态记在 `data/delivery/` 里，标记为「还没发出去」。下次运行会**从仓库里读回来重新发**，**不需要重新抓取**。

想手动补发，什么都不用做——等下一次定时运行，或者去 Actions 手动 Run workflow 一次即可。

---

## Part III — QQ / OneBot 接入实操

### 24. OneBot 是什么，X-Reader 用它的什么

X-Reader **可以**把内容推送到 QQ。但它和 Telegram 有一个根本区别，必须先说清楚：

> **X-Reader 自己不会登录 QQ，也不会一直运行。** 它只是往你自己已经跑起来的 **OneBot** 程序**发一条 HTTP 请求**，由那个程序负责把消息发到 QQ 群里。

**OneBot 是什么。** OneBot 是一个**开放的聊天机器人接口标准**（不是某一个软件）。很多客户端实现它，比如 NapCat、Lagrange 这类——它们负责登录你的 QQ 账号，并对外暴露一套 HTTP 接口。X-Reader 是这套接口的**客户端**。

也就是说，你需要**另外准备一个常驻运行的 OneBot 实现**，让它登录你的 QQ 并开启 HTTP 接口。X-Reader 这边**不需要服务器、不需要 VPS、不需要常驻进程**——它每天跑完就退出，和以前完全一样。

**X-Reader 用 OneBot 的什么。** 只有一个动作：往 `/send_group_msg` 发一条 POST 请求。

```
POST <你的 OneBot 地址>/send_group_msg
{
  "group_id": 123456789,
  "message": [{"type": "text", "data": {"text": "要发的内容"}}]
}
```

返回的 JSON 里 `retcode` 是 `0` 才算成功。**不是 `0` 就一律算失败**（内容保持「待发送」，下次重发）。

**没有用任何 QQ SDK**，也没有用 QQ 官方机器人接口，也没有长连接（没有 WebSocket、没有常驻事件循环）——就是一次普通的 HTTP 请求，用的是项目里已有的 `httpx`。

**四个容易混淆的概念**，出错时先分清楚：

| 名字 | 是什么 | 是秘密吗 | 放哪 |
| --- | --- | --- | --- |
| **QQ 群号**（`group_id`） | 消息发到**哪个群** | **不是** | `config/config.yaml` |
| **OneBot 地址**（`api_base`） | OneBot 的 HTTP 接口**在哪** | **不是** | `config/config.yaml` |
| **OneBot Access Token** | 调 OneBot 接口要带的**密码** | **是** | GitHub Secret / 环境变量 |
| **环境变量名**（`access_token_env`） | 上面那个密码**存在哪个变量名里** | 不是 | `config/config.yaml` |

> ⚠️ **QQ 群号和 QQ 密码／令牌完全是两回事。** 群号就是群里那个数字，公开的、不是秘密。X-Reader **不需要**你的 QQ 账号密码、不需要 QQ 登录凭据，也不需要 QQ 官方机器人的 AppID / Token。

**这个功能不能做什么：**

- **不能**帮你登录 QQ，也不能代替 OneBot。QQ 的登录状态由你的 OneBot 程序负责。
- **不能**接收 QQ 消息。X-Reader 只发不收，不是 QQ 机器人服务器。
- **不能**发图片、语音、合并转发等富媒体。当前只发**纯文本**。
- **不能**发到好友私聊，只发群（`group_id`）。优先保证「稳定地把文字发出去」。

---

### 25. 第 1 步：准备好你自己的 OneBot

X-Reader 不附带 OneBot，你需要自己准备一个。这一步的细节取决于你选哪个实现，**无法在本项目里自动验证**。

1. **选一个 OneBot 实现**（例如 NapCat、Lagrange 等），按它自己的文档装好。
2. **让它登录你的 QQ 账号**。用哪个 QQ 号、怎么登录，都由那个程序负责——X-Reader 完全不参与。
3. **开启它的 HTTP 接口**（有时叫 HTTP API / HTTP 服务器 / OneBot HTTP）。
4. **记下它监听的地址和端口**。这会在 [第 27 节](#27-第-3-步确定-api_base) 用到。
5. **决定要不要设访问令牌**。见 [第 29 节](#29-第-5-步决定要不要-access-token)。

> 💡 一个实际的考虑：OneBot 需要**一直开着**（它登录着你的 QQ）。如果你没有一台一直开着的机器，Telegram 是更省事的选择——它不需要任何常驻程序（见 [Part II](#part-ii--telegram-接入实操)）。
>
> **未验证**：不同 OneBot 实现的安装与配置界面差异很大，且会随版本变化。上面只说明「你需要准备好什么」，具体操作请以你所选实现的文档为准。

---

### 26. 第 2 步：确认 HTTP API 可用

在配置 X-Reader 之前，先确认 OneBot 的 HTTP 接口本身是通的。这一步能挡掉后面一大半的排查工作。

确认三件事：

1. **进程在跑**。你的 OneBot 实现处于运行状态，并且 QQ 已登录（大部分实现会打印登录成功）。
2. **HTTP 接口已开启**，并监听着你记下的那个地址和端口。
3. **能访问到**。在同一台机器上用浏览器或 `curl` 访问一下接口根地址（`api_base`），能返回东西（哪怕是报错 JSON）就说明**通了**；完全连不上说明地址或端口不对，或者接口没开。

> ⚠️ **如果 X-Reader 跑在 GitHub Actions 上，它访问不到你本机的 `127.0.0.1`。** 默认配置 `api_base: "http://127.0.0.1:3000"` 只适用于**在你自己电脑上本地运行** X-Reader。要在 Actions 上用，OneBot 必须有一个**从公网可达**的地址（这通常意味着你自己做端口转发或反向代理，并且**务必设置访问令牌**）。
>
> 这是本方案里唯一需要你自己解决网络问题的地方——X-Reader 不提供任何隧道或中转服务。

---

### 27. 第 3 步：确定 `api_base`

`api_base` 是 **OneBot HTTP 接口的根地址**。X-Reader 会在它后面拼上 `/send_group_msg`。

| 场景 | `api_base` 大概长什么样 |
| --- | --- |
| X-Reader 和 OneBot 在**同一台机器**上 | `http://127.0.0.1:3000` |
| OneBot 在**局域网**里另一台机器上 | `http://192.168.1.50:3000` |
| OneBot 有**公网可达**的地址 | `https://your-onebot.example.com` |

要点：

- **必须带 `http://` 或 `https://`。**
- **结尾不要加 `/`**（加了程序也会自己去掉，但写规范一点更清楚）。
- **它不是秘密**，写在 `config/config.yaml` 里。
- 默认值就是 `http://127.0.0.1:3000`。

---

### 28. 第 4 步：确定 `group_id`

`group_id` 是**要发到哪个 QQ 群**，也就是群号。

- 它是一个**数字**（QQ 群号）。在配置文件里**写数字或写字符串都行**，程序两种都接受。
- **它不是秘密**，写在 `config/config.yaml` 里。
- **只能发群，不能发好友私聊。** 如果只想自己看，可以建一个只有自己（和机器人账号）的群。
- **机器人账号必须在那个群里**，否则 OneBot 会拒绝发送，返回非 0 的 `retcode`（见 [第 35 节](#35-qq-排查现象--原因--检查--修复)）。

---

### 29. 第 5 步：决定要不要 access token

访问令牌（access token）是 OneBot 用来**限制谁能调它的接口**的一道口令。

- **可以没有。** 很多本地部署的 OneBot 根本不设令牌——这种情况**留空即可**，程序不会带任何认证头。**这是受支持的正常配置，不是错误。**
- **如果设了**，就必须让 X-Reader 知道，否则接口会返回 401 / 403。

程序的行为是：

- 配置里只写**令牌存在哪个环境变量名下**（`access_token_env`，默认 `ONEBOT_ACCESS_TOKEN`）；
- 真正的值由环境变量提供；
- 令牌通过 `Authorization: Bearer <令牌>` 请求头发送，**不会出现在 URL 里**；
- 令牌会被从程序产生的任何日志、报告和错误信息里**打码**。

> **如果 OneBot 暴露在公网上，强烈建议设置令牌。** 没有令牌的 OneBot 接口，任何知道地址的人都能用它发消息。

**OneBot 没设令牌时，`access_token_env` 留空即可，不要在环境变量里放一个假的占位符。**

---

### 30. 第 6 步：把 token 存进 GitHub Secrets

位置和 Telegram 的 Secret 一样（见 [第 19 节](#19-第-4-步把凭据存进-github-secrets)）：**Settings → Secrets and variables → Actions → New repository secret**。

- **Name** 填：`ONEBOT_ACCESS_TOKEN`
- **Secret** 填：你 OneBot 里设置的访问令牌

**如果你的 OneBot 没设令牌，这一步跳过。**

工作流里已经写好了这一行（你不需要改它）：

```yaml
env:
  ONEBOT_ACCESS_TOKEN: ${{ secrets.ONEBOT_ACCESS_TOKEN }}
```

> 没设置这个 Secret 时它展开成空字符串，程序就不带认证头——正好对应「OneBot 没设令牌」的情况。
>
> **令牌和群号、地址一样，绝对不能写进 `config/config.yaml`。** 配置文件是要提交进仓库的。

---

### 31. 第 7 步：打开 QQ 开关

`config/config.yaml` 里默认是这样（关着的）：

```yaml
qq:
  enabled: false
  api_base: "http://127.0.0.1:3000"
  group_id: ""
  access_token_env: ONEBOT_ACCESS_TOKEN
  timeout: 20
```

要打开，改成：

```yaml
qq:
  enabled: true
  api_base: "http://127.0.0.1:3000"      # 你的 OneBot HTTP 接口地址
  group_id: 123456789                     # 要发到哪个群（数字，不用加引号）
  access_token_env: ONEBOT_ACCESS_TOKEN   # 存令牌的环境变量名（不是令牌本身）
  timeout: 20
```

| 字段 | 必填 | 含义 |
| --- | --- | --- |
| `enabled` | 否 | `true` 打开，`false` 关闭（默认 `false`）。 |
| `api_base` | 打开时必填 | OneBot 的 HTTP 接口根地址，`http://` 或 `https://` 开头，**结尾不要加 `/`**。 |
| `group_id` | 打开时必填 | 目标 QQ 群号。写数字或写字符串都行。 |
| `access_token_env` | 否 | **存令牌的环境变量名**，默认 `ONEBOT_ACCESS_TOKEN`。**OneBot 没设令牌时留空即可。** |
| `timeout` | 否 | 单次请求超时秒数，默认 20。 |

> `api_base` 和 `group_id` **不是秘密**，所以它们写在配置文件里。只有**令牌**必须放进 Secret。

#### 为什么 `access_token_env` 填的是「名字」

和 Telegram 那边同一个道理（见 [第 20 节](#20-第-5-步打开-telegram-开关)）：配置**要提交进仓库**，写进去就等于公开。所以配置里只写「去哪个环境变量里找」，真正的值由 GitHub Secret 在运行时注入。

如果你**真的**把令牌写进了这一段，程序**不会**默默忽略——它会**启动时报错**并指出这个多余的字段。这是故意的：否则你会对着配置里明明白白的令牌，收到「连不上 OneBot」的提示，根本不知道问题在哪。

---

### 32. 第 8 步：手动跑一次验证

配置完成后，先手动跑一次，确认消息真的能到。

1. 点 **Actions** → 左侧 **Fetch X, RSS and YouTube data** → 右侧 **Run workflow**。
2. **第一次验证建议只抓一个账号**，避免一次发太多消息：在 `account` 里填 `accounts.yaml` 中的某一个账号名，然后运行。
3. 等运行结束，去看你的 QQ 群。

**你应该看到**：一条或多条消息，格式见 [第 33 节](#33-推送出来的消息长什么样)。

**如果没看到消息**，先别急着改配置：

1. **看运行报告里有没有 `output: qq` 这一行**（见 [第 13 节](#13-几个固定行为)）。
   - **完全没有这一行** → 配置没生效：`qq.enabled` 是不是真的改成 `true` 了？改动 Commit 了吗？
   - **有这一行，且写着 `ok`** → 程序认为发出去了。那问题在 OneBot 或 QQ 那边（比如你看错群了）。
   - **有这一行，但写着 `FAILED`** → 后面会跟着原因。对照 [第 35 节](#35-qq-排查现象--原因--检查--修复) 的表格处理。
2. **确认 OneBot 还活着**（登录状态没掉）。
3. **确认机器人账号还在那个群里。**

> **第一次运行可能会一次发很多条**，因为仓库里已经存过的内容如果从没推送过，都算「待发送」。这是设计如此（不会漏发）。

---

### 33. 推送出来的消息长什么样

一条内容渲染成一条**纯文本**消息：

```
【视频标题或文章标题】

正文内容……

来源：某个频道或网站
时间：2024-09-11T19:31:00+00:00
链接：https://example.com/...
```

具体规则（就是 `outputs/qq.py` 里的实际行为）：

| 元素 | 怎么渲染 |
| --- | --- |
| 标题 | 用 `【】` 包起来；**最多 200 个字符**，超出部分截断并加 `…（已截断）` |
| 正文 | **最多 600 个字符**，同样截断并标注；空白字符会被合并成单个空格 |
| 来源 | `来源：<publisher>`（有才显示） |
| 时间 | `时间：<published_at>`；取不到时显示来源给的原样文字并加「（原始值）」 |
| 链接 | `链接：<canonical_url>` |

- **是纯文本，不是 Markdown，也不是 HTML。** OneBot 的富文本形式是 CQ 码字符串；从渲染器里直接吐 CQ 码，是「标题里的 `[CQ:at,qq=...]` 被当成真的 @」这类事故的来源，所以这里刻意只发文本——内容永远按字面显示，不需要转义，也不会被重新解释。
- 标题用 `【】` 包起来，是因为纯文本里没有加粗这种东西。
- 时间取不到时**不会瞎猜**。
- 链接**总是**会带上。所以即使标题或正文被截断了，内容依然找得到——截断只是少一点方便，不会让人够不到原文。

> **不发图片。** 发缩略图会让投递依赖 OneBot 能不能去抓一个第三方 URL，把一条本来可靠的文字链路变得不可靠。所以只发文字，链接始终带上。

#### 每条消息是独立的一次请求

程序对每条内容发一次 HTTP 请求。所以第 1 条失败不会影响第 2 条，每条的成功／失败也是**分别**记录的。

#### 连续失败会主动停下来

和 Telegram 一样：**连续 5 条**都失败，程序会停止这一轮的 QQ 发送，把剩下的留到下次运行。

原因也一样：网络不通时一条一条地耗下去会白白浪费时间，最后可能超出工作流的时间上限。**停下来不会丢任何东西**——没发出去的条目依然是「待发送」。一旦有一条成功，计数器归零。

> **OneBot 这边不做自动重试。** Telegram 被限流时会明确告诉你「等几秒再来」，所以程序会等；OneBot 没有这种信号，重试就成了瞎猜。所以一次失败就是一次失败，内容保持「待发送」，**下次运行**再试。

---

### 34. 投递状态与重复发送

QQ 的投递状态记在**自己那一份文件**里：

```
data/delivery/qq.jsonl
```

它和 Telegram 的 `data/delivery/telegram.jsonl` **完全独立**。这带来三个行为：

- **换群号** → 已经发过的内容**不会**重发到新群（它们在 `qq.jsonl` 里记着是 `sent`）。想让全部重发一遍，把 `data/delivery/qq.jsonl` 删掉（谨慎操作）。
- **换 OneBot 地址** → 不影响「发过什么」的记录，只影响以后往哪发。
- **一个输出挂了不影响另一个** → 同一条内容完全可以处在「Telegram 已发、QQ 没发」的状态，下次运行时 Telegram 跳过、QQ 重发。

**投递是「至少一次」。** 状态在发送**之后**才写。代价是：如果写状态时崩溃，下次会重发一条（重复消息，可恢复）；反过来先写状态再发送，就可能把没送达的内容记成已送达（内容丢失，不可恢复）。**宁可重发，不可丢失。**

> 内容本身存在 `data/items/` 里，和投递状态无关。所以 QQ 那边失败多少次，内容都不会丢——它只是还没发出去。

---

### 35. QQ 排查：现象 → 原因 → 检查 → 修复

在 **Summarise the run** 步骤或本地运行的输出里找 `output: qq` 那一行，它后面写的原因对应下表。

| 现象（你看到的） | 可能原因 | 检查什么 | 如何修复 |
| --- | --- | --- | --- |
| `qq is enabled but api base is missing` | 配置说打开 QQ，但没给地址 | `qq.api_base` 是否为空 | 填上 OneBot 的 HTTP 接口地址（[第 27 节](#27-第-3-步确定-api_base)） |
| `qq is enabled but group id is missing` | 配置说打开 QQ，但没给群号 | `qq.group_id` 是否为空 | 填上群号（[第 28 节](#28-第-4-步确定-group_id)） |
| `connection error` / `connection refused` | OneBot 没在跑，或地址／端口不对 | 在跑 X-Reader 的那台机器上能不能访问 `api_base`（[第 26 节](#26-第-2-步确认-http-api-可用)） | 启动 OneBot，开启 HTTP 接口；把 `api_base` 改成它实际监听的地址。**注意 Actions 访问不到你本机的 `127.0.0.1`。** |
| `timeout after Ns` | 连上了但没在规定时间内返回 | OneBot 是否卡住；`qq.timeout` 是否太小 | 把 `qq.timeout` 调大；或检查 OneBot 的状态 |
| `HTTP 401` / `HTTP 403` | 令牌不对，或该带令牌却没带 | `ONEBOT_ACCESS_TOKEN` 的值和 OneBot 里设置的是否一致；OneBot 是否真的设了令牌 | 让两边一致；OneBot 没设令牌就把 `access_token_env` 留空（[第 29 节](#29-第-5-步决定要不要-access-token)） |
| `HTTP 404` | 地址不对（`api_base` 拼出来不是 OneBot 的接口） | `api_base` 是否只到根地址、有没有多余路径 | 改成 OneBot 的 HTTP 根地址 |
| `HTTP 5xx` | OneBot 自己出错了 | OneBot 的日志 | 看 OneBot 那边的报错。内容保持「待发送」，下次重试 |
| `retcode=<非 0>: <OneBot 给的原因>` | OneBot 收到了请求但拒绝发送 | OneBot 返回的原因；群号是否正确；机器人账号是否在那个群里；是否被禁言 | 按 OneBot 给的原因处理。最常见是群号写错或机器人不在群里 |
| `HTTP 2xx` 但 `response was not JSON` | 地址可能指向了别的服务（不是 OneBot） | `api_base` 指向的是不是 OneBot | 改成真正的 OneBot 地址。**注意：2xx 但读不出 OneBot 的成功码，程序一律算失败**，不会误记成已发送 |
| `aborted after 5 consecutive failures; N item(s) left pending` | 连续 5 条都失败，程序主动停了这一轮 | 上面几行里的**第一个**原因 | 先修那个根因；没发出去的会在下次运行全部补发 |

> **任何失败都不会被记成成功。** 这是刻意的：把没送达的记成已送达，内容就永远丢了；把送达的记成失败，最多下次重发一条。**宁可重发，不可丢失。**

**想手动补发**：什么都不用做——等下一次定时运行，或者去 Actions 手动 Run workflow 一次即可。

---

## 附录

### A. 在电脑上本地运行（可选）

> **日常使用不需要这一步。** 在 GitHub 上 Fork 之后，一切都在云端自动完成。

需要 Python **3.12 或更高版本**。

#### 安装依赖

在项目目录下执行：

```bash
python -m pip install -r requirements.txt
```

#### 运行

```bash
python main.py                    # 抓取全部已启用的来源
python main.py --account jack     # 只抓某一个 X 账号
python main.py --no-rss           # 这次跳过 RSS 来源
python main.py --no-youtube       # 这次跳过 YouTube 频道
python main.py --json             # 输出机器可读的 JSON 报告
python main.py --strict           # 只要有单元失败、或有内容没发出去，就返回非 0
```

数据同样落在 `data/` 目录。可以用这条命令检查存下来的数据结构是否正常：

```bash
python scripts/verify_storage.py
```

#### 想在本地测试 Telegram 推送

本地运行时，两个凭据要从**环境变量**里读。先在同一个终端里设置好，再运行：

Windows PowerShell：

```powershell
$env:TELEGRAM_BOT_TOKEN = "123456789:AA..."
$env:TELEGRAM_CHAT_ID   = "-1001234567890"
python main.py
```

macOS / Linux / Git Bash：

```bash
export TELEGRAM_BOT_TOKEN="123456789:AA..."
export TELEGRAM_CHAT_ID="-1001234567890"
python main.py
```

记得同时把 `config/config.yaml` 里的 `telegram.enabled` 改成 `true`。

#### 想在本地测试 QQ 推送

QQ 需要**两样**东西：地址和群号（写在配置里），以及一个可选的令牌（写在环境变量里）。

先确认你的 OneBot 已经跑起来、HTTP 接口能访问，然后把 `config/config.yaml` 里的 `qq.enabled` 改成 `true`，`api_base` 和 `group_id` 填对。如果 OneBot 设了访问令牌：

Windows PowerShell：

```powershell
$env:ONEBOT_ACCESS_TOKEN = "你的令牌"
python main.py
```

macOS / Linux / Git Bash：

```bash
export ONEBOT_ACCESS_TOKEN="你的令牌"
python main.py
```

> 🔒 **注意**：
>
> - 用这种方法设的变量**只在当前终端窗口有效**，关掉就没了（这正是好事，不会残留在系统里）；
> - 不要把上面的命令写进任何**会被提交**的文件。项目的 `.gitignore` 已经忽略了 `.env`，但最安全的做法还是只在这个终端里临时设置。

#### 跑测试（想确认程序没被改坏时）

```bash
python -m pytest -q
```

---

### B. 常见问题

**Q1：我需要 X 官方 API key，或者 YouTube Data API key 吗？要花钱吗？**
都不需要，也不花钱。抓 X 走的是公开的 Nitter 镜像站；抓 RSS 就是普通网页请求；抓 YouTube 读的是频道公开的订阅源，**不需要 Data API key，也不需要 Google 账号**；推 Telegram 用的是官方免费的机器人接口；推 QQ 走的是你自己跑起来的 OneBot 接口。整个方案不需要任何官方 API、不需要付费服务、不需要服务器或 VPS。

**Q2：为什么某个 X 账号这次没抓到内容？**
Nitter 镜像站不稳定，经常临时挂掉或被限流。程序会在多个镜像站之间自动切换，也会在下次运行时重试。**单个账号失败不影响其他账号**，不用特别处理。如果**所有**账号长期都抓不到，才需要看看镜像站是不是大面积不可用了（可以换 `config/config.yaml` 里 `nitter.endpoints` 的地址）。

**Q3：Fork 之后为什么它不自动运行？**
GitHub 默认不会在 Fork 出来的仓库里跑工作流。需要你手动打开一次：**Actions** 标签页 → 点 **I understand my workflows, go ahead and enable them**。见 [快速开始](#快速开始) 第 2 步。

**Q4：运行成功了，但数据没有提交回仓库，怎么回事？**
多半是写权限没开。去 **Settings → Actions → General → Workflow permissions**，选 **Read and write permissions** 并保存。见 [快速开始](#快速开始) 第 3 步。

**Q5：它会把同样的内容重复存很多遍吗？**
不会。每条内容有身份标记（来源 + 条目 ID），已经存过的会被跳过。重复运行只会显示 `dup` 增加，`new` 是 0。

**Q6：我改了 RSS 来源的 `id`，结果内容全部重复了，怎么办？**
这是预期行为。`id` 是内容归属的一部分，改了它等于换了一个新来源，旧内容会被重新当作新内容。**把 `id` 改回原来的值**即可恢复。以后**一次选定不要再改**。

**Q7：能只要 RSS、或者只要 YouTube、完全不要 X 吗？**
都可以。把 `config/config.yaml` 里的 `provider.order` 改成 `[]`，`accounts.yaml` 写成 `accounts: []`，然后只在 `rss_sources` 或 `youtube_channels` 里填你要的来源。见 [第 10 节](#10-最小可运行配置)。

**Q8：推送会重复发同一条内容吗？**
不会。已经成功发送过的内容会记在对应输出的 `data/delivery/<输出>.jsonl` 里，下次跳过。每个输出各记各的。详见 [第 8 节](#8-存储去重与投递状态)。

**Q9：推送没发出去，内容会丢吗？**
**不会。** 内容已经存进 `data/items/` 了。没发出去的会被标记成「待发送」，下次运行**从仓库里读回来重新发**，不需要重新抓取。详见 [第 8 节](#8-存储去重与投递状态)、[第 23 节](#23-telegram-排查现象--检查--如何修复)、[第 35 节](#35-qq-排查现象--原因--检查--修复)。

**Q10：第一次打开推送，为什么一下子发了几十条消息？**
因为仓库里**所有**已存内容都还没推送过，程序会把它们全部发一遍（这是为了不遗漏）。发完这一轮，之后每次只发新增的。详见 [第 8 节](#8-存储去重与投递状态)。

**Q11：我能把内容同时发到多个地方吗？**
可以同时发到**一个** Telegram 目标和一个 QQ 群（两个输出各自独立）。但同一个输出不支持多个目标——配置里只有一个 `TELEGRAM_CHAT_ID`、一个 `qq.group_id`。想换目标就改这个值（已发过的不会重发到新目标）。

**Q12：能推送到 QQ 吗？**
能。但你需要自己跑一个 OneBot 实现来登录 QQ——X-Reader 只负责往它发 HTTP 请求，不会自己登录 QQ，也不是常驻程序。见 [Part III](#part-iii--qq--onebot-接入实操)。

**Q13：会生成 RSS 输出文件或者做 AI 摘要吗？**
不会。这两项都还没实现。现在它只做「抓取 → 存储 → 推送」。

**Q14：运行会不会跑很久、占用很多额度？**
单次运行一般几分钟内结束，工作流设了 20 分钟超时。定时任务每天一次，公开仓库使用 GitHub Actions 是免费的。Telegram 的机器人接口也是免费的。

**Q15：仓库会不会越来越大？**
会缓慢增长，但每行 JSONL 都很小，且 git 只记录新增的行，增长是可控的。默认的原始响应留存策略是「只在出错时保留」，已经尽量精简。

**Q16：改了配置要重新 Fork 吗？**
不用。直接在你 Fork 的仓库里改文件、提交，下次运行就会用新配置。

**Q17：Telegram 令牌泄露了怎么办？**
立刻回到 **@BotFather**，发 `/revoke`，选中你的机器人，它会给你一个新令牌（旧的立刻失效）。然后按 [第 19 节](#19-第-4-步把凭据存进-github-secrets) 把 GitHub Secret `TELEGRAM_BOT_TOKEN` 更新成新值。

**Q18：我能在自己电脑上先跑跑看吗？**
可以，但**正常使用完全不需要**——在 GitHub 上跑就够了。想本地试的话见 [附录 A](#a-在电脑上本地运行可选)。

**Q19：YouTube 抓不到，怎么办？**
先确认 `youtube.com` 在你的运行环境里能不能访问。程序会明确报告 `network_error`，不会假装成功。单个频道失败不影响其他频道，也不影响 X 和 RSS。另外，频道订阅源只给最近的视频（约 15 条），更早的历史内容拿不到——这是 YouTube 的限制，不是程序截断。见 [第 6.3 节](#63-要关注哪些-youtube-频道)。

**Q20：我填了频道名，程序报错说不认识。**
`youtube_channels` 里要填的是**频道 ID**（`UC...` 开头那一串），不是频道名，也不是 `@handle`。见 [第 6.3 节](#63-要关注哪些-youtube-频道)。

**Q21：QQ 那边一条都没收到。**
先看运行报告里 `output: qq` 那一行写的原因（见 [第 35 节](#35-qq-排查现象--原因--检查--修复)）。最常见的是 OneBot 没在跑、`api_base` 地址不对、或者群号写错。注意 X-Reader 只是往 OneBot 发请求，**QQ 的登录状态由你的 OneBot 负责**。

**Q22：X-Reader 会一直挂着吗？**
不会。X-Reader 每次只运行几分钟就退出。唯一需要一直开着的是 QQ 那侧的 OneBot 实现——那是你自己的程序。不推 QQ 就完全不需要它。

---

### C. 想深入了解

以上内容足够你正常使用。如果你想知道程序内部是怎么组织的（为什么这样分层、为什么用 JSONL 而不是数据库、数据字段是怎么定义的、投递是怎么和「已见」分开的），看这些文档：

| 文档 | 内容 |
| --- | --- |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | 分层设计、配置、存储选型、测试、投递设计、YouTube / QQ 的实现现状 |
| [`docs/ARCHITECTURE_FREEZE_V3.md`](docs/ARCHITECTURE_FREEZE_V3.md) | 跨来源的数据契约（字段定义与边界） |
| [`docs/NEXT_PHASE_PLAN.md`](docs/NEXT_PHASE_PLAN.md) | 阶段状态、后续计划、已知限制 |
| [`data/README.md`](data/README.md) | `data/` 里每种文件的语义（规范存储 vs 证据 vs 投递状态） |

想自己动手扩展（加一个输入来源、加一个输出适配器），见 [第 14 节](#14-想自己扩展它)。
