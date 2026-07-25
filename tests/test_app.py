"""Application, integration boundary, and prompt tests."""
import os
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import pytest
from fastapi import BackgroundTasks, HTTPException
os.environ.setdefault("VERIFY_TOKEN", "qa-token")
import app
from config import GROQ_MODEL, PHONE_NUMBER_ID, required_env

def payload(message_id: str = "wamid.1") -> dict:
    return {"entry": [{"changes": [{"value": {"messages": [{"id": message_id, "from": "49123", "type": "text", "text": {"body": "Hilfe"}}]}}]}]}

def test_model_and_phone_configuration() -> None:
    assert GROQ_MODEL == "llama-3.3-70b-versatile"
    assert PHONE_NUMBER_ID == "1264010770128749"

def test_required_env() -> None:
    with patch.dict(os.environ, {"GROQ_API_KEY": " key "}, clear=True):
        assert required_env("GROQ_API_KEY") == "key"
    with patch.dict(os.environ, {}, clear=True), pytest.raises(RuntimeError, match="GROQ_API_KEY"):
        required_env("GROQ_API_KEY")

def test_extract_messages_and_ignore_statuses() -> None:
    assert app.extract_text_messages(payload()) == [("wamid.1", "49123", "Hilfe")]
    assert app.extract_text_messages({"entry": [{"changes": [{"value": {"statuses": [{}]}}]}]}) == []

def test_verification() -> None:
    with patch.dict(os.environ, {"VERIFY_TOKEN": "qa-token"}, clear=True):
        assert app.verify_webhook("subscribe", "qa-token", "123").body == b"123"
        with pytest.raises(HTTPException) as error:
            app.verify_webhook("subscribe", "wrong", "123")
        assert error.value.status_code == 403

def test_webhook_only_queues_work_and_is_fast() -> None:
    tasks = BackgroundTasks()
    started = time.perf_counter()
    assert app.receive_webhook(payload(), tasks) == {"status": "accepted"}
    assert time.perf_counter() - started < 0.1
    assert len(tasks.tasks) == 1

def test_groq_receives_configured_model() -> None:
    completion = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Antwort"))])
    client = MagicMock()
    client.chat.completions.create.return_value = completion
    with patch.object(app, "Groq", return_value=client), patch.dict(os.environ, {"GROQ_API_KEY": "secret"}, clear=True):
        assert app.generate_reply("Hallo") == "Antwort"
    assert client.chat.completions.create.call_args.kwargs["model"] == GROQ_MODEL
    assert "Keine Rechtsberatung" in app.SYSTEM_PROMPT

def test_process_message_deduplicates(tmp_path) -> None:
    app.store = app.JsonDataStore(tmp_path / "store.json")
    with patch.object(app, "generate_reply", return_value="Antwort") as generate, patch.object(app, "send_whatsapp_message") as send:
        app.process_message("same", "49123", "Hallo")
        app.process_message("same", "49123", "Hallo")
    generate.assert_called_once()
    send.assert_called_once_with("49123", "Antwort")
    assert app.store.snapshot()["messages"]["same"]["status"] == "sent"
