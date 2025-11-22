# Security Test Suite

This directory contains comprehensive security tests for Asgiri. These tests are marked as `@pytest.mark.slow` and `@pytest.mark.security` and are **excluded from pre-commit checks** by default due to their longer execution time.

## Running Security Tests

### Run all security tests:
```bash
pytest tests/test_security.py -v
```

### Run tests by category:
```bash
# HTTP/1.1 security tests only
pytest tests/test_security.py -k "http11" -v

# HTTP/2 security tests only
pytest tests/test_security.py -k "http2" -v

# TLS security tests only
pytest tests/test_security.py -k "tls" -v

# DoS tests only
pytest tests/test_security.py -k "exhaustion or flood or slowloris" -v
```

### Run with markers:
```bash
# All slow tests (includes security)
pytest tests -m slow -v

# Only security tests
pytest tests -m security -v

# All tests except slow ones (default for pre-commit)
pytest tests -m "not slow" -v
```

## Test Categories

### HTTP/1.1 Security Tests
- **CRLF Injection**: Tests header injection via CRLF characters
- **Request Smuggling**: CL.TE attack vector testing
- **Slowloris Attack**: Slow header DoS protection
- **Large Headers**: Excessive header size handling
- **Null Byte Injection**: Path traversal via null bytes
- **Excessive URL Length**: Long URL DoS vector

### HTTP/2 Security Tests
- **Rapid Stream Creation**: Stream exhaustion DoS
- **Settings Flood**: SETTINGS frame flood attack
- **Priority Tree Manipulation**: Complex dependency CPU exhaustion

### TLS/SSL Security Tests
- **Cipher Suite Security**: Verification of secure ciphers only
- **TLS Version Enforcement**: Ensuring TLS 1.2+ requirement

### Input Validation Tests
- **Unicode Normalization**: Unicode-based path traversal
- **Null Byte Handling**: Null byte injection in various contexts

### Information Disclosure Tests
- **Error Messages**: Ensuring stack traces aren't exposed
- **Server Headers**: Minimal version information disclosure

### WebSocket Security Tests
- **Message Size Limits**: Large message DoS protection (placeholder)
- **Origin Validation**: CORS-like origin checking (placeholder)

### Denial of Service Tests
- **Connection Exhaustion**: Handling of many simultaneous connections
- **Request Rate Limiting**: Server stability under request floods

## Test Configuration

The pytest configuration in `pyproject.toml` includes:

```toml
[tool.pytest.ini_options]
markers = [
    "slow: marks tests as slow (deselect with '-m \"not slow\"')",
    "security: marks security-focused tests",
]
```

The pre-commit hook in `scripts/pre_commit.py` automatically excludes slow tests:

```python
pytest tests -m "not slow" --tb=short
```

## Adding New Security Tests

When adding new security tests:

1. **Mark as slow and security**:
   ```python
   @pytest.mark.slow
   @pytest.mark.security
   @pytest.mark.timeout(10)  # Add appropriate timeout
   @pytest.mark.asyncio
   async def test_new_security_feature(unused_port: int):
       """Test description."""
       pass
   ```

2. **Document the attack vector** in the docstring

3. **Use appropriate timeouts** (most security tests need 10-20s)

4. **Clean up resources** in finally blocks

5. **Test both rejection and safe handling** - server may either reject malicious input or handle it safely

## Common Fixtures

- **unused_port**: Provides an available port for testing
- **wait_for_server(host, port, timeout)**: Waits for server to be ready

## Expected Test Behavior

Security tests verify that Asgiri:

1. **Rejects** malicious input (returns 4xx/5xx errors)
2. **Handles** edge cases without crashing
3. **Limits** resource consumption under attack
4. **Doesn't leak** sensitive information in errors
5. **Enforces** security policies (TLS versions, cipher suites, etc.)

## CI/CD Integration

For CI/CD pipelines, you may want to:

```bash
# Run fast tests only (default)
pytest tests -m "not slow"

# Run all tests including slow security tests (nightly/weekly)
pytest tests

# Run only security tests (security-focused pipeline)
pytest tests -m security
```

## Performance Considerations

Security tests are marked as slow because they:

- Open many connections simultaneously
- Send large amounts of data
- Test timeout/delay scenarios
- Perform cryptographic operations
- Execute protocol-level attacks

Typical execution time: **2-5 minutes** for the full security suite.

## Future Enhancements

Planned additional tests:

- [ ] HTTP/3 QUIC-specific security tests
- [ ] HPACK/QPACK compression bomb tests
- [ ] Complete WebSocket security suite
- [ ] Fuzzing integration
- [ ] Memory leak detection under attack
- [ ] Certificate validation chain tests
