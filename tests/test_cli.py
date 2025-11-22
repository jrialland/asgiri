"""Tests for CLI argument parsing and application loading."""

import argparse
from unittest.mock import Mock, patch

import pytest
from asgiref.wsgi import WsgiToAsgi

from asgiri.cli import create_parser, load_application, parse_args
from asgiri.server import HttpProtocolVersion


class TestLoadApplication:
    """Tests for load_application function."""

    def test_load_asgi_application(self):
        """Test loading a valid ASGI application."""
        app = load_application("tests.app:app")
        assert app is not None
        assert callable(app)

    def test_load_application_invalid_format_no_colon(self):
        """Test that invalid format without colon raises ValueError."""
        with pytest.raises(
            ValueError, match="Invalid application specification"
        ):
            load_application("tests.app.app")

    def test_load_application_invalid_format_empty_module(self):
        """Test that invalid format with empty module raises ValueError or ImportError."""
        # Empty module name will raise ValueError from importlib
        with pytest.raises((ValueError, ImportError)):
            load_application(":app")

    def test_load_application_module_not_found(self):
        """Test that non-existent module raises ImportError."""
        with pytest.raises(ImportError, match="Could not import module"):
            load_application("nonexistent_module:app")

    def test_load_application_attribute_not_found(self):
        """Test that non-existent attribute raises AttributeError."""
        with pytest.raises(AttributeError, match="has no attribute"):
            load_application("tests.app:nonexistent_attribute")

    def test_load_wsgi_application(self):
        """Test loading and wrapping a WSGI application."""
        # Create a mock WSGI app in the tests module
        with patch("asgiri.cli.importlib.import_module") as mock_import:
            mock_module = Mock()

            # Create a simple WSGI app
            def wsgi_app(environ, start_response):
                status = "200 OK"
                headers = [("Content-Type", "text/plain")]
                start_response(status, headers)
                return [b"Hello World"]

            mock_module.wsgi_app = wsgi_app
            mock_import.return_value = mock_module

            app = load_application("tests.app:wsgi_app", wsgi=True)

            # Verify it's wrapped
            assert isinstance(app, WsgiToAsgi)

    def test_load_application_with_nested_module(self):
        """Test loading application from nested module path."""
        # This should work with any valid nested module in the project
        app = load_application("tests.app:app")
        assert app is not None


class TestCreateParser:
    """Tests for create_parser function."""

    def test_parser_creation(self):
        """Test that parser is created successfully."""
        parser = create_parser()
        assert isinstance(parser, argparse.ArgumentParser)
        assert parser.prog == "asgiri"

    def test_parser_has_required_arguments(self):
        """Test that parser has all required arguments."""
        parser = create_parser()

        # Get all argument names
        actions = {action.dest for action in parser._actions}

        # Check for key arguments
        assert "application" in actions
        assert "protocol" in actions
        assert "host" in actions
        assert "port" in actions
        assert "workers" in actions
        assert "selfcert" in actions
        assert "cert" in actions
        assert "key" in actions
        assert "wsgi" in actions
        assert "lifespan_policy" in actions
        assert "log_level" in actions

    def test_parser_protocol_group_mutually_exclusive(self):
        """Test that protocol flags are mutually exclusive."""
        parser = create_parser()

        # Try to use both --http11 and --http2
        with pytest.raises(SystemExit):
            parser.parse_args(["--http11", "--http2", "app:app"])

    def test_parser_tls_group_selfcert_mutually_exclusive(self):
        """Test that --selfcert is in a mutually exclusive group."""
        parser = create_parser()

        # --selfcert should work alone
        args = parser.parse_args(["--selfcert", "app:app"])
        assert args.selfcert is True


class TestParseArgs:
    """Tests for parse_args function."""

    def test_parse_minimal_args(self):
        """Test parsing with only required arguments."""
        args = parse_args(["myapp:app"])

        assert args.application == "myapp:app"
        assert args.host == "127.0.0.1"
        assert args.port == -1  # Default port before adjustment
        assert args.protocol == HttpProtocolVersion.AUTO
        assert args.workers == "1"
        assert args.wsgi is False
        assert args.selfcert is False
        assert args.lifespan_policy == "auto"
        assert args.log_level == "INFO"

    def test_parse_http11_protocol(self):
        """Test parsing with --http11 flag."""
        args = parse_args(["--http11", "myapp:app"])
        assert args.protocol == HttpProtocolVersion.HTTP_1_1

    def test_parse_http2_protocol(self):
        """Test parsing with --http2 flag."""
        args = parse_args(["--http2", "myapp:app"])
        assert args.protocol == HttpProtocolVersion.AUTO

    def test_parse_http3_protocol(self):
        """Test parsing with --http3 flag."""
        args = parse_args(["--http3", "myapp:app"])
        assert args.protocol == HttpProtocolVersion.HTTP_3

    def test_parse_custom_host(self):
        """Test parsing with custom host."""
        args = parse_args(["--host", "0.0.0.0", "myapp:app"])
        assert args.host == "0.0.0.0"

    def test_parse_custom_port(self):
        """Test parsing with custom port."""
        args = parse_args(["--port", "8080", "myapp:app"])
        assert args.port == 8080

    def test_parse_workers_number(self):
        """Test parsing with workers as number."""
        args = parse_args(["--workers", "4", "myapp:app"])
        assert args.workers == "4"

    def test_parse_workers_auto(self):
        """Test parsing with workers as 'auto'."""
        args = parse_args(["--workers", "auto", "myapp:app"])
        assert args.workers == "auto"

    def test_parse_selfcert(self):
        """Test parsing with --selfcert flag."""
        args = parse_args(["--selfcert", "myapp:app"])
        assert args.selfcert is True
        assert args.cert is None
        assert args.key is None

    def test_parse_cert_and_key(self):
        """Test parsing with --cert and --key."""
        args = parse_args(
            ["--cert", "cert.pem", "--key", "key.pem", "myapp:app"]
        )
        assert args.cert == "cert.pem"
        assert args.key == "key.pem"
        assert args.selfcert is False

    def test_parse_wsgi_flag(self):
        """Test parsing with --wsgi flag."""
        args = parse_args(["--wsgi", "myapp:wsgi_app"])
        assert args.wsgi is True

    def test_parse_lifespan_policy_enabled(self):
        """Test parsing with lifespan policy enabled."""
        args = parse_args(["--lifespan-policy", "enabled", "myapp:app"])
        assert args.lifespan_policy == "enabled"

    def test_parse_lifespan_policy_disabled(self):
        """Test parsing with lifespan policy disabled."""
        args = parse_args(["--lifespan-policy", "disabled", "myapp:app"])
        assert args.lifespan_policy == "disabled"

    def test_parse_log_level_debug(self):
        """Test parsing with DEBUG log level."""
        args = parse_args(["--log-level", "DEBUG", "myapp:app"])
        assert args.log_level == "DEBUG"

    def test_parse_log_level_warning(self):
        """Test parsing with WARNING log level."""
        args = parse_args(["--log-level", "WARNING", "myapp:app"])
        assert args.log_level == "WARNING"

    def test_parse_all_options_combined(self):
        """Test parsing with multiple options combined."""
        args = parse_args(
            [
                "--http11",
                "--host",
                "0.0.0.0",
                "--port",
                "8080",
                "--workers",
                "4",
                "--cert",
                "cert.pem",
                "--key",
                "key.pem",
                "--lifespan-policy",
                "enabled",
                "--log-level",
                "DEBUG",
                "myapp:app",
            ]
        )

        assert args.protocol == HttpProtocolVersion.HTTP_1_1
        assert args.host == "0.0.0.0"
        assert args.port == 8080
        assert args.workers == "4"
        assert args.cert == "cert.pem"
        assert args.key == "key.pem"
        assert args.lifespan_policy == "enabled"
        assert args.log_level == "DEBUG"
        assert args.application == "myapp:app"

    def test_parse_missing_required_application(self):
        """Test that missing application argument causes SystemExit."""
        with pytest.raises(SystemExit):
            parse_args([])

    def test_parse_invalid_port_type(self):
        """Test that invalid port type causes SystemExit."""
        with pytest.raises(SystemExit):
            parse_args(["--port", "not-a-number", "myapp:app"])

    def test_parse_invalid_log_level(self):
        """Test that invalid log level causes SystemExit."""
        with pytest.raises(SystemExit):
            parse_args(["--log-level", "INVALID", "myapp:app"])

    def test_parse_invalid_lifespan_policy(self):
        """Test that invalid lifespan policy causes SystemExit."""
        with pytest.raises(SystemExit):
            parse_args(["--lifespan-policy", "invalid", "myapp:app"])

    def test_parse_help_flag(self):
        """Test that --help flag causes SystemExit."""
        with pytest.raises(SystemExit) as exc_info:
            parse_args(["--help"])
        # Help should exit with code 0
        assert exc_info.value.code == 0

    def test_default_protocol_is_auto(self):
        """Test that default protocol is AUTO."""
        args = parse_args(["myapp:app"])
        assert args.protocol == HttpProtocolVersion.AUTO

    def test_parse_application_with_dots(self):
        """Test parsing application with dots in module path."""
        args = parse_args(["my.nested.module:app"])
        assert args.application == "my.nested.module:app"

    def test_parse_application_with_underscores(self):
        """Test parsing application with underscores."""
        args = parse_args(["my_app_module:my_app"])
        assert args.application == "my_app_module:my_app"

    def test_parse_port_zero_allowed(self):
        """Test that port 0 (auto-assign) is allowed."""
        args = parse_args(["--port", "0", "myapp:app"])
        assert args.port == 0

    def test_parse_high_port_number(self):
        """Test parsing with high port number."""
        args = parse_args(["--port", "65535", "myapp:app"])
        assert args.port == 65535

    def test_protocol_flags_mutually_exclusive(self):
        """Test that only one protocol flag can be used."""
        # --http11 and --http3 should be mutually exclusive
        with pytest.raises(SystemExit):
            parse_args(["--http11", "--http3", "myapp:app"])

        # --http2 and --http3 should be mutually exclusive
        with pytest.raises(SystemExit):
            parse_args(["--http2", "--http3", "myapp:app"])

    def test_parse_with_equals_syntax(self):
        """Test parsing with --option=value syntax."""
        args = parse_args(
            ["--host=192.168.1.1", "--port=9000", "--workers=2", "myapp:app"]
        )

        assert args.host == "192.168.1.1"
        assert args.port == 9000
        assert args.workers == "2"

    def test_parse_cert_without_key_accepted_by_parser(self):
        """Test that parser accepts --cert without --key (validation happens later)."""
        # Parser should accept this, but the main() function validates it
        args = parse_args(["--cert", "cert.pem", "myapp:app"])
        assert args.cert == "cert.pem"
        assert args.key is None

    def test_parse_key_without_cert_accepted_by_parser(self):
        """Test that parser accepts --key without --cert (validation happens later)."""
        # Parser should accept this, but the main() function validates it
        args = parse_args(["--key", "key.pem", "myapp:app"])
        assert args.key == "key.pem"
        assert args.cert is None
