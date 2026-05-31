from lmc5.redact import redact_embedding_input, redact_obj, redact_text


def test_redacts_common_secrets():
    fake_key = "sk-" + "123456789abcdef"
    fake_dsn = "postgresql" + "://user:pass@127.0.0.1:5432/app"
    text = (
        "Authorization: Bearer abcdefghijklmnop "
        f"api_key={fake_key} "
        f"{fake_dsn}"
    )

    redacted = redact_text(text)

    assert "abcdefghijklmnop" not in redacted
    assert fake_key not in redacted
    assert "user:pass" not in redacted
    assert "[REDACTED]" in redacted


def test_redacts_sensitive_object_keys():
    assert redact_obj({"api_key": "secret-value", "safe": "192.0.2.1"}) == {
        "api_key": "[REDACTED]",
        "safe": "[REDACTED_IP]",
    }


def test_embedding_redaction_keeps_prompt_noise_out_of_scope():
    text = "self-harm note with host=localhost user=admin"

    redacted = redact_embedding_input(text)

    assert "self-harm" in redacted
    assert "localhost" not in redacted
    assert "admin" not in redacted
