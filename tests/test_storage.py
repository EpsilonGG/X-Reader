"""Storage tests.

The freeze names storage's required properties explicitly: persistence,
identity deduplication, incremental saving, no duplicates on a repeat run, raw
data kept, and re-parseability later. Each of those has a test here.

Since Phase 2 there are **two** interfaces and the tests are split to match:

* :class:`~storage.base.BaseStorage` — the canonical store, which knows exactly
  one model, ``NormalizedItem``. Its tests never mention a tweet.
* :class:`~storage.base.RawRecordArchive` — the Phase 1 raw-record archive, kept
  as re-processing evidence. Its tests never mention ``NormalizedItem``.

Keeping the two apart in the tests is what stops a raw shape leaking back into
the canonical path.
"""
from __future__ import annotations

import json

import pytest

from config.schema import StorageConfig
from domain.errors import StorageError
from domain.models.account import Account
from domain.models.item import PRECISION_SECOND
from domain.models.item import SOURCE_X
from domain.models.item import MediaItem
from domain.models.item import NormalizedItem
from domain.models.tweet import RawTweet
from storage.base import DELIVERY_FAILED
from storage.base import DELIVERY_SENT
from storage.base import RunRecord
from storage.jsonl import JsonlStorage


def make_item(
    item_id: str,
    *,
    source_id: str = SOURCE_X,
    content: str = "hello",
    fetched_at: str = "2024-09-11T19:31:00+00:00",
) -> NormalizedItem:
    return NormalizedItem(
        source_id=source_id,
        item_id=item_id,
        content=content,
        publisher="jack",
        canonical_url=f"https://x.com/jack/status/{item_id}",
        published_at="2024-09-11T19:31:00+00:00",
        published_at_raw="Sep 11, 2024 · 7:31 PM UTC",
        published_precision=PRECISION_SECOND,
        fetched_at=fetched_at,
    )


def make_tweet(tweet_id: str, *, account: str = "jack", text: str = "hello") -> RawTweet:
    return RawTweet(
        tweet_id=tweet_id,
        account=account,
        url=f"https://x.com/{account}/status/{tweet_id}",
        text=text,
        created_at="2024-09-11T19:31:00+00:00",
        created_at_raw="Sep 11, 2024 · 7:31 PM UTC",
        author_username=account,
        author_name=account,
        provider="nitter",
        route="html",
        fetched_at="2024-09-11T19:31:00+00:00",
        raw={"source": "nitter_html", "text": text},
    )


@pytest.fixture
def storage(tmp_path) -> JsonlStorage:
    return JsonlStorage(data_dir=tmp_path / "data")


@pytest.fixture
def account_obj() -> Account:
    return Account(username="jack")


# --- canonical store: persistence -----------------------------------------
def test_save_writes_one_json_line_per_item(storage):
    report = storage.save([make_item("1"), make_item("2")])

    assert (report.received, report.inserted, report.duplicates) == (2, 2, 0)
    path = storage.item_path(SOURCE_X)
    assert path.exists()

    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line]
    assert len(lines) == 2
    for line in lines:
        assert isinstance(json.loads(line), dict)


def test_saved_record_carries_every_contract_field(storage):
    storage.save([make_item("42")])
    record = json.loads(storage.item_path(SOURCE_X).read_text(encoding="utf-8").strip())
    for field in ("identity_key", "source_id", "item_id", "title", "content",
                  "publisher", "canonical_url", "published_at", "published_at_raw",
                  "published_precision", "fetched_at", "media", "metadata"):
        assert field in record, f"missing field {field}"
    assert record["item_id"] == "42"
    assert record["identity_key"] == "x:42"


def test_stored_identity_key_agrees_with_its_components(storage):
    """A stored copy could drift; the property cannot. Both must agree anyway."""
    storage.save([make_item("7")])
    record = json.loads(storage.item_path(SOURCE_X).read_text(encoding="utf-8").strip())
    assert record["identity_key"] == f"{record['source_id']}:{record['item_id']}"


def test_metadata_is_preserved_verbatim(storage):
    """Re-parseability later depends on this."""
    item = make_item("7", content="original body")
    item.metadata["raw_source"] = "nitter_html"
    storage.save([item])
    record = json.loads(storage.item_path(SOURCE_X).read_text(encoding="utf-8").strip())
    assert record["metadata"] == {"raw_source": "nitter_html"}


def test_save_creates_missing_directories(storage):
    assert not storage.items_dir.exists()
    storage.save([make_item("1")])
    assert storage.items_dir.exists()


def test_stored_line_reloads_into_an_equal_item(storage):
    """The round trip is what makes a stored item re-processable."""
    original = make_item("42", content="round trip")
    original.media = [MediaItem(url="https://img.example/a.jpg", type="image")]
    original.metadata = {"lang": "ja"}
    storage.save([original])

    record = json.loads(storage.item_path(SOURCE_X).read_text(encoding="utf-8").strip())
    reloaded = NormalizedItem.from_dict(record)
    assert reloaded.to_dict() == original.to_dict()
    assert reloaded.identity_key == original.identity_key


# --- canonical store: deduplication ---------------------------------------
def test_second_identical_save_inserts_nothing(storage):
    first = storage.save([make_item("1"), make_item("2")])
    second = storage.save([make_item("1"), make_item("2")])

    assert first.inserted == 2
    assert second.inserted == 0
    assert second.duplicates == 2
    # The file must not have grown.
    lines = storage.item_path(SOURCE_X).read_text(encoding="utf-8").splitlines()
    assert len([line for line in lines if line]) == 2


def test_partial_overlap_only_appends_the_new_records(storage):
    storage.save([make_item("1"), make_item("2")])
    report = storage.save([make_item("2"), make_item("3")])

    assert (report.inserted, report.duplicates) == (1, 1)
    ids = [json.loads(line)["item_id"] for line in
           storage.item_path(SOURCE_X).read_text(encoding="utf-8").splitlines() if line]
    assert ids == ["1", "2", "3"]


def test_duplicate_ids_within_one_batch_are_inserted_once(storage):
    report = storage.save([make_item("1"), make_item("1")])
    assert (report.inserted, report.duplicates) == (1, 1)


def test_existing_records_are_never_rewritten(storage):
    """First write wins: records stay immutable, so appends stay appends."""
    storage.save([make_item("1", content="original")])
    storage.save([make_item("1", content="revised")])
    record = json.loads(storage.item_path(SOURCE_X).read_text(encoding="utf-8").strip())
    assert record["content"] == "original"


def test_same_item_id_under_two_sources_coexists(storage):
    """``x:12345`` and ``youtube:12345`` are different things and must not merge."""
    report = storage.save([
        make_item("12345", source_id=SOURCE_X),
        make_item("12345", source_id="youtube"),
    ])

    assert report.inserted == 2
    assert report.duplicates == 0
    assert storage.known_keys("x") == {"x:12345"}
    assert storage.known_keys("youtube") == {"youtube:12345"}
    assert storage.known_keys() == {"x:12345", "youtube:12345"}


def test_item_without_an_id_is_rejected_and_counted(storage):
    report = storage.save([make_item(""), make_item("1")])
    assert report.invalid == 1
    assert report.inserted == 1


def test_item_without_content_is_rejected_and_counted(storage):
    """Identity alone is not enough — a record with no content carries nothing."""
    empty = NormalizedItem(source_id=SOURCE_X, item_id="9", fetched_at="2024-01-01T00:00:00Z")
    report = storage.save([empty, make_item("1")])
    assert report.invalid == 1
    assert report.inserted == 1


def test_item_without_fetched_at_is_rejected(storage):
    incomplete = NormalizedItem(source_id=SOURCE_X, item_id="9", content="hi")
    report = storage.save([incomplete])
    assert report.invalid == 1
    assert not storage.item_path(SOURCE_X).exists()


# --- canonical store: layout ----------------------------------------------
def test_sources_are_stored_in_separate_files(storage):
    storage.save([make_item("1", source_id="jack")])
    storage.save([make_item("2", source_id="vnovel")])
    assert storage.item_path("jack").name == "jack.jsonl"
    assert storage.item_path("vnovel").name == "vnovel.jsonl"


def test_one_batch_spanning_two_sources_is_split(storage):
    report = storage.save([
        make_item("1", source_id="jack"),
        make_item("2", source_id="vnovel"),
    ])
    assert report.inserted == 2
    assert storage.item_path("jack").exists()
    assert storage.item_path("vnovel").exists()


# --- canonical store: reading ---------------------------------------------
def test_known_keys_is_empty_for_an_unseen_source(storage):
    assert storage.known_keys("jack") == set()


def test_known_keys_reflects_what_was_saved(storage):
    storage.save([make_item("1"), make_item("2")])
    assert storage.known_keys(SOURCE_X) == {"x:1", "x:2"}


def test_known_keys_without_a_source_spans_every_file(storage):
    storage.save([make_item("1", source_id="jack")])
    storage.save([make_item("1", source_id="vnovel")])
    assert storage.known_keys() == {"jack:1", "vnovel:1"}


def test_malformed_line_is_skipped_not_fatal(storage):
    """A process killed mid-append leaves one torn line; the rest must survive."""
    storage.save([make_item("1"), make_item("2")])
    path = storage.item_path(SOURCE_X)
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"item_id": "3", "content": "trunca\n')  # torn write

    assert storage.known_keys(SOURCE_X) == {"x:1", "x:2"}

    report = storage.save([make_item("3")])
    assert report.malformed_lines == 1
    assert report.inserted == 1


def test_non_object_line_counts_as_malformed(storage):
    path = storage.item_path(SOURCE_X)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[1, 2, 3]\n", encoding="utf-8")
    assert storage.known_keys(SOURCE_X) == set()


def test_legacy_line_without_identity_key_is_still_recognised(storage):
    """A line written before the derived key existed must not be re-inserted."""
    path = storage.item_path(SOURCE_X)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"source_id": "x", "item_id": "1", "content": "old"}) + "\n",
        encoding="utf-8",
    )
    assert storage.known_keys(SOURCE_X) == {"x:1"}
    assert storage.save([make_item("1")]).inserted == 0


def test_save_with_no_items_is_a_no_op(storage):
    report = storage.save([])
    assert report.received == 0 and report.inserted == 0
    assert not storage.item_path(SOURCE_X).exists()


# --- raw record archive (evidence, separate interface) --------------------
def test_archive_writes_one_json_line_per_tweet(storage, account_obj):
    report = storage.archive_raw_tweets(account_obj, [make_tweet("1"), make_tweet("2")])
    assert (report.received, report.inserted) == (2, 2)

    lines = [line for line in
             storage.account_path(account_obj).read_text(encoding="utf-8").splitlines() if line]
    assert len(lines) == 2


def test_archive_preserves_the_raw_payload_verbatim(storage, account_obj):
    storage.archive_raw_tweets(account_obj, [make_tweet("7", text="original body")])
    record = json.loads(
        storage.account_path(account_obj).read_text(encoding="utf-8").strip()
    )
    assert record["raw"] == {"source": "nitter_html", "text": "original body"}


def test_archive_is_idempotent(storage, account_obj):
    storage.archive_raw_tweets(account_obj, [make_tweet("1")])
    second = storage.archive_raw_tweets(account_obj, [make_tweet("1")])
    assert (second.inserted, second.duplicates) == (0, 1)


def test_known_ids_reflects_what_was_archived(storage, account_obj):
    assert storage.known_ids(account_obj) == set()
    storage.archive_raw_tweets(account_obj, [make_tweet("1"), make_tweet("2")])
    assert storage.known_ids(account_obj) == {"1", "2"}


def test_archive_accepts_a_plain_string_unit(storage):
    """An RSS source has no ``Account``; the archive interface takes either."""
    storage.archive_raw_tweets("vnovel", [make_tweet("1", account="vnovel")])
    assert storage.known_ids("vnovel") == {"1"}
    assert storage.account_path("vnovel").name == "vnovel.jsonl"


def test_archive_and_canonical_store_do_not_share_a_file(storage, account_obj):
    """The two interfaces must not write to the same place by accident."""
    storage.save([make_item("1")])
    storage.archive_raw_tweets(account_obj, [make_tweet("1")])
    assert storage.item_path(SOURCE_X) != storage.account_path(account_obj)


# --- delivery state (deliberately NOT "seen") -----------------------------
def test_delivery_state_is_empty_before_any_attempt(storage):
    assert storage.delivery_state("telegram") == {}
    assert storage.pending_for("telegram", ["x:1", "x:2"]) == ["x:1", "x:2"]


def test_mark_delivered_removes_keys_from_pending(storage):
    written = storage.mark_delivered("telegram", ["x:1"], status=DELIVERY_SENT)
    assert written == 1
    assert storage.delivery_state("telegram") == {"x:1": DELIVERY_SENT}
    assert storage.pending_for("telegram", ["x:1", "x:2"]) == ["x:2"]


def test_a_failed_delivery_keeps_the_key_pending(storage):
    """The whole point: a failed output must not lose content."""
    storage.mark_delivered("telegram", ["x:1"], status=DELIVERY_FAILED, detail="429")
    assert storage.pending_for("telegram", ["x:1"]) == ["x:1"]


def test_delivery_state_is_per_output(storage):
    storage.mark_delivered("telegram", ["x:1"])
    assert storage.delivery_state("qq") == {}
    assert storage.pending_for("qq", ["x:1"]) == ["x:1"]


def test_delivery_is_independent_of_the_item_store(storage):
    """Marking something delivered must never touch (or need) the store."""
    storage.mark_delivered("telegram", ["x:1"])
    assert not storage.item_path(SOURCE_X).exists()
    assert storage.known_keys() == set()


def test_latest_delivery_status_wins(storage):
    storage.mark_delivered("telegram", ["x:1"], status=DELIVERY_FAILED, detail="429")
    storage.mark_delivered("telegram", ["x:1"], status=DELIVERY_SENT)
    assert storage.delivery_state("telegram") == {"x:1": DELIVERY_SENT}


# --- audit trail ----------------------------------------------------------
def test_run_record_is_appended_to_a_dated_file(storage):
    storage.record_run(
        RunRecord(run_id="r1", account="jack", status="ok", provider="nitter",
                  route="html", fetched=20, inserted=3, duplicates=17)
    )
    files = sorted(storage.runs_dir.glob("*.jsonl"))
    assert len(files) == 1
    record = json.loads(files[0].read_text(encoding="utf-8").strip())
    assert record["run_id"] == "r1"
    assert record["status"] == "ok"
    assert record["inserted"] == 3


def test_run_record_carries_the_kind_and_source_id(storage):
    """An operator must be able to tell an X unit from an RSS unit."""
    storage.record_run(
        RunRecord(run_id="r1", account="vnovel", status="ok", kind="rss",
                  source_id="vnovel", normalized=24)
    )
    files = sorted(storage.runs_dir.glob("*.jsonl"))
    record = json.loads(files[0].read_text(encoding="utf-8").strip())
    assert record["kind"] == "rss"
    assert record["source_id"] == "vnovel"
    assert record["normalized"] == 24


def test_run_records_accumulate(storage):
    for index in range(3):
        storage.record_run(
            RunRecord(run_id=f"r{index}", account="jack", status="ok")
        )
    files = sorted(storage.runs_dir.glob("*.jsonl"))
    lines = [line for line in files[0].read_text(encoding="utf-8").splitlines() if line]
    assert len(lines) == 3


def test_failed_run_record_keeps_the_error_kind(storage):
    storage.record_run(
        RunRecord(run_id="r1", account="jack", status="error",
                  error_kind="parsing_error", error="not a timeline page")
    )
    files = sorted(storage.runs_dir.glob("*.jsonl"))
    record = json.loads(files[0].read_text(encoding="utf-8").strip())
    assert record["error_kind"] == "parsing_error"
    assert record["error"] == "not a timeline page"


# --- raw capture policy ---------------------------------------------------
def test_capture_policy_never(storage, account_obj):
    storage.raw_policy = "never"
    assert storage.should_capture(failed=True, parsed=0) is False
    assert storage.should_capture(failed=False, parsed=5) is False


def test_capture_policy_on_error(storage, account_obj):
    assert storage.should_capture(failed=False, parsed=5) is False
    assert storage.should_capture(failed=False, parsed=0) is True
    assert storage.should_capture(failed=True, parsed=0) is True


def test_capture_policy_always(storage, account_obj):
    storage.raw_policy = "always"
    assert storage.should_capture(failed=False, parsed=5) is True


def test_capture_raw_writes_the_body_with_a_guessed_extension(storage, account_obj, make_response):
    response = make_response("<html>body</html>", content_type="text/html; charset=utf-8")
    path = storage.capture_raw(account_obj, "nitter", "html", response)

    assert path is not None
    assert path.endswith(".html")
    assert "nitter" in path and "html" in path
    assert "body" in open(path, encoding="utf-8").read()


def test_capture_raw_accepts_a_source_id_as_scope(storage, make_response):
    """RSS capture is scoped by source id, not by an X handle."""
    response = make_response("<rss/>", content_type="application/rss+xml")
    path = storage.capture_raw("vnovel", "rss", "feed", response)
    assert path is not None
    assert path.endswith(".xml")
    assert "vnovel" in path


def test_capture_raw_skips_an_empty_body(storage, account_obj, make_response):
    assert storage.capture_raw(account_obj, "nitter", "html", make_response("")) is None


def test_capture_raw_raises_storage_error_on_a_bad_path(tmp_path, account_obj, make_response):
    """The runner downgrades this to a warning; storage still reports honestly."""
    storage = JsonlStorage(data_dir=tmp_path / "data")
    storage.raw_dir = tmp_path / "data" / "raw"
    # A file where a directory must go makes mkdir fail.
    storage.raw_dir.parent.mkdir(parents=True, exist_ok=True)
    storage.raw_dir.write_text("not a directory", encoding="utf-8")
    with pytest.raises(StorageError):
        storage.capture_raw(account_obj, "nitter", "html", make_response("<html/>"))


# --- wiring and inspection ------------------------------------------------
def test_from_config_uses_the_configured_directory(tmp_path):
    config = StorageConfig(data_dir=str(tmp_path / "custom"), store_raw_response="always")
    storage = JsonlStorage.from_config(config)
    assert storage.data_dir == tmp_path / "custom"
    assert storage.raw_policy == "always"


def test_summary_counts_items_per_source(storage):
    storage.save([make_item("1", source_id="jack"), make_item("2", source_id="jack")])
    storage.save([make_item("3", source_id="vnovel")])
    storage.record_run(RunRecord(run_id="r", account="jack", status="ok"))

    summary = storage.summary()
    assert summary["items"] == {"jack": 2, "vnovel": 1}
    assert summary["total_items"] == 3
    assert summary["run_records"] == 1


def test_summary_reports_the_raw_archive_separately(storage, account_obj):
    """The archive is evidence; it must not be counted as canonical items."""
    storage.save([make_item("1")])
    storage.archive_raw_tweets(account_obj, [make_tweet("1"), make_tweet("2")])

    summary = storage.summary()
    assert summary["accounts"] == {"jack": 2}
    assert summary["total_tweets"] == 2
    assert summary["total_items"] == 1


def test_summary_reports_deliveries(storage):
    storage.mark_delivered("telegram", ["x:1"])
    assert storage.summary()["deliveries"] == {"telegram": 1}
