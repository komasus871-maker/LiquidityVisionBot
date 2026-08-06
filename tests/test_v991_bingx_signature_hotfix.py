import hashlib
import hmac
from urllib.parse import urlencode

from services.exchanges.bingx_swap import BingXSwapAdapter


def test_bingx_signature_uses_sorted_exact_wire_order():
    secret = "secret"
    query, signature = BingXSwapAdapter.sign_params(
        {"timestamp": 1700000000000, "recvWindow": 5000}, secret)
    wire = f"{query}&signature={signature}"
    assert wire.startswith("recvWindow=5000&timestamp=1700000000000&signature=")
    assert signature == hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
