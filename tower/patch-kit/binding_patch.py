#!/usr/bin/env python3
"""Patch the HAOS petlibro_local addon: reply to fw 3.1.54 BINDING publishes.
Mirrors the proven installer patch9 (cmd/msgId/code echo on system/sub)."""
from pathlib import Path
import shutil
import tempfile

F = Path("/opt/petlibro-local/appdaemon/mqtt_client.py")
anchor = """        if command == Commands.DEVICE_LOG_REPORT_EVENT:
            self.logger.trace("ignored device log report")
            return"""
addition = anchor + """

        # LOCAL BINDING PATCH: fw 3.1.54 sends BINDING (system/post) during its
        # boot/setup flow and expects the standard cmd/msgId/code echo on
        # system/sub before it emits DEVICE_START_EVENT (which carries the
        # cameraAuthInfo the camera pipeline needs).
        if command == "BINDING":
            import time as _time_bp
            try:
                reply = {
                    "cmd": "BINDING",
                    "msgId": payload.get("msgId"),
                    "code": 0,
                    "ts": int(_time_bp.time() * 1000),
                }
                self.mqtt.mqtt_publish(
                    self.message_topics.sub("system"),
                    json.dumps(reply),
                    namespace="mqtt",
                )
                self.logger.info("replied to feeder BINDING", msg_id=payload.get("msgId"))
            except Exception as error:
                self.logger.warning(
                    "BINDING reply failed", exception_type=type(error).__name__
                )
            return"""

def patch(path=F):
    src = path.read_text()
    if "LOCAL BINDING PATCH" in src:
        compile(src, str(path), "exec")
        print("already applied")
        return
    if src.count(anchor) != 1:
        raise RuntimeError("ERROR: expected exactly one anchor")
    src = src.replace(anchor, addition, 1)
    compile(src, str(path), "exec")
    shutil.copy2(path, str(path) + ".pre-binding-patch")
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as f:
        temporary = Path(f.name)
        try:
            f.write(src)
            f.flush()
            shutil.copymode(path, temporary)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    print("applied + compiles OK")


if __name__ == "__main__":
    patch()
