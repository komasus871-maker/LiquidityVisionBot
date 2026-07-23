import hashlib
import hmac
from urllib.parse import urlencode


def test_bingx_signature_uses_exact_wire_order():
    secret = "secret"
    params = [("timestamp", 1700000000000), ("recvWindow", 5000)]
    query = urlencode(params)
    signature = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    wire = urlencode(params + [("signature", signature)])
    assert wire.startswith("timestamp=1700000000000&recvWindow=5000&signature=")
    assert signature == hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
