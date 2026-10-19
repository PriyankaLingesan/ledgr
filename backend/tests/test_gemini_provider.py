"""The Gemini adapter, with the network replaced: nothing here reaches a real model."""

import logging

import httpx

from app.services.ai.provider import AIMessage, GeminiProvider


def _fake_post(captured, finish="STOP", text="A full sentence."):
    def post(url, *, headers, json, timeout):
        captured["json"], captured["headers"], captured["url"] = json, headers, url
        return httpx.Response(
            200,
            json={"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": finish}]},
            request=httpx.Request("POST", url),
        )

    return post


def _complete(max_tokens=300):
    provider = GeminiProvider(api_key="test-key", model="m", base_url="", timeout=5)
    return provider.complete(
        system="s", messages=[AIMessage(role="user", content="hi")], max_tokens=max_tokens
    )


def test_thinking_models_get_headroom_so_a_small_cap_does_not_truncate_the_answer(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(httpx, "post", _fake_post(captured))
    result = _complete(max_tokens=300)
    assert result.text == "A full sentence."
    assert captured["json"]["generationConfig"]["maxOutputTokens"] == 300 + 2048


def test_the_key_travels_in_a_header_never_in_the_url(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(httpx, "post", _fake_post(captured))
    _complete()
    assert "test-key" not in captured["url"]
    assert captured["headers"]["x-goog-api-key"] == "test-key"


def test_a_cut_off_answer_is_logged_not_silently_accepted(monkeypatch):
    # The app reconfigures the root logger on import, which detaches pytest's caplog, so
    # attach a handler to the adapter's own logger.
    records: list[logging.LogRecord] = []

    class Collect(logging.Handler):
        def emit(self, record):
            records.append(record)

    logger = logging.getLogger("ledgr.ai")
    handler = Collect(level=logging.WARNING)
    previous, was_disabled = logger.level, logger.disabled
    logger.disabled = False  # Alembic's fileConfig() (run by the harness) disables existing loggers
    logger.setLevel(logging.WARNING)  # whatever LOG_LEVEL the environment configured
    logger.addHandler(handler)
    try:
        monkeypatch.setattr(httpx, "post", _fake_post({}, finish="MAX_TOKENS", text="half a sen"))
        _complete()
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)
        logger.disabled = was_disabled
    assert any("cut off" in r.getMessage() for r in records)
    assert all("test-key" not in r.getMessage() for r in records)
