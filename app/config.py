from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"
CACHE_DIR = ROOT / "cache"
OUTPUT_DIR = ROOT / "output"
KEYS_DIR = ROOT / "keys"

_DEFAULTS = {
    "wechat_data_roots": [],
    "server": {"host": "127.0.0.1", "port": 8765, "open_browser": True},
}


def load_config() -> dict:
    config = {
        "wechat_data_roots": list(_DEFAULTS["wechat_data_roots"]),
        "server": dict(_DEFAULTS["server"]),
    }
    if CONFIG_PATH.exists():
        data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
        roots = data.get("wechat_data_roots")
        if isinstance(roots, list):
            config["wechat_data_roots"] = [str(item) for item in roots]
        server = data.get("server")
        if isinstance(server, dict):
            config["server"].update(server)
    config["server"]["host"] = "127.0.0.1"
    return config
