import threading
import sys
import os
import argparse
from pathlib import Path


def configure_cli(argv):
    # Set import-time selection before registering the server. Otherwise its
    # CLI re-exec switches to eda_agent.server and drops this coordination
    # wrapper (including shared locks, restart and idle-release handling).
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument('--toolset')
    parser.add_argument('--backend')
    options, _ = parser.parse_known_args(argv)
    for name, value in [('EDA_AGENT_TOOLSET', options.toolset), ('EDA_AGENT_BACKEND', options.backend)]:
        if value is not None:
            os.environ[name] = value


configure_cli(sys.argv[1:])
# Prefer deployed sources to an unrelated editable package in the interpreter.
sys.path.insert(0, str(Path(__file__).resolve().parent / 'eda-agent' / 'src'))
from suite_config import configure_environment
configure_environment()
from bridge_coordination import engine_lock, background_engine_lock, ensure_eda
from eda_agent.bridge.altium_bridge import AltiumBridge

original = AltiumBridge._execute_command

def coordinated(self, command, params, timeout):
    background = threading.current_thread().name == 'altium-keepalive'
    if background:
        with background_engine_lock() as acquired:
            if not acquired:
                return {'engine_busy': True}
            if not (self.config.workspace_dir / 'bridge-ready.json').exists():
                return {'engine_released': True}
            # A stop can race the ready check. Bound this health-only wait so
            # it cannot hold the shared lock for five seconds ahead of a user.
            return original(self, command, params, min(timeout, 1.0))
    with engine_lock():
        if command != 'application.stop_server':
            health = ensure_eda(self, original)
            if command == 'application.ping' and not params:
                # The foreground preflight already answered this exact query.
                return health
        return original(self, command, params, timeout)

AltiumBridge._execute_command = coordinated
from eda_agent.server import main
if __name__ == '__main__':
    main()
