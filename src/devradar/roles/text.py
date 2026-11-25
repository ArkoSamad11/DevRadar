import re

ROLES = ("software", "ml_ai", "data_science", "systems", "security", "quant")

# Keeps terms such as c++, c#, .net, node.js and the single letters c and r.
TOKEN = re.compile(r"\.net\b|[a-z][a-z0-9]*(?:[.+#][a-z0-9+#]*[a-z0-9+#]|[+#]+)?")
MAX_TOKEN_LENGTH = 30


def tokenize(text: str) -> list[str]:
    return [t for t in TOKEN.findall(text.lower()) if len(t) <= MAX_TOKEN_LENGTH]
