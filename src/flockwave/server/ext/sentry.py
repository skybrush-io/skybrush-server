"""Extension that logs unhandled server exceptions to Sentry.io"""

import os
import sentry_sdk


def load(app, configuration, logger):
    dsn = os.environ.get("SENTRY_DSN") or configuration.get("dsn")
    if not dsn:
        logger.warn("Sentry DSN not specified; Sentry integration disabled.")
        logger.info(
            "Set your Sentry DSN in the 'dsn' key of the extension "
            "configuration or in the SENTRY_DSN environment variable."
        )
        return

    sentry_sdk.init(dsn)
