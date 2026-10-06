# Workspace Codex Integration

This directory contains the Codex plugin/skill bundle for Workspace.

## User Flow

1. Open Workspace Settings > Integrations.
2. Add a Codex Agent.
3. Copy the full setup commands shown after the generated token.
4. Toggle the tools Codex is allowed to use.
5. Configure the terminal Codex session:

```bash
export WORKSPACE_URL=http://your-workspace-host:7000
export WORKSPACE_API_TOKEN=ody_generated_token
mkdir -p ~/plugins
curl -fsSL -H "Authorization: Bearer $WORKSPACE_API_TOKEN" "$WORKSPACE_URL/api/codex/plugin.zip" -o /tmp/workspace-codex-plugin.zip
python3 -m zipfile -e /tmp/workspace-codex-plugin.zip ~/plugins
python3 - <<'PY'
import json
from pathlib import Path

p = Path.home() / ".agents" / "plugins" / "marketplace.json"
p.parent.mkdir(parents=True, exist_ok=True)
if p.exists():
    data = json.loads(p.read_text())
else:
    data = {"name": "personal", "interface": {"displayName": "Personal"}, "plugins": []}

data.setdefault("name", "personal")
data.setdefault("interface", {}).setdefault("displayName", "Personal")
plugins = data.setdefault("plugins", [])
entry = {
    "name": "workspace",
    "source": {"source": "local", "path": "./plugins/workspace"},
    "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
    "category": "Productivity",
}
data["plugins"] = [item for item in plugins if item.get("name") != "workspace"] + [entry]
p.write_text(json.dumps(data, indent=2) + "\n")
PY
codex plugin add workspace@personal
```

6. Verify:

```bash
python3 ~/plugins/workspace/scripts/workspace_api.py capabilities
```

Codex must use `/api/codex/*` endpoints. SSH, Docker, direct Python imports, database queries, and MCP internals bypass Workspace Settings and must not be used for user data access.
