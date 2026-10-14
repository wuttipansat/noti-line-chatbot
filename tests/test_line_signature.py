from app.line.signature import compute_signature, is_valid_signature

SECRET = "test-channel-secret"
BODY = b'{"destination":"U0","events":[]}'

def test_valid_signature_passes():
    signature = compute_signature(SECRET, BODY)

    assert is_valid_signature(SECRET, BODY, signature)

def test_known_value():
    expected = "DPAG63PPtgrPymD3vuZfdXRn2FAFcXXzYktwi1/304A=" #OpenSSL
    assert compute_signature(SECRET, b"hello") == expected

def test_changed_body_fails():
    signature = compute_signature(SECRET, BODY)
    tampered = BODY.replace(b"[]", b"[ ]")

    assert not is_valid_signature(SECRET, tampered, signature)

def test_wrong_secret_fails():
    signature = compute_signature("someone-else", BODY)

    assert not is_valid_signature(SECRET, BODY, signature)

def test_missing_signature_fails():
    assert not is_valid_signature(SECRET, BODY, None)
    assert not is_valid_signature(SECRET, BODY, "")
