"""Cases ported from a production run of the governed-key fix (Hermes-side, pre-eviction).

The local hermes-agent patch ran 2026-08-31 to 2026-09-25 in a live local_embedded
setup (37 spurious daemon restarts before, flat after, through two Hermes updates).
These are the cases from that suite the nine pinned in ``test_profile_env_sync.py``
don't cover, plus the file-format edges a long-lived shared env file actually hits.

Deliberately out of scope here: the write path. ``_secure_write_profile_env``
truncates in place (no temp+rename), so a reader in the window sees governed keys
vanish; that half of the hotspot is tracked in #4662, not this PR.
"""

from hindsight_hermes import embedded

CONFIG = {
    "profile": "taller",
    "llm_provider": "openai_compatible",
    "llm_model": "some-model",
    "llmApiKey": "test-key",
    "llm_base_url": "https://llm.example/v1",
}


def _materialize(config: dict | None = None):
    """Write the profile env the way the plugin does, then return its path."""
    return embedded._materialize_embedded_profile_env(dict(config or CONFIG))


# ---------------------------------------------------------------------------
# The ignore-contract is generic, and it cuts both ways
# ---------------------------------------------------------------------------


def test_any_foreign_key_is_ignored_not_just_the_known_ones(hermes_env):
    """The guarantee is "keys this build never owns", not a HINDSIGHT_API_PORT
    special case: hindsight-embed's template is free to grow new keys, and the
    production incident fired on exactly such an unanticipated append."""
    path = _materialize()
    with path.open("a", encoding="utf-8") as fh:
        fh.write("HINDSIGHT_API_PORT=9176\n")
        fh.write("HINDSIGHT_API_HOST=127.0.0.1\n")
        fh.write("HINDSIGHT_DB_PATH=/var/lib/hindsight/db\n")
        fh.write("HINDSIGHT_API_EMBEDDINGS_LOCAL_MODEL=BAAI/bge-small-en-v1.5\n")

    assert embedded._profile_env_out_of_sync(dict(CONFIG)) is False


def test_real_drift_is_still_caught_with_foreign_keys_present(hermes_env):
    """The production loop, both sides at once: the daemon has appended its
    keys AND the operator has since changed a governed value. Loose on foreign
    keys must not mean loose on governed ones."""
    path = _materialize()
    with path.open("a", encoding="utf-8") as fh:
        fh.write("HINDSIGHT_API_PORT=9176\n")
        fh.write("HINDSIGHT_API_EMBEDDINGS_LOCAL_MODEL=BAAI/bge-small-en-v1.5\n")

    assert embedded._profile_env_out_of_sync({**CONFIG, "llm_model": "another-model"}) is True


def test_a_rotated_llm_key_is_a_mismatch(hermes_env):
    """The credential has its own write-path guard (_may_rewrite_profile_env:
    rotation to a new non-empty value stays writable), so the compare catching
    a rotated key is what makes that guard reachable at all."""
    _materialize()

    assert embedded._profile_env_out_of_sync({**CONFIG, "llmApiKey": "rotated-key"}) is True


# ---------------------------------------------------------------------------
# File-format edges a long-lived shared file actually hits (asked in-review)
# ---------------------------------------------------------------------------


def test_crlf_line_endings_are_not_a_mismatch(hermes_env):
    """A file edited on the Windows side of a mixed fleet, read back on POSIX:
    the parser strips, so ``value\\r\\n`` must still compare equal."""
    path = _materialize()
    path.write_text(path.read_text(encoding="utf-8").replace("\n", "\r\n"), encoding="utf-8")

    assert embedded._profile_env_out_of_sync(dict(CONFIG)) is False


def test_a_utf8_bom_is_not_a_mismatch(hermes_env):
    """utf-8-sig is already the parser's contract (Notepad BOM on the Hermes
    .env path); the sync check must inherit it, not re-trip on the first key."""
    path = _materialize()
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())

    assert embedded._profile_env_out_of_sync(dict(CONFIG)) is False


def test_a_duplicate_governed_line_with_the_same_value_is_not_a_mismatch(hermes_env):
    """A hand-edited file can carry the same governed line twice; the parser is
    last-wins, and the file's intent (the current value) is unambiguous."""
    path = _materialize()
    body = path.read_text(encoding="utf-8")
    model_line = next(line for line in body.splitlines() if "LLM_MODEL" in line)
    path.write_text(body + "\n" + model_line + "\n", encoding="utf-8")

    assert embedded._profile_env_out_of_sync(dict(CONFIG)) is False


def test_a_duplicate_governed_line_with_a_stale_value_is_a_mismatch(hermes_env):
    """Last-wins cuts the other way too: an appended stale duplicate is the
    value now, and must read as drift — not as noise the check shrugs off."""
    path = _materialize()
    body = path.read_text(encoding="utf-8")
    path.write_text(body + "\nHINDSIGHT_API_LLM_MODEL=older-model\n", encoding="utf-8")

    assert embedded._profile_env_out_of_sync(dict(CONFIG)) is True


def test_lines_without_an_equals_sign_are_ignored(hermes_env):
    """A half-written or hand-mangled file can carry junk lines; the parser
    skips them, and the check must neither crash nor read them as drift."""
    path = _materialize()
    with path.open("a", encoding="utf-8") as fh:
        fh.write("this line has no equals sign\n")
        fh.write("# a stray comment\n")

    assert embedded._profile_env_out_of_sync(dict(CONFIG)) is False


# ---------------------------------------------------------------------------
# The read side of the two-inits race (asked in-review; write side is #4662)
# ---------------------------------------------------------------------------


def test_a_file_whose_governed_keys_were_torn_away_reads_as_a_mismatch(hermes_env):
    """Reader slice of the two-inits race: one init truncates (in-place
    O_TRUNC, no atomic rename yet) while another reads mid-write and sees
    governed keys vanish. The compare must see that as drift so the next
    init rewrites and settles — the cost of the spurious rewrite in that
    window is the write-path half, tracked in #4662."""
    path = _materialize()
    torn = path.read_text(encoding="utf-8").splitlines(keepends=True)[:2]
    path.write_text("".join(torn), encoding="utf-8")

    assert embedded._profile_env_out_of_sync(dict(CONFIG)) is True

    # ... and it settles after the rewrite the daemon-start path performs.
    _materialize()
    assert embedded._profile_env_out_of_sync(dict(CONFIG)) is False