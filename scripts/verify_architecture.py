"""Phase 4 architecture regression gate (task section 34).

Checks the things a passing test suite cannot: that the *shape* of the code still
respects the freeze. These are the assertions that would silently rot — nothing
fails if a provider starts importing Telegram, but the design would be dead.

Checks
------
1. no Telegram/QQ/output import anywhere in providers / parsers / normalizers /
   storage / domain / infrastructure;
2. no output-specific field declared on ``NormalizedItem`` — including the
   Phase 4 temptations (``video_id``, ``channel_id``, ``thumbnail``, …);
3. no Telegram/OneBot API detail in ``app/runner.py`` (orchestration only);
4. the Runner never imports a concrete adapter — only the interface;
5. no SDK dependency was added for Telegram or QQ (``httpx`` only);
6. no business logic in the workflow file: no API call, no token interpolation
   into a shell command;
7. no secret literal anywhere in tracked source or config;
8. the Phase 4 features are wired through the frozen seams, not around them:
   YouTube parses to a raw record and normalizes to ``NormalizedItem``; QQ is an
   ``OutputAdapter``; Telegram and QQ do not import each other;
9. the forbidden features genuinely do not exist (RSS output, summarizer/LLM).

Run: python scripts/verify_architecture.py
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

failures: list[str] = []
notes: list[str] = []

#: Packages that must never learn that an output exists.
SEALED_PACKAGES = ("providers", "parsers", "normalizers", "storage", "domain", "infrastructure")

#: Words that indicate output-layer knowledge. Applied to import lines only —
#: module docstrings deliberately *name* the things they forbid, so a raw text
#: scan would flag the comment that documents the rule.
#:
#: ``deliver`` is deliberately absent: ``storage/`` legitimately owns the
#: delivery-state *records* (``DELIVERY_SENT``, ``DeliveryRecord``), and those
#: names would otherwise be flagged as output-layer knowledge.
OUTPUT_WORDS = ("telegram", "onebot", "qq", "outputs")

BANNED_ITEM_FIELDS = {
    # Phase 3 temptations.
    "telegram_text", "telegram_html", "telegram_message_id", "telegram_chat_id",
    "markdown", "rendered_text", "qq_text", "delivery_status",
    # Phase 4 temptations: a rendered message, a wire payload, or a video field
    # that belongs in ``media`` / ``metadata``.
    "qq_message", "onebot_payload", "telegram_message",
    "youtube_embed", "video_id", "channel_id", "thumbnail", "duration",
    "view_count", "video_url",
}


def fail(message: str) -> None:
    failures.append(message)


def ok(message: str) -> None:
    notes.append(message)


def python_files(*packages: str) -> list[Path]:
    files: list[Path] = []
    for package in packages:
        directory = ROOT / package
        if directory.exists():
            files.extend(sorted(directory.rglob("*.py")))
    return files


def strip_docstrings(tree: ast.AST) -> ast.AST:
    """Remove docstrings so a comment explaining a rule is not read as code.

    The module docstrings in this project deliberately name the things they
    forbid ("adding a ``telegram_text`` field would..."). A naive text scan
    flags exactly those sentences, which is how an audit produces 30 phantom
    findings and then stops being read.
    """
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                node.body = body[1:] or [ast.Pass()]
    return tree


def code_only(path: Path) -> str:
    """Source with docstrings removed, as text."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        fail(f"{path.relative_to(ROOT)} does not parse: {exc}")
        return ""
    return ast.unparse(strip_docstrings(tree))


# ---------------------------------------------------------------- 1. imports
def check_sealed_packages() -> None:
    offenders: list[str] = []
    for path in python_files(*SEALED_PACKAGES):
        source = code_only(path)
        for line in source.splitlines():
            if not re.match(r"\s*(import|from)\s", line):
                continue
            lowered = line.lower()
            for word in OUTPUT_WORDS:
                if word in lowered:
                    offenders.append(f"{path.relative_to(ROOT)}: {line.strip()}")
                    break
    if offenders:
        fail(f"output-layer import inside a sealed package: {offenders}")
    else:
        ok(f"no output imports in {', '.join(SEALED_PACKAGES)}")


# ----------------------------------------------------------------- 2. model
def check_normalized_item_fields() -> None:
    path = ROOT / "domain/models/item.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    target = next(
        (n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "NormalizedItem"),
        None,
    )
    if target is None:
        fail("NormalizedItem not found")
        return
    declared = {
        stmt.target.id
        for stmt in target.body
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
    }
    leaked = sorted(declared & BANNED_ITEM_FIELDS)
    if leaked:
        fail(f"NormalizedItem declares banned field(s): {leaked}")
    else:
        ok(f"NormalizedItem has {len(declared)} fields, none output-specific")


# ---------------------------------------------------------------- 3. runner
def check_runner_is_orchestration_only() -> None:
    path = ROOT / "app/runner.py"
    source = code_only(path)

    banned = (
        # Telegram detail
        "api.telegram.org", "sendMessage", "send_message", "parse_mode", "4096",
        # OneBot detail (Phase 4)
        "/send_group_msg", "send_group_msg", "retcode", "group_id", "onebot",
    )
    for needle in banned:
        if needle in source:
            fail(f"app/runner.py contains protocol-specific detail: '{needle}'")

    if re.search(r"\bhttpx\b", source):
        fail("app/runner.py imports or uses httpx — the adapter owns the transport")

    for concrete in ("outputs.telegram", "outputs.qq"):
        if concrete in source:
            fail(f"app/runner.py imports {concrete} instead of the interface")
            break
    else:
        if "from outputs.base import" not in source:
            fail("app/runner.py does not import OutputAdapter from outputs.base")
        else:
            ok("app/runner.py orchestrates only: no Telegram/OneBot detail, no HTTP, interface import")


def check_runner_orders_delivery_after_storage() -> None:
    """Delivery must come after *every* unit loop, not inside one."""
    source = (ROOT / "app/runner.py").read_text(encoding="utf-8")
    run_body = source[source.index("def run(self)"):]
    if "_deliver()" not in run_body:
        fail("Runner.run() never calls _deliver()")
        return

    loops = (
        "for account in self.accounts",
        "for source in self.rss_sources",
        "for channel in self.youtube_channels",
    )
    missing = [loop for loop in loops if loop not in run_body]
    if missing:
        fail(f"Runner.run() lost a unit loop: {missing}")
        return

    last_loop = max(run_body.index(loop) for loop in loops)
    if run_body.index("_deliver()") < last_loop:
        fail("Runner.run() delivers before finishing acquisition")
    else:
        ok("Runner delivers once, after every unit loop (X, RSS, YouTube) has finished")


# ------------------------------------------------------------ 4. dependencies
def check_no_sdk_added() -> None:
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    for banned in ("telegram", "python-telegram-bot", "aiogram", "telebot", "onebot", "nonebot"):
        if banned in requirements:
            fail(f"requirements.txt declares '{banned}' — the Bot API is called over httpx")
    if "httpx" not in requirements:
        fail("requirements.txt no longer declares httpx")
    else:
        ok("no notification SDK added; httpx is the only HTTP dependency")


# -------------------------------------------------------------- 5. workflow
def strip_yaml_comments(text: str) -> str:
    """Drop comment lines, keeping line count so reported numbers stay right.

    The workflow *documents* the rule it follows ("`${{ secrets.X }}` in a
    ``run:`` body would end up in the log"). Scanning raw text flags that
    sentence as a violation — a false positive that teaches the reader to
    ignore the gate.
    """
    return "\n".join(
        "" if line.lstrip().startswith("#") else line for line in text.splitlines()
    )


def check_workflow() -> None:
    raw = (ROOT / ".github/workflows/update.yml").read_text(encoding="utf-8")
    workflow = strip_yaml_comments(raw)

    # The invariant: a secret may only be *assigned to an env key*, never
    # interpolated into a shell body — the latter lands in the job log.
    for match in re.finditer(r"\$\{\{\s*secrets\.([A-Za-z_]+)\s*\}\}", workflow):
        name = match.group(1)
        line_no = workflow[: match.start()].count("\n") + 1
        line = workflow.splitlines()[line_no - 1]
        if not re.match(r"^\s+[A-Za-z_][A-Za-z0-9_]*:\s*\$\{\{\s*secrets\.", line):
            fail(
                f"secret {name} is not an `env:` assignment (line {line_no}) — "
                "interpolating a secret into a command leaks it into the log"
            )
    if re.search(r"secrets\.", workflow) and "env:" not in workflow:
        fail("the workflow reads secrets without an env: block")

    if "api.telegram.org" in workflow:
        fail("the workflow contains a Telegram API call — business logic must stay in Python")

    for required in ("cron:", "concurrency:", "GITHUB_STEP_SUMMARY", "actions/upload-artifact"):
        if required not in workflow:
            fail(f"the workflow lost a required element: {required}")

    # Phase 4 added a third input; the dispatch surface must expose it.
    if "no_youtube" not in workflow:
        fail("the workflow no longer exposes the no_youtube dispatch input")

    if workflow.index("Run tests") > workflow.index("Fetch, store and deliver"):
        fail("the workflow no longer runs tests before writing data")
    else:
        ok("workflow: secrets via env, no API calls, tests before writes, schedule/concurrency kept")


# ---------------------------------------------------------------- 6. secrets
TOKEN_SHAPED = re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{30,}\b")


def check_no_secret_literals() -> None:
    """A real bot token has a very specific shape; nothing should look like one."""
    offenders: list[str] = []
    for path in list(ROOT.rglob("*.py")) + list(ROOT.rglob("*.yaml")) + list(ROOT.rglob("*.yml")):
        if ".venv" in path.parts or "__pycache__" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for match in TOKEN_SHAPED.finditer(text):
            token = match.group(0)
            # Test placeholders are obviously not real.
            if any(marker in token for marker in ("Example", "fake", "test", "AAAA")):
                continue
            offenders.append(f"{path.relative_to(ROOT)}: {token[:12]}...")
    if offenders:
        fail(f"something shaped like a real bot token is in the repo: {offenders}")
    else:
        ok("no bot-token-shaped literal anywhere in source or config")


def check_data_dir_has_no_secrets() -> None:
    data = ROOT / "data"
    if not data.exists():
        ok("no data/ directory in the repo (nothing to leak)")
        return
    hits: list[str] = []
    for path in data.rglob("*"):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if TOKEN_SHAPED.search(text) or "TELEGRAM_BOT_TOKEN=" in text:
            hits.append(str(path.relative_to(ROOT)))
    if hits:
        fail(f"a credential may have been written into data/: {hits}")
    else:
        ok("data/ contains no credential")


# ------------------------------------------------------- 7. absent features
def check_forbidden_features_absent() -> None:
    """What is still *not* built, and must not be built in this phase.

    Phase 4 built YouTube input and QQ output, so both left this list. What
    remains are the features the freeze has never approved: an RSS output
    renderer, an AI summarizer, and anything that would make X-Reader a web
    service or a queue.
    """
    forbidden = {
        "outputs/rss.py": "RSS output adapter",
        "outputs/feed.py": "feed generator",
        "outputs/onebot.py": "a second OneBot adapter (QQ lives in outputs/qq.py)",
        "summarizers": "AI summarizer package",
        "app/output_manager.py": "an output-manager layer above the adapters",
        "app/source_manager.py": "a source-manager layer above the providers",
    }
    present = [f"{p} ({why})" for p, why in forbidden.items() if (ROOT / p).exists()]
    if present:
        fail(f"Phase 4 built something it was told not to: {present}")
    else:
        ok("RSS output / feed generator / summarizer / manager layers all absent")

    # No LLM client anywhere.
    llm_markers = ("openai", "anthropic", "litellm", "langchain")
    hits: list[str] = []
    for path in python_files("providers", "parsers", "normalizers", "outputs", "app", "domain"):
        source = code_only(path)
        for marker in llm_markers:
            if marker in source.lower():
                hits.append(f"{path.relative_to(ROOT)}: {marker}")
    if hits:
        fail(f"an LLM client appears in the codebase: {hits}")
    else:
        ok("no LLM client or summarizer anywhere")


# --------------------------------------------- 8. Phase 4 wired through seams
def _imports(path: Path) -> set[str]:
    """Module names imported by ``path``, as written."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _bases(path: Path, class_name: str) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return [ast.unparse(base) for base in node.bases]
    return []


def check_phase4_is_wired_through_the_seams() -> None:
    """YouTube must normalize to NormalizedItem; QQ must be an OutputAdapter.

    These are the two shortcuts Phase 4 could have taken and did not: a YouTube
    path that writes its own row shape, and a QQ adapter that bypasses the
    delivery contract. Both would pass every functional test and still break the
    freeze, so they are asserted structurally.
    """
    problems: list[str] = []

    # -- YouTube: raw record in, NormalizedItem out, no storage/output contact.
    for module in ("parsers/youtube.py", "providers/youtube.py"):
        path = ROOT / module
        if not path.exists():
            problems.append(f"{module} is missing")
            continue
        imported = _imports(path)
        for banned in ("storage", "outputs", "app.runner", "app.registry"):
            if any(name == banned or name.startswith(banned + ".") for name in imported):
                problems.append(f"{module} imports {banned}")

    normalizer = ROOT / "normalizers/youtube.py"
    if not normalizer.exists():
        problems.append("normalizers/youtube.py is missing")
    elif "NormalizedItem" not in normalizer.read_text(encoding="utf-8"):
        problems.append("normalizers/youtube.py never mentions NormalizedItem")

    # -- QQ: a real OutputAdapter, independent of Telegram.
    qq = ROOT / "outputs/qq.py"
    if not qq.exists():
        problems.append("outputs/qq.py is missing")
    else:
        if "OutputAdapter" not in _bases(qq, "QQOutput"):
            problems.append("QQOutput does not subclass OutputAdapter")
        if any("telegram" in name for name in _imports(qq)):
            problems.append("outputs/qq.py imports Telegram")

    telegram = ROOT / "outputs/telegram.py"
    if telegram.exists() and any("qq" in name or "onebot" in name for name in _imports(telegram)):
        problems.append("outputs/telegram.py imports QQ/OneBot")

    # -- The factory is still the single wiring point, and knows both.
    factory = (ROOT / "outputs/factory.py").read_text(encoding="utf-8")
    for expected in ("TelegramOutput", "QQOutput"):
        if expected not in factory:
            problems.append(f"outputs/factory.py no longer builds {expected}")

    if problems:
        fail(f"Phase 4 bypassed a frozen seam: {problems}")
    else:
        ok(
            "YouTube → NormalizedItem and QQ → OutputAdapter both hold; "
            "Telegram and QQ do not import each other"
        )


def main() -> int:
    check_sealed_packages()
    check_normalized_item_fields()
    check_runner_is_orchestration_only()
    check_runner_orders_delivery_after_storage()
    check_no_sdk_added()
    check_workflow()
    check_no_secret_literals()
    check_data_dir_has_no_secrets()
    check_forbidden_features_absent()
    check_phase4_is_wired_through_the_seams()

    for note in notes:
        print(f"  ok   {note}")
    for problem in failures:
        print(f"  FAIL {problem}")

    print()
    if failures:
        print(f"ARCHITECTURE GATE FAILED ({len(failures)} problem(s))")
        return 1
    print("ARCHITECTURE GATE PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
