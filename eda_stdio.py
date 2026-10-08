import threading
from suite_config import configure_environment
configure_environment()
from bridge_coordination import engine_lock, ensure_eda
from eda_agent.bridge.altium_bridge import AltiumBridge

original = AltiumBridge._execute_command

def coordinated(self, command, params, timeout):
    with engine_lock():
        # A keepalive must never resurrect an intentionally detached session
        # or relaunch the bridge behind a dialog. User tool calls recover it.
        background = threading.current_thread().name == 'altium-keepalive'
        if background and not (self.config.workspace_dir / 'bridge-ready.json').exists():
            # Idle release is normal, not a dead-engine fault. Do not queue a
            # ping for a stopped loop or resurrect it behind the user's UI.
            return {'engine_released': True}
        if not background and command != 'application.stop_server':
            ensure_eda(self, original)
        return original(self, command, params, timeout)

AltiumBridge._execute_command = coordinated
from eda_agent.server import main
if __name__ == '__main__':
    main()
