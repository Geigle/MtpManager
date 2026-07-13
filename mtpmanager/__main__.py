"""python -m mtpmanager"""

from __future__ import annotations

import logging
import sys


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.DEBUG)
    # Ensure project root is importable when run as script path variants
    from mtpmanager.infra.pymtp_device import PymtpDevice
    from mtpmanager.ui.controllers import AppController
    from mtpmanager.ui.window import MainWindow

    window = MainWindow()
    device = PymtpDevice()
    AppController(window, device)
    window.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
