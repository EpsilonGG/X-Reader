"""Check README.md against the code that actually exists.

A README is the one artefact nobody tests, so it drifts first and hurts most.
This script makes the claims checkable:

1. every ```yaml block parses, and every *complete* config block loads through
   the real ``load_config`` — a README example that the loader rejects is worse
   than no example;
2. the deliberately-wrong example (a token in config.yaml) actually *is*
   rejected, so the warning in the README is true rather than decorative;
3. the support-status table agrees with the filesystem: anything marked
   implemented has code, anything marked not-implemented has none;
4. the sample run report in the README is *byte-identical* to what
   ``render_report`` prints for the same inputs, so it cannot drift;
5. every table-of-contents link resolves to a heading, and the numbered
   subsections run 1..N with no gaps;
6. the YouTube and QQ sections describe things that are actually wired:
   config keys, env var names and workflow secrets all exist;
7. every repo path the README names in backticks exists on disk, and every file
   named in the documented directory tree exists too — a README that points at
   a file nobody has is worse than one that says nothing.

The README is organised as three parts (``Part I`` project, ``Part II``
Telegram, ``Part III`` QQ / OneBot) plus an appendix. The numbered subsections
run continuously across the parts (1..14, 15..23, 24..35), so the checks locate
a part by its heading rather than by a hard-coded section number.

Run: python scripts/verify_readme.py
Exit code is non-zero if any check fails.
"""
from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

README = ROOT / "README.md"

failures: list[str] = []
notes: list[str] = []


def fail(message: str) -> None:
    failures.append(message)


def ok(message: str) -> None:
    notes.append(message)


def unquote(block: str) -> str:
    """Strip markdown blockquote markers.

    Several README examples live inside ``> ```yaml`` callouts (warnings and
    "this will fail" illustrations). The raw capture keeps the ``> `` prefix,
    which is not YAML, so it has to come off before anything can be parsed.
    """
    return "\n".join(re.sub(r"^>\s?", "", line) for line in block.splitlines()) + "\n"


def slugify(heading: str) -> str:
    """GitHub's anchor algorithm, near enough for these headings."""
    text = heading.strip().lower()
    text = re.sub(r"[^\w\u4e00-\u9fff\- ]", "", text)
    return text.replace(" ", "-")


# ---------------------------------------------------------------- 1. yaml
def bad_example_block(text: str) -> str:
    """The deliberately-invalid example, as a normalised string."""
    index = text.find("常见错误示范")
    if index == -1:
        return ""
    match = re.search(r"```yaml\n(.*?)```", text[index:], re.DOTALL)
    return unquote(match.group(1)).strip() if match else ""


def check_yaml_blocks(text: str) -> None:
    import yaml

    from config.loader import load_config
    from domain.errors import ConfigurationError

    blocks = re.findall(r"```yaml\n(.*?)```", text, re.DOTALL)
    if not blocks:
        fail("no yaml blocks found — did the README lose its examples?")

    # The "常见错误示范" block is *supposed* to be rejected; it is checked
    # separately. Skipping it here is what keeps this check meaningful instead
    # of a blanket "ignore failures" that would hide real ones.
    skip = bad_example_block(text)

    full_configs = 0
    for index, raw_block in enumerate(blocks, start=1):
        block = unquote(raw_block)
        if skip and block.strip() == skip:
            continue
        try:
            data = yaml.safe_load(block)
        except yaml.YAMLError as exc:
            fail(f"yaml block #{index} does not parse: {exc}")
            continue

        # A block that declares `provider` is a complete config and must load.
        if isinstance(data, dict) and "provider" in data:
            full_configs += 1
            path = Path(tempfile.mkdtemp()) / "config.yaml"
            path.write_text(block, encoding="utf-8")
            try:
                load_config(path)
            except ConfigurationError as exc:
                fail(f"yaml block #{index} is a full config but the loader rejects it: {exc}")
            except Exception as exc:  # noqa: BLE001
                fail(f"yaml block #{index} raised {type(exc).__name__}: {exc}")
    ok(f"{len(blocks)} yaml blocks parse; {full_configs} full configs load through load_config")


def check_the_bad_example_is_really_bad(text: str) -> None:
    """The README warns that a token in config.yaml fails. Prove it."""
    from config.loader import load_config
    from domain.errors import ConfigurationError

    marker = "常见错误示范"
    index = text.find(marker)
    if index == -1:
        fail("README no longer contains the '常见错误示范' warning")
        return
    after = text[index:]
    block = re.search(r"```yaml\n(.*?)```", after, re.DOTALL)
    if not block:
        fail("the '常见错误示范' warning has no yaml block")
        return

    path = Path(tempfile.mkdtemp()) / "config.yaml"
    path.write_text(unquote(block.group(1)), encoding="utf-8")
    try:
        load_config(path)
    except ConfigurationError as exc:
        if "bot_token" not in str(exc) and "extra" not in str(exc):
            fail(f"the '常见错误示范' block fails for the wrong reason: {exc}")
        else:
            ok("the 'this will fail' example really is rejected, and names bot_token")
    else:
        fail("the README says the '常见错误示范' block fails, but the loader accepted it")


# ------------------------------------------------- 2. support status table
IMPLEMENTED = {
    "outputs/telegram.py": "Telegram 推送",
    "outputs/qq.py": "QQ（OneBot）推送",
    "outputs/factory.py": "Telegram / QQ 推送",
    "storage/jsonl.py": "投递状态记录（防止重复发送）",
    "providers/youtube.py": "抓取 YouTube 频道",
    "parsers/youtube.py": "抓取 YouTube 频道",
    "normalizers/youtube.py": "抓取 YouTube 频道",
}
NOT_IMPLEMENTED = {
    "outputs/rss.py": "生成 RSS 输出文件",
    "summarizers": "AI 摘要 / 翻译",
}


def check_support_table(text: str) -> None:
    if "✅ **已实现**" not in text or "⛔ **未实现**" not in text:
        fail("the support-status table markers are missing")

    for path, label in IMPLEMENTED.items():
        if not (ROOT / path).exists():
            fail(f"README marks '{label}' as implemented but {path} does not exist")
    ok("implemented rows have code on disk")

    for path, label in NOT_IMPLEMENTED.items():
        if (ROOT / path).exists():
            fail(f"README marks '{label}' as not implemented but {path} exists")
    ok("not-implemented rows have no code on disk")

    # The whole point of the table: no Telegram/QQ leakage into the model.
    #
    # Checked with AST rather than by substring: the module docstring *does*
    # mention Telegram and Markdown — it explains why they are deliberately not
    # fields. A text search cannot tell an explanation from a declaration, so it
    # would flag the very comment that documents the rule.
    import ast

    tree = ast.parse((ROOT / "domain/models/item.py").read_text(encoding="utf-8"))
    banned_fields = {
        "telegram_text", "telegram_html", "telegram_message_id", "telegram_chat_id",
        "markdown", "rendered_text", "qq_text", "delivered", "delivery_status",
    }
    target = next(
        (n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "NormalizedItem"),
        None,
    )
    if target is None:
        fail("NormalizedItem class not found in domain/models/item.py")
    else:
        declared = {
            stmt.target.id
            for stmt in target.body
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
        }
        leaked = sorted(declared & banned_fields)
        if leaked:
            fail(f"NormalizedItem declares output-specific field(s): {leaked}")
        else:
            ok(f"NormalizedItem declares no output-specific field ({len(declared)} fields checked)")


def check_outputs_are_really_wired(text: str) -> None:
    """The README claims 6 steps end in a working Telegram push, and that QQ
    works over OneBot. Check the wiring for both."""
    schema = (ROOT / "config/schema.py").read_text(encoding="utf-8")

    if "class TelegramConfig" not in schema:
        fail("README documents telegram: config but TelegramConfig does not exist")
    for field in ("enabled", "bot_token_env", "chat_id_env"):
        if field not in schema:
            fail(f"README documents telegram.{field} but the schema lacks it")

    if "class QQConfig" not in schema:
        fail("README documents qq: config but QQConfig does not exist")
    for field in ("enabled", "api_base", "group_id", "access_token_env", "timeout"):
        if field not in schema:
            fail(f"README documents qq.{field} but the schema lacks it")

    if "class YoutubeChannelConfig" not in schema:
        fail("README documents youtube_channels: but YoutubeChannelConfig does not exist")
    for field in ("channel_id", "url", "enabled"):
        if field not in schema:
            fail(f"README documents youtube channel field '{field}' but the schema lacks it")

    workflow = (ROOT / ".github/workflows/update.yml").read_text(encoding="utf-8")
    for secret in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "ONEBOT_ACCESS_TOKEN"):
        if secret not in workflow:
            fail(f"README says to add secret {secret} but the workflow never reads it")
    if "secrets.TELEGRAM_BOT_TOKEN" in workflow and "env:" not in workflow:
        fail("workflow interpolates a secret without mapping it to env")
    ok("telegram + qq config and workflow secret wiring are all present")


# ------------------------------------------------------- 3. report format
def check_report_sample(text: str) -> None:
    """The README shows a report. Regenerate it and require an exact match.

    A loose "does render_report contain these words" check cannot catch a stale
    sample — which is exactly the drift that matters, because the sample is what
    an operator compares their real output against.
    """
    from app.runner import AccountOutcome, DeliveryOutcome, RunSummary, STATUS_OK
    from main import render_report

    summary = RunSummary(run_id="20260928T171000Z-abcd1234", started_at="x", duration_ms=1234)
    summary.outcomes.append(
        AccountOutcome(
            account="mimoriaino", status=STATUS_OK, kind="x",
            provider="nitter", route="rss", fetched=4, normalized=4, inserted=4,
        )
    )
    summary.outcomes.append(
        AccountOutcome(
            account="my-site", status=STATUS_OK, kind="rss",
            provider="rss", route="feed", fetched=24, normalized=24, inserted=23,
            duplicates=1,
        )
    )
    summary.outcomes.append(
        AccountOutcome(
            account="UCabcdefg", status=STATUS_OK, kind="youtube",
            provider="youtube", route="feed", fetched=3, normalized=3, inserted=3,
        )
    )
    summary.deliveries.append(DeliveryOutcome(output="telegram", pending=30, delivered=30))
    summary.deliveries.append(DeliveryOutcome(output="qq", pending=30, delivered=30))
    expected = render_report(summary).strip("\n")

    marker = "报告长这样"
    index = text.find(marker)
    if index == -1:
        fail("README no longer contains the sample run report ('报告长这样')")
        return
    match = re.search(r"```\n(.*?)```", text[index:], re.DOTALL)
    if not match:
        fail("the sample run report has no fenced block")
        return

    actual = match.group(1).strip("\n")
    if actual != expected:
        fail(
            "the README's sample run report no longer matches render_report:\n"
            f"--- README ---\n{actual}\n--- render_report ---\n{expected}"
        )
    else:
        ok("the README's sample run report is byte-identical to render_report's output")

    for expected_token in ("units:", "items:", "output: telegram", "output: qq", "youtube"):
        if expected_token not in expected:
            fail(f"the sample report lost '{expected_token}'")


# ------------------------------------------------------------- 4. TOC links
def check_toc(text: str) -> None:
    """Every link target must be a heading that actually exists.

    Levels 1..4 are collected: the README links to parts (h2), numbered
    subsections (h3) and a few sub-subsections (h4), and a link to any of them
    has to resolve.
    """
    headings = re.findall(r"^#{1,4} (.+)$", text, re.MULTILINE)
    anchors = {slugify(h) for h in headings}

    links = re.findall(r"\]\(#([^)]+)\)", text)
    if not links:
        fail("the README has no table of contents links")

    missing = sorted({link for link in links if link not in anchors})
    if missing:
        fail(f"TOC links with no matching heading: {missing}")
    ok(f"{len(links)} TOC links all resolve to a heading")


def check_numbered_sections(text: str) -> None:
    """The numbered subsections run 1..N with no gaps or repeats.

    The pattern requires ``### N. `` followed by whitespace, so a nested heading
    like ``### 6.1 ...`` is not mistaken for subsection 6.
    """
    numbers = [int(m) for m in re.findall(r"^### (\d+)\.\s", text, re.MULTILINE)]
    if not numbers:
        fail("no numbered subsections found — did the README lose its structure?")
        return
    if numbers != list(range(1, len(numbers) + 1)):
        fail(f"subsection numbering is not contiguous: {numbers}")
    else:
        ok(f"{len(numbers)} subsections numbered 1..{len(numbers)} contiguously")


# ------------------------------------------------------------------ 5. QQ
PART_I = "# Part I — X-Reader 项目"
PART_II = "# Part II — Telegram 接入实操"
PART_III = "# Part III — QQ / OneBot 接入实操"
APPENDIX = "# 附录"


def part_of(text: str, start_marker: str, end_marker: str) -> str:
    """The slice of the README between two top-level headings."""
    start = text.find(start_marker)
    if start == -1:
        return ""
    end = text.find(end_marker, start + len(start_marker))
    return text[start:end] if end != -1 else text[start:]


def matrix_row(text: str, label: str) -> str:
    """The support-matrix row whose first cell mentions ``label``."""
    for line in text.splitlines():
        if line.startswith("|") and label in line:
            return line
    return ""


def check_qq_section(text: str) -> None:
    """QQ is implemented — the README must say so, and say how."""
    if not (ROOT / "outputs/qq.py").exists():
        fail("the README documents QQ output but outputs/qq.py does not exist")

    if "✅ **已实现**" not in matrix_row(text, "推送到 QQ"):
        fail("the support matrix does not mark '推送到 QQ（OneBot）' as implemented")

    section = part_of(text, PART_III, APPENDIX)
    if not section:
        fail("the README has no 'Part III — QQ / OneBot 接入实操' part")
        return

    if "未实现" in section:
        fail("the QQ part still claims QQ is not implemented")

    subsections = [int(m) for m in re.findall(r"^### (\d+)\.\s", section, re.MULTILINE)]
    if subsections != list(range(24, 36)):
        fail(f"the QQ part should have subsections 24..35, found {subsections}")
    else:
        ok("the QQ part has subsections 24..35")

    # The two facts that decide success or failure on the wire.
    absent = [token for token in ("/send_group_msg", "retcode") if token not in section]
    if absent:
        fail(f"the QQ part never mentions {absent}, which is how success is decided")
    else:
        ok("the QQ part states the OneBot action and that retcode == 0 decides success")

    # A usable config block must actually load through the real loader.
    qq_blocks = [
        block for block in re.findall(r"```yaml\n(.*?)```", text, re.DOTALL)
        if re.search(r"^\s*qq:", unquote(block), re.MULTILINE)
    ]
    if not qq_blocks:
        fail("the README documents QQ but shows no `qq:` config block")
    else:
        from config.loader import load_config

        for index, block in enumerate(qq_blocks, start=1):
            if "provider" in block:
                continue  # a full config; already validated in check_yaml_blocks
            # The QQ block alone is a fragment, so give it the minimum context
            # that makes it a complete, loadable configuration.
            body = unquote(block)
            if re.search(r"^\s*qq:", body, re.MULTILINE) and "provider" not in body:
                path = Path(tempfile.mkdtemp()) / "config.yaml"
                path.write_text(
                    "provider:\n"
                    "  order: [nitter]\n"
                    "  nitter:\n"
                    "    endpoints: [https://nitter.poast.org]\n" + body,
                    encoding="utf-8",
                )
                try:
                    load_config(path)
                except Exception as exc:  # noqa: BLE001
                    fail(f"README's qq config block #{index} is rejected by the loader: {exc}")
    ok("the README documents QQ as implemented, with a loadable `qq:` block")


def check_youtube_section(text: str) -> None:
    """YouTube input is implemented — the README must describe it truthfully."""
    for path in ("providers/youtube.py", "parsers/youtube.py", "normalizers/youtube.py"):
        if not (ROOT / path).exists():
            fail(f"the README documents YouTube but {path} does not exist")

    if "✅ **已实现**" not in matrix_row(text, "抓取 YouTube"):
        fail("the support matrix does not mark '抓取 YouTube 频道' as implemented")

    part_one = part_of(text, PART_I, PART_II)
    if not part_one:
        fail("the README has no 'Part I — X-Reader 项目' part")
        return

    # The two claims that matter most for a non-technical reader.
    if "不需要 YouTube Data API" not in part_one:
        fail("the README no longer states that no YouTube Data API key is needed")
    if "channel_id" not in part_one:
        fail("the README does not explain channel_id")
    ok("the YouTube input section states no Data API key is needed and explains channel_id")


# ------------------------------------------------------- 6. paths and tree
#: Directory prefixes whose backticked paths must exist. ``data/`` is excluded
#: on purpose: it is runtime output, created by a run, so the README describes
#: its shape without the directories existing in a fresh checkout.
PATH_PREFIXES = (
    "app/", "config/", "domain/", "infrastructure/", "normalizers/", "outputs/",
    "parsers/", "providers/", "scripts/", "storage/", "tests/", "docs/",
)
ROOT_FILES = ("main.py", "accounts.yaml", "requirements.txt", "pyproject.toml", "README.md")


def check_paths_exist(text: str) -> None:
    """Every repo path the README names in backticks must exist."""
    checked = 0
    for token in re.findall(r"`([^`\n]+)`", text):
        if any(char in token for char in "<>*$ "):
            continue
        candidate = token[2:] if token.startswith("./") else token
        if not (candidate.startswith(PATH_PREFIXES) or candidate in ROOT_FILES):
            continue
        checked += 1
        if not (ROOT / candidate).exists():
            fail(f"the README names `{candidate}`, which does not exist")
    ok(f"{checked} repo paths named in the README all exist")


def check_tree_matches_filesystem(text: str) -> None:
    """Every file named in the documented directory tree exists somewhere."""
    marker = "目录结构"
    index = text.find(marker)
    if index == -1:
        fail("README no longer contains the directory tree ('目录结构')")
        return
    block = re.search(r"```\n(X-Reader/.*?)```", text[index:], re.DOTALL)
    if not block:
        fail("the directory tree has no fenced block starting with 'X-Reader/'")
        return

    named = set(re.findall(r"([A-Za-z0-9_.-]+\.(?:py|yaml|yml|md|txt|toml))", block.group(1)))
    real = {
        path.name
        for path in ROOT.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }
    missing = sorted(named - real)
    if missing:
        fail(f"the directory tree names files that do not exist: {missing}")
    else:
        ok(f"the directory tree's {len(named)} file names all exist in the repo")


def check_no_secrets_in_readme(text: str) -> None:
    """Every token-looking string in the README must be obviously a placeholder."""
    for match in re.finditer(r"\b(\d{6,}:[A-Za-z0-9_-]{20,})\b", text):
        token = match.group(1)
        if "Example" not in token and "AA" not in token:
            fail(f"README contains something that looks like a real bot token: {token[:12]}...")
    ok("no real-looking bot token in the README")


def main() -> int:
    text = README.read_text(encoding="utf-8")

    check_yaml_blocks(text)
    check_the_bad_example_is_really_bad(text)
    check_support_table(text)
    check_outputs_are_really_wired(text)
    check_report_sample(text)
    check_toc(text)
    check_numbered_sections(text)
    check_qq_section(text)
    check_youtube_section(text)
    check_paths_exist(text)
    check_tree_matches_filesystem(text)
    check_no_secrets_in_readme(text)

    for note in notes:
        print(f"  ok   {note}")
    for problem in failures:
        print(f"  FAIL {problem}")

    print()
    if failures:
        print(f"README verification FAILED ({len(failures)} problem(s))")
        return 1
    print("README verification passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
