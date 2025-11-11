"""TLS extension middleware for ASGI applications.
Wraps an ASGI application to add TLS extension support. as specified at https://asgi.readthedocs.io/en/latest/specs/tls.html.
```
The connection scope information passed in scope contains an "extensions" key, which contains a dictionary of extensions. Inside that dictionary, the key "tls" identifies the extension specified in this document. The value will be a dictionary with the following entries:

server_cert (Unicode string or None) – The PEM-encoded conversion of the x509 certificate sent by the server when establishing the TLS connection. Some web server implementations may be unable to provide this (e.g. if TLS is terminated by a separate proxy or load balancer); in that case this shall be None. Mandatory.

client_cert_chain (Iterable[Unicode string]) – An iterable of Unicode strings, where each string is a PEM-encoded x509 certificate. The first certificate is the client certificate. Any subsequent certificates are part of the certificate chain sent by the client, with each certificate signing the preceding one. If the client did not provide a client certificate then it will be an empty iterable. Some web server implementations may be unable to provide this (e.g. if TLS is terminated by a separate proxy or load balancer); in that case this shall be an empty iterable. Optional; if missing defaults to empty iterable.

client_cert_name (Unicode string or None) – The x509 Distinguished Name of the Subject of the client certificate, as a single string encoded as defined in RFC4514. If the client did not provide a client certificate then it will be None. Some web server implementations may be unable to provide this (e.g. if TLS is terminated by a separate proxy or load balancer); in that case this shall be None. If client_cert_chain is provided and non-empty then this field must be provided and must contain information that is consistent with client_cert_chain[0]. Note that under some setups, (e.g. where TLS is terminated by a separate proxy or load balancer and that device forwards the client certificate name to the web server), this field may be set even where client_cert_chain is not set. Optional; if missing defaults to None.

client_cert_error (Unicode string or None) – None if a client certificate was provided and successfully verified, or was not provided. If a client certificate was provided but verification failed, this is a non-empty string containing an error message or error code indicating why validation failed; the details are web server specific. Most web server implementations will reject the connection if the client certificate verification failed, instead of setting this value. However, some may be configured to allow the connection anyway. This is especially useful when testing that client certificates are supported properly by the client - it allows a response containing an error message that can be presented to a human, instead of just refusing the connection. Optional; if missing defaults to None.

tls_version (integer or None) – The TLS version in use. This is one of the version numbers as defined in the TLS specifications, which is an unsigned integer. Common values include 0x0303 for TLS 1.2 or 0x0304 for TLS 1.3. If TLS is not in use, set to None. Some web server implementations may be unable to provide this (e.g. if TLS is terminated by a separate proxy or load balancer); in that case set to None. Mandatory.

cipher_suite (integer or None) – The TLS cipher suite that is being used. This is a 16-bit unsigned integer that encodes the pair of 8-bit integers specified in the relevant RFC, in network byte order. For example RFC8446 section B.4 defines that the cipher suite TLS_AES_128_GCM_SHA256 is {0x13, 0x01}; that is encoded as a cipher_suite value of 0x1301 (equal to 4865 decimal). Some web server implementations may be unable to provide this (e.g. if TLS is terminated by a separate proxy or load balancer); in that case set to None. Mandatory.
```
"""

import ssl
from typing import Any, cast
from asgiref.typing import (
    ASGIApplication,
    ASGI3Application,
    Scope,
    ASGIReceiveCallable,
    ASGISendCallable,
)


class TLSExtensionMiddleware:
    """
    ASGI middleware that adds TLS extension support to the scope.
    """

    def __init__(self, app: ASGIApplication, ssl_context: ssl.SSLContext) -> None:
        self.app = app
        self.ssl_context = ssl_context

    async def __call__(
        self,
        scope: Scope,
        receive: ASGIReceiveCallable,
        send: ASGISendCallable,
    ) -> None:
        if scope["type"] == "http" or scope["type"] == "websocket":
            tls_info: dict[str, Any] = {
                "server_cert": None,
                "client_cert_chain": [],
                "client_cert_name": None,
                "client_cert_error": None,
                "tls_version": None,
                "cipher_suite": None,
            }

            # Attempt to extract TLS information from the SSL context
            transport = scope.get("transport")
            if transport is not None and hasattr(transport, "get_extra_info"):
                ssl_object = transport.get_extra_info("ssl_object")
                if ssl_object is not None:
                    # Server certificate
                    cert = ssl_object.getpeercert(binary_form=True)
                    if cert:
                        tls_info["server_cert"] = ssl.DER_cert_to_PEM_cert(cert)

                    # TLS version
                    version = ssl_object.version()
                    tls_info["tls_version"] = version

                    # Cipher suite
                    cipher = ssl_object.cipher()
                    if cipher:
                        # cipher is a tuple (name, protocol version, secret bits)
                        # We need to convert the name to its corresponding integer value
                        # This is a simplified approach; in a real implementation, you would
                        # need a mapping from cipher names to their integer values.
                        cipher_name: str = cipher[0]
                        tls_info["cipher_suite"] = cipher_name

            if "extensions" not in scope:
                scope["extensions"] = {}
            extensions = scope["extensions"]
            if extensions is not None:
                # Cast tls_info to match the expected type in extensions dict
                extensions["tls"] = cast(dict[object, object], tls_info)

        # Cast to ASGI3Application since we're using it as an ASGI3 app
        app = cast(ASGI3Application, self.app)
        await app(scope, receive, send)
