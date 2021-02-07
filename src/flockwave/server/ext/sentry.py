"""Extension that logs unhandled server exceptions to Sentry.io"""

import os
import sentry_sdk

from flockwave.server.version import __version__
from sentry_sdk.integrations.logging import ignore_logger


#: Number of times the extension has been loaded already; used to detect the
#: first startup and send an event
load_count = 0


def on_before_sending_log_event(event, hint):
    """Filter function that ignores certain events that we do not want to end
    up in the error logs on the server.
    """
    extra = event.get("extra")
    if extra and isinstance(extra, dict) and extra.get("sentry_ignore"):
        return None

    return event


def load(app, configuration, logger):
    global load_count

    dsn = os.environ.get("SENTRY_DSN") or configuration.get("dsn")
    if dsn is None:
        logger.warn("Sentry DSN not specified; Sentry integration disabled.")
        logger.info(
            "Set your Sentry DSN in the 'dsn' key of the extension "
            "configuration or in the SENTRY_DSN environment variable."
        )

        load_count += 1

        return

    get_license = app.import_api("license").get_license

    if callable(get_license):
        license = get_license()

    if not load_count:
        init_sentry(dsn, license)
        load_count += 1


def init_sentry(dsn: str, license) -> None:
    # Don't log events from urllib3.connectionpool -- otherwise we would get
    # warnings about Sentry's failed attempts to submit an event when the
    # network is down
    ignore_logger("urllib3.connectionpool")

    sentry_sdk.init(
        dsn,
        before_send=on_before_sending_log_event,
        release=f"skybrushd@{__version__}",
    )
    if license is not None:
        sentry_sdk.set_user(
            {
                "id": license.get_id(),
                "username": license.get_licensee(),
                "ip_address": "{{auto}}",
            }
        )

    sentry_sdk.capture_message("Application started")


dependencies = ("license",)
