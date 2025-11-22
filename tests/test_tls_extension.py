"""
Tests for TLS extension middleware.

Verifies that the TLS extension properly populates the scope with TLS information
as specified in https://asgi.readthedocs.io/en/latest/specs/tls.html
"""

import ssl
from unittest.mock import MagicMock, Mock

import pytest

from asgiri.extensions.tls import TLSExtensionMiddleware


@pytest.mark.asyncio
async def test_tls_extension_adds_tls_info_to_http_scope():
    """Test that TLS extension adds TLS info to HTTP scope."""
    # Create a mock ASGI app
    app_called = False
    received_scope = None

    async def mock_app(scope, receive, send):
        nonlocal app_called, received_scope
        app_called = True
        received_scope = scope

    # Create SSL context
    ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)

    # Create middleware
    middleware = TLSExtensionMiddleware(mock_app, ssl_context)

    # Create a basic HTTP scope
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "path": "/",
        "query_string": b"",
        "headers": [],
    }

    # Call middleware
    async def mock_receive():
        return {"type": "http.request"}

    async def mock_send(message):
        pass

    await middleware(scope, mock_receive, mock_send)

    # Verify app was called
    assert app_called
    assert received_scope is not None

    # Verify TLS extension was added
    assert "extensions" in received_scope
    assert "tls" in received_scope["extensions"]

    tls_info = received_scope["extensions"]["tls"]

    # Verify all mandatory fields are present
    assert "server_cert" in tls_info
    assert "tls_version" in tls_info
    assert "cipher_suite" in tls_info

    # Verify optional fields are present (with default values)
    assert "client_cert_chain" in tls_info
    assert "client_cert_name" in tls_info
    assert "client_cert_error" in tls_info

    # Without actual SSL connection, these should have None or empty values
    assert tls_info["server_cert"] is None
    assert tls_info["client_cert_chain"] == []
    assert tls_info["client_cert_name"] is None
    assert tls_info["client_cert_error"] is None
    assert tls_info["tls_version"] is None
    assert tls_info["cipher_suite"] is None


@pytest.mark.asyncio
async def test_tls_extension_adds_tls_info_to_websocket_scope():
    """Test that TLS extension adds TLS info to WebSocket scope."""
    received_scope = None

    async def mock_app(scope, receive, send):
        nonlocal received_scope
        received_scope = scope

    ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    middleware = TLSExtensionMiddleware(mock_app, ssl_context)

    scope = {
        "type": "websocket",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "path": "/ws",
        "query_string": b"",
        "headers": [],
    }

    async def mock_receive():
        return {"type": "websocket.connect"}

    async def mock_send(message):
        pass

    await middleware(scope, mock_receive, mock_send)

    # Verify TLS extension was added to WebSocket scope
    assert received_scope is not None
    assert "extensions" in received_scope
    assert "tls" in received_scope["extensions"]

    tls_info = received_scope["extensions"]["tls"]
    assert "server_cert" in tls_info
    assert "tls_version" in tls_info
    assert "cipher_suite" in tls_info


@pytest.mark.asyncio
async def test_tls_extension_does_not_modify_lifespan_scope():
    """Test that TLS extension does not add TLS info to lifespan scope."""
    received_scope = None

    async def mock_app(scope, receive, send):
        nonlocal received_scope
        received_scope = scope

    ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    middleware = TLSExtensionMiddleware(mock_app, ssl_context)

    scope = {
        "type": "lifespan",
        "asgi": {"version": "3.0"},
    }

    async def mock_receive():
        return {"type": "lifespan.startup"}

    async def mock_send(message):
        pass

    await middleware(scope, mock_receive, mock_send)

    # Lifespan scope should not have TLS extension
    assert received_scope is not None
    assert (
        "extensions" not in received_scope
        or "tls" not in received_scope.get("extensions", {})
    )


@pytest.mark.asyncio
async def test_tls_extension_preserves_existing_extensions():
    """Test that TLS extension preserves existing extensions in scope."""
    received_scope = None

    async def mock_app(scope, receive, send):
        nonlocal received_scope
        received_scope = scope

    ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    middleware = TLSExtensionMiddleware(mock_app, ssl_context)

    # Scope with existing extensions
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "path": "/",
        "query_string": b"",
        "headers": [],
        "extensions": {
            "custom_extension": {"data": "value"},
        },
    }

    async def mock_receive():
        return {"type": "http.request"}

    async def mock_send(message):
        pass

    await middleware(scope, mock_receive, mock_send)

    # Verify existing extensions are preserved
    assert received_scope is not None
    assert "extensions" in received_scope
    assert "custom_extension" in received_scope["extensions"]
    assert received_scope["extensions"]["custom_extension"] == {"data": "value"}

    # And TLS extension was added
    assert "tls" in received_scope["extensions"]


@pytest.mark.asyncio
async def test_tls_extension_with_ssl_transport():
    """Test that TLS extension extracts info from SSL transport when available."""
    received_scope = None

    async def mock_app(scope, receive, send):
        nonlocal received_scope
        received_scope = scope

    ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    middleware = TLSExtensionMiddleware(mock_app, ssl_context)

    # Mock SSL object
    mock_ssl_object = MagicMock()
    mock_ssl_object.getpeercert.return_value = None  # No peer cert in this test
    mock_ssl_object.version.return_value = "TLSv1.3"
    mock_ssl_object.cipher.return_value = (
        "TLS_AES_256_GCM_SHA384",
        "TLSv1.3",
        256,
    )

    # Mock transport with SSL object
    mock_transport = Mock()
    mock_transport.get_extra_info.return_value = mock_ssl_object

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "path": "/",
        "query_string": b"",
        "headers": [],
        "_transport": mock_transport,
    }

    async def mock_receive():
        return {"type": "http.request"}

    async def mock_send(message):
        pass

    await middleware(scope, mock_receive, mock_send)

    # Verify TLS info was extracted
    assert received_scope is not None
    assert "extensions" in received_scope
    assert "tls" in received_scope["extensions"]

    tls_info = received_scope["extensions"]["tls"]

    # Verify TLS version and cipher suite were extracted
    assert tls_info["tls_version"] == "TLSv1.3"
    assert tls_info["cipher_suite"] == "TLS_AES_256_GCM_SHA384"

    # Verify transport's get_extra_info was called
    mock_transport.get_extra_info.assert_called_once_with("ssl_object")


@pytest.mark.asyncio
async def test_tls_extension_mandatory_fields_always_present():
    """Test that mandatory TLS fields are always present in the extension."""
    received_scope = None

    async def mock_app(scope, receive, send):
        nonlocal received_scope
        received_scope = scope

    ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    middleware = TLSExtensionMiddleware(mock_app, ssl_context)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "path": "/",
        "query_string": b"",
        "headers": [],
    }

    async def mock_receive():
        return {"type": "http.request"}

    async def mock_send(message):
        pass

    await middleware(scope, mock_receive, mock_send)

    tls_info = received_scope["extensions"]["tls"]

    # According to ASGI TLS spec, these are MANDATORY fields
    mandatory_fields = ["server_cert", "tls_version", "cipher_suite"]
    for field in mandatory_fields:
        assert (
            field in tls_info
        ), f"Mandatory field '{field}' missing from TLS extension"


@pytest.mark.asyncio
async def test_tls_extension_optional_fields_have_defaults():
    """Test that optional TLS fields have appropriate default values."""
    received_scope = None

    async def mock_app(scope, receive, send):
        nonlocal received_scope
        received_scope = scope

    ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    middleware = TLSExtensionMiddleware(mock_app, ssl_context)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "path": "/",
        "query_string": b"",
        "headers": [],
    }

    async def mock_receive():
        return {"type": "http.request"}

    async def mock_send(message):
        pass

    await middleware(scope, mock_receive, mock_send)

    tls_info = received_scope["extensions"]["tls"]

    # Optional fields and their defaults according to ASGI TLS spec
    assert (
        tls_info.get("client_cert_chain") == []
    ), "client_cert_chain should default to empty list"
    assert (
        tls_info.get("client_cert_name") is None
    ), "client_cert_name should default to None"
    assert (
        tls_info.get("client_cert_error") is None
    ), "client_cert_error should default to None"


@pytest.mark.asyncio
async def test_tls_extension_extracts_client_certificate():
    """Test that TLS extension extracts client certificate info when available."""
    received_scope = None

    async def mock_app(scope, receive, send):
        nonlocal received_scope
        received_scope = scope

    ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    middleware = TLSExtensionMiddleware(mock_app, ssl_context)

    # Mock SSL object with client certificate
    mock_ssl_object = MagicMock()

    # Mock client certificate in DER format (simplified)
    mock_client_cert_der = b"MOCK_DER_CERT_DATA"
    mock_ssl_object.getpeercert.side_effect = lambda binary_form=False: (
        mock_client_cert_der
        if binary_form
        else {
            "subject": (
                (("countryName", "US"),),
                (("organizationName", "Asgiri Dev Server"),),
                (("commonName", "testclient"),),
            ),
            "issuer": ((("countryName", "US"),),),
        }
    )
    mock_ssl_object.version.return_value = "TLSv1.3"
    mock_ssl_object.cipher.return_value = (
        "TLS_AES_256_GCM_SHA384",
        "TLSv1.3",
        256,
    )

    # Mock transport with SSL object
    mock_transport = Mock()
    mock_transport.get_extra_info.return_value = mock_ssl_object

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "path": "/",
        "query_string": b"",
        "headers": [],
        "_transport": mock_transport,
    }

    async def mock_receive():
        return {"type": "http.request"}

    async def mock_send(message):
        pass

    await middleware(scope, mock_receive, mock_send)

    assert received_scope is not None
    assert "extensions" in received_scope
    assert "tls" in received_scope["extensions"]

    tls_info = received_scope["extensions"]["tls"]

    # Verify client certificate was extracted
    assert len(tls_info["client_cert_chain"]) > 0
    assert "BEGIN CERTIFICATE" in tls_info["client_cert_chain"][0]

    # Verify client cert name was extracted and formatted
    assert tls_info["client_cert_name"] is not None
    assert "commonName=testclient" in tls_info["client_cert_name"]
    assert "organizationName=Asgiri Dev Server" in tls_info["client_cert_name"]

    # No error for successful validation
    assert tls_info["client_cert_error"] is None
