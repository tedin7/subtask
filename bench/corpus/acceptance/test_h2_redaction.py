"""Acceptance h2: redaction covers common secret formats, not only sk-ant keys."""

from subtask.worker import redact

SECRETS = [
    "sk-ant-api03-abcdefghijklmnop1234",
    "sk-proj-AbCdEf0123456789xyzQRSTuvw",
    "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8",
    "github_pat_11ABCDEFG0123456789_abcdefghijklmnopqrstuvwxyz0123456789",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
    "AKIAIOSFODNN7EXAMPLE",
]


def test_known_formats_are_masked():
    for secret in SECRETS:
        out = redact(f"the value is {secret} ok")
        assert secret not in out, secret
        assert out.startswith("the value is ") and out.endswith(" ok")


def test_private_key_block_masked():
    block = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEAs3cr3t\n-----END RSA PRIVATE KEY-----"
    out = redact("key:\n" + block + "\nend")
    assert "MIIEowIBAAKCAQEAs3cr3t" not in out


def test_key_value_secrets_masked():
    out = redact("password=hunter2secret and API_KEY: zz9988776655")
    assert "hunter2secret" not in out and "zz9988776655" not in out


def test_plain_text_untouched():
    text = "task-ant runs sk- tests; the token budget is 40k; ghp is short"
    assert redact(text) == text
