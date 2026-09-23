"""The profile env sync check: only the keys this build governs count as a mismatch.

hindsight-embed appends its own keys (``HINDSIGHT_API_PORT``, embedding config, ...) to the
same profile env file the plugin writes, so a whole-mapping equality check is permanently
unequal — every daemon start rewrote the file and stopped a healthy daemon.
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


def test_keys_the_build_does_not_own_are_not_a_mismatch(hermes_env):
    """The regression: hindsight-embed's own keys must not trigger a rewrite.

    The file it maintains carries more keys than this build produces (it appends
    ``HINDSIGHT_API_PORT`` on start), so the whole-mapping comparison was always unequal:
    every start rewrote the env and killed the running daemon, losing in-flight retains.
    """
    path = _materialize()
    with path.open("a", encoding="utf-8") as fh:
        fh.write("HINDSIGHT_API_PORT=9176\n")
        fh.write("HINDSIGHT_API_EMBEDDINGS_LOCAL_MODEL=BAAI/bge-small-en-v1.5\n")

    assert embedded._profile_env_out_of_sync(dict(CONFIG)) is False


def test_a_missing_governed_key_is_a_mismatch(hermes_env):
    path = _materialize()
    kept = [line for line in path.read_text(encoding="utf-8").splitlines() if "LLM_MODEL" not in line]
    path.write_text("\n".join(kept) + "\n", encoding="utf-8")

    assert embedded._profile_env_out_of_sync(dict(CONFIG)) is True


def test_a_changed_governed_value_is_a_mismatch(hermes_env):
    """Config drift must still rewrite and restart: the check is not a no-op."""
    _materialize()

    assert embedded._profile_env_out_of_sync({**CONFIG, "llm_model": "another-model"}) is True


def test_an_absent_profile_env_is_a_mismatch(hermes_env):
    assert embedded._profile_env_out_of_sync(dict(CONFIG)) is True


def test_whitespace_and_comments_around_governed_keys_are_not_a_mismatch(hermes_env):
    """The parser already strips and ignores lines; the sync check must inherit that."""
    path = _materialize()
    body = path.read_text(encoding="utf-8").replace("=", " = ", 1)
    path.write_text("# managed by hindsight-embed\n" + body, encoding="utf-8")

    assert embedded._profile_env_out_of_sync(dict(CONFIG)) is False
