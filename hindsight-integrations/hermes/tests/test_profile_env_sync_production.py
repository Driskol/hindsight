"""Production cases for the profile env sync check — ported from a live host.

These extend ``test_profile_env_sync.py`` (the nine pinned there) with the
shapes a fixture and a running setup disagree on. Each is grounded in an
observed production file state on a Hermes gateway that ran the equivalent
fix from 2026-08-31 (bundled-plugin local patch; 37 daemon restarts → flat
across two Hermes updates).
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
    return embedded._materialize_embedded_profile_env(dict(config or CONFIG))


def _env_path(config: dict | None = None):
    return embedded._embedded_profile_env_path(dict(config or CONFIG))


# ── 1. line endings ──────────────────────────────────────────────────────────


def test_crlf_body_from_a_windows_write_still_compares_equal(hermes_env):
    """The production file was rewritten by the Windows side of the setup and
    read back by the POSIX plugin: a CRLF body must not be a perpetual mismatch,
    or every init rewrites the file and kills a healthy daemon — the exact bug
    this PR fixes, resurrected by an editor instead of by hindsight-embed."""
    path = _materialize()
    body = path.read_text(encoding="utf-8")
    path.write_bytes(body.replace("\n", "\r\n").encode("utf-8"))

    assert embedded._profile_env_out_of_sync(dict(CONFIG)) is False


def test_bom_on_the_file_is_not_a_mismatch(hermes_env):
    """Notepad-style BOM sticks to the first key without an explicit utf-8-sig
    read (``_load_simple_env`` already reads utf-8-sig; this pins that the sync
    check inherits it). Observed on a hand-edited production file."""
    path = _materialize()
    body = path.read_text(encoding="utf-8")
    path.write_bytes(b"\xef\xbb\xbf" + body.encode("utf-8"))

    assert embedded._profile_env_out_of_sync(dict(CONFIG)) is False


# ── 2. duplicate / malformed governed lines ──────────────────────────────────


def test_duplicate_governed_lines_are_last_wins(hermes_env):
    """A hand-edited file carried ``HINDSIGHT_API_LLM_MODEL`` twice. The parser
    is last-wins (a dict comprehension over the lines), so the pair resolves
    deterministically: effective drift only when the stale value lands last —
    which is what an edit that appends the new value on top of the old line
    looks like. Pin the contract in both directions so a future parser change
    cannot make duplicate lines silently ambiguous."""
    path = _materialize()
    lines = path.read_text(encoding="utf-8").splitlines()
    model_idx = next(i for i, l in enumerate(lines) if l.startswith("HINDSIGHT_API_LLM_MODEL="))

    # stale duplicate AFTER the good line: last-wins keeps the stale value.
    lines.insert(model_idx + 1, "HINDSIGHT_API_LLM_MODEL=stale-model")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert embedded._profile_env_out_of_sync(dict(CONFIG)) is True

    # stale duplicate BEFORE the good line: last-wins resolves to the good value.
    lines = path.read_text(encoding="utf-8").splitlines()
    lines = [l for l in lines if l != "HINDSIGHT_API_LLM_MODEL=stale-model"]
    model_idx = next(i for i, l in enumerate(lines) if l.startswith("HINDSIGHT_API_LLM_MODEL="))
    lines.insert(model_idx, "HINDSIGHT_API_LLM_MODEL=stale-model")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert embedded._profile_env_out_of_sync(dict(CONFIG)) is False

    # and the drift settles after one rewrite, like every other mismatch
    _materialize(dict(CONFIG))
    assert embedded._profile_env_out_of_sync(dict(CONFIG)) is False


def test_malformed_line_without_equals_is_ignored_for_governed_keys(hermes_env):
    """A truncated write left a bare ``HINDSIGHT_API_LLM_MODEL`` line with no
    ``=`` (the value lost to the truncation window). The parser skips it; the
    governed key is then genuinely missing — a mismatch, so the rewrite repairs
    the file rather than keeping a half-written env live."""
    path = _materialize()
    lines = path.read_text(encoding="utf-8").splitlines()
    lines = [l.split("=", 1)[0] if l.startswith("HINDSIGHT_API_LLM_MODEL") else l for l in lines]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    assert embedded._profile_env_out_of_sync(dict(CONFIG)) is True


# ── 3. two provider inits racing the rewrite ─────────────────────────────────


def test_a_truncated_file_from_a_racing_rewrite_reads_as_drift(hermes_env):
    """The shape that still shows up as intermittent restarts after this fix:
    init A truncates the file (O_TRUNC, then writes) while init B reads it, so
    B sees governed keys missing and queues its own rewrite. The check must
    report that half-written state as out of sync — the daemon-start path's
    rewrite settles it, and the next init is in sync again (no loop: the
    settled file matches)."""
    _materialize()
    path = _env_path()
    # simulate the truncation window: file exists, governed keys gone
    path.write_text("HINDSIGHT_API_PORT=9177\n", encoding="utf-8")  # foreign key survives the window
    assert embedded._profile_env_out_of_sync(dict(CONFIG)) is True

    # what the daemon-start path does on a mismatch: rewrite, then settle
    _materialize(dict(CONFIG))
    with path.open("a", encoding="utf-8") as fh:  # the daemon re-appends its keys after
        fh.write("HINDSIGHT_API_PORT=9177\n")
    assert embedded._profile_env_out_of_sync(dict(CONFIG)) is False


# ── 4. the write path: operator-owned keys vs O_TRUNC ────────────────────────


def test_operator_owned_keys_do_not_survive_a_genuine_drift_rewrite(hermes_env):
    """The O_TRUNC half of the #4662 hotspot (deliberately out of scope for the
    compare fix, filed separately there): when real drift DOES trigger a
    rewrite, the rewrite drops keys this build does not own. On the production
    host that ate ``HF_HUB_OFFLINE`` and ``HINDSIGHT_EMBEDDINGS_LOCAL_FORCE_CPU``
    the operator had set, silently changing embedder behaviour. Kept red here so
    the data-loss half is pinned; flip the assertion when _secure_write merges
    foreign keys.
    """
    _materialize()
    path = _env_path()
    with path.open("a", encoding="utf-8") as fh:
        fh.write("HF_HUB_OFFLINE=1\n")
        fh.write("HINDSIGHT_EMBEDDINGS_LOCAL_FORCE_CPU=true\n")

    # real drift: model changed
    _materialize({**CONFIG, "llm_model": "another-model"})

    on_disk = embedded._load_simple_env(path)
    assert "HF_HUB_OFFLINE" not in on_disk  # RED today: O_TRUNC dropped it