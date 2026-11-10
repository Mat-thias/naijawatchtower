"""ASGI entry point: `uvicorn naijawatchtower.api.main:app`.

Environment: NWT_DB (database URL), VLM_BACKEND (cache | local), SERVER_SECRET, USSD_SECRET,
OBSERVER_INVITE_CODE, NWT_DEMO_MODE (1 enables demo quick sign-in).
"""

import logging

from naijawatchtower.api.app import create_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = create_app()
