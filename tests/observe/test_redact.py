import pytest

from aiarb.observe.redact import REDACTED, Redactor

ANTHROPIC = "sk-ant-api03-" + "a1B2c3D4" * 6
GITHUB = "ghp_" + "a1B2c3D4e5" * 4
AWS = "AKIA" + "ABCDEFGH12345678"
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
PRIVATE_KEY = (
    "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\nq1w2e3r4\n-----END RSA PRIVATE KEY-----"
)


class TestEnvironment:
    def test_takes_out_the_values_of_secret_variables(self) -> None:
        redactor = Redactor.from_environment(
            {"TYPESAFE_API_KEY": "ts-9f8e7d6c5b4a", "GITHUB_TOKEN": "x9y8z7w6v5u4t3"}
        )

        text = redactor.text("Called with ts-9f8e7d6c5b4a and x9y8z7w6v5u4t3.")

        assert text == f"Called with {REDACTED} and {REDACTED}."

    @pytest.mark.parametrize(
        ("name", "value"),
        [
            ("HOME", "/Users/someone12345"),  # not a secret's name
            ("SSH_KEY_PATH", "/Users/someone/.ssh/id_ed25519"),  # a path
            ("SORT_KEY", "created"),  # too short
            ("API_KEY_NAME", "production-settings"),  # an ordinary word
        ],
    )
    def test_leaves_ordinary_settings_alone(self, name: str, value: str) -> None:
        redactor = Redactor.from_environment({name: value})

        assert redactor.secrets == ()

    def test_a_longer_secret_is_taken_out_before_one_inside_it(self) -> None:
        redactor = Redactor(("abc123def456", "abc123def456ghi789"))

        assert redactor.text("abc123def456ghi789") == REDACTED


class TestFormats:
    @pytest.mark.parametrize("secret", [ANTHROPIC, GITHUB, AWS, JWT, PRIVATE_KEY])
    def test_takes_out_secrets_known_by_their_shape(self, secret: str) -> None:
        text = Redactor().text(f"config.py hard-codes {secret} on line 3.")

        assert text == f"config.py hard-codes {REDACTED} on line 3."

    def test_takes_out_bearer_tokens(self) -> None:
        text = Redactor().text("curl -H 'Authorization: Bearer abcdef0123456789xyz'")

        assert text == f"curl -H 'Authorization: Bearer {REDACTED}'"

    def test_takes_out_passwords_in_urls_but_keeps_the_rest(self) -> None:
        text = Redactor().text("DATABASE_URL is postgres://app:hunter22@db:5432/app")

        assert text == f"DATABASE_URL is postgres://app:{REDACTED}@db:5432/app"

    def test_leaves_urls_without_passwords_alone(self) -> None:
        text = "See http://localhost:8000/health and https://example.com/a@b"

        assert Redactor().text(text) == text


class TestAssignments:
    @pytest.mark.parametrize(
        ("text", "redacted"),
        [
            ("STRIPE_SECRET=abc123def456", f"STRIPE_SECRET={REDACTED}"),
            ('password = "changeme"', f'password = "{REDACTED}"'),
            ('{"api_key": "q1w2e3r4t5y6"}', f'{{"api_key": "{REDACTED}"}}'),
            ("db_password: s3cretpass", f"db_password: {REDACTED}"),
        ],
    )
    def test_takes_out_values_given_to_secret_names(self, text: str, redacted: str) -> None:
        assert Redactor().text(text) == redacted

    @pytest.mark.parametrize(
        "text",
        [
            "password = request.form['password']",
            "token = settings.auth_token",
            "password: hashed_with_bcrypt",
            "max_tokens: 4096",
            "token_count = count_tokens(text)",
        ],
    )
    def test_leaves_code_alone(self, text: str) -> None:
        assert Redactor().text(text) == text

    def test_redacting_twice_changes_nothing_more(self) -> None:
        once = Redactor().text(f'password = "changeme" and {ANTHROPIC}')

        assert Redactor().text(once) == once


def test_redacts_every_string_in_data_but_not_the_keys() -> None:
    data = {"api_key_note": [ANTHROPIC, ("ok", 3)], "count": 2, "nested": {"x": GITHUB}}

    assert Redactor().data(data) == {
        "api_key_note": [REDACTED, ["ok", 3]],
        "count": 2,
        "nested": {"x": REDACTED},
    }
