"""JSON helper that prefers orjson, falls back to stdlib json."""
from __future__ import annotations

try:
    import orjson as _orjson

    def dumps(obj) -> bytes:
        return _orjson.dumps(obj)

    def loads(data):
        return _orjson.loads(data)

    OPT_INDENT_2 = getattr(_orjson, "OPT_INDENT_2", 0)

    def dumps_pretty(obj) -> bytes:
        return _orjson.dumps(obj, option=OPT_INDENT_2)

except ImportError:
    import json as _json

    def dumps(obj) -> bytes:
        return _json.dumps(obj, default=str).encode()

    def loads(data):
        if isinstance(data, (bytes, bytearray)):
            data = data.decode()
        return _json.loads(data)

    def dumps_pretty(obj) -> bytes:
        return _json.dumps(obj, indent=2, default=str).encode()
