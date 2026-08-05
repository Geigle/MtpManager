"""python -m mtpmanager"""

from __future__ import annotations

import logging
import sys


def main(argv: list[str] | None = None) -> int:
    from mtpmanager.infra.logging_setup import configure_logging, prune_old_logs

    log_dir = configure_logging()
    removed = prune_old_logs(log_dir)
    log = logging.getLogger("mtpmanager")
    log.info("Logging to %s", log_dir.resolve())
    if removed:
        log.info("Pruned %d stale log file(s) from %s", removed, log_dir)

    # Ensure project root is importable when run as script path variants
    from mtpmanager.infra.pymtp_device import PymtpDevice
    from mtpmanager.ui.controllers import AppController
    from mtpmanager.ui.window import MainWindow

    window = MainWindow()
    device = PymtpDevice()
    controller = AppController(window, device)
    try:
        window.mainloop()
    finally:
        # Belt-and-suspenders if the mainloop ends without WM_DELETE_WINDOW
        # (e.g. some platforms destroy the root first).
        try:
            controller.shutdown_device_session()
        except Exception:
            log.debug("device session shutdown after mainloop failed", exc_info=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
