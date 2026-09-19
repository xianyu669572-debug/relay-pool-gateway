"""命令行入口：python -m relay_gateway --config config.toml"""

from __future__ import annotations

import argparse
import signal
import sys
import threading
import time
from pathlib import Path

from .config import load_config
from .server import RelayPoolServer


def _serve(config_path: str, host: str | None, port: int | None, reload: bool) -> None:
    config = load_config(config_path)
    cfg_host = host or config.server.host
    cfg_port = port or config.server.port
    server = RelayPoolServer((cfg_host, cfg_port), __import__("relay_gateway.server", fromlist=["Handler"]).Handler, config)
    server.health.start()
    print(f"[relay-pool-gateway] listening on http://{cfg_host}:{cfg_port} (privacy mode={config.privacy.mode})", flush=True)

    mtime = Path(config_path).stat().st_mtime

    def reload_watcher() -> None:
        nonlocal mtime
        while True:
            time.sleep(2)
            try:
                new_mtime = Path(config_path).stat().st_mtime
                if new_mtime != mtime:
                    mtime = new_mtime
                    try:
                        new_config = load_config(config_path)
                        server.reload(new_config)
                        print(f"[relay-pool-gateway] config reloaded: {config_path}", flush=True)
                    except Exception as exc:
                        print(f"[relay-pool-gateway] reload failed: {exc}", flush=True)
            except Exception:
                pass

    if reload:
        threading.Thread(target=reload_watcher, daemon=True).start()

    def stop(signum, frame):  # noqa: ANN001
        server.health.stop()
        server.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        server.serve_forever()
    finally:
        server.health.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="多中转 API 聚合网关（零依赖）")
    parser.add_argument("--config", help="TOML 配置文件路径")
    parser.add_argument("--host", default=None, help="监听地址（覆盖配置）")
    parser.add_argument("--port", type=int, default=None, help="监听端口（覆盖配置）")
    parser.add_argument("--reload", action="store_true", help="配置文件变更自动热加载")
    parser.add_argument("--version", action="store_true", help="显示版本号")
    args = parser.parse_args()
    if args.version:
        from . import __version__

        print(f"relay-pool-gateway {__version__}")
        return
    if not args.config:
        parser.error("--config 是必需的（或使用 --version）")
    _serve(args.config, args.host, args.port, args.reload)


if __name__ == "__main__":
    main()
