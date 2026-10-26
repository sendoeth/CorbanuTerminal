"""Read `cargo test --no-run --message-format=json` from stdin; print the codex-login lib test executable."""
import json
import sys

hits = []
for line in sys.stdin:
    try:
        msg = json.loads(line)
    except ValueError:
        continue
    if (msg.get("reason") == "compiler-artifact" and msg.get("executable")
            and msg.get("target", {}).get("name") in ("codex_login", "codex-login")
            and msg.get("profile", {}).get("test")):
        hits.append(msg["executable"])
if len(hits) != 1:
    sys.exit(f"expected one codex-login test executable, got {hits}")
print(hits[0].replace("\\", "/"))
