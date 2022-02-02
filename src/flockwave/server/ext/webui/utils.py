from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from flockwave.app_framework.configurator import Configuration
    from flockwave.server.app import SkybrushServer


def get_server_configuration_as_json(
    app: "SkybrushServer", *, compact: bool = False
) -> "Configuration":
    """Returns the entire configuration of the server application as a JSON
    object. This may be used for debugging purposes if we want a full snapshot
    that contains the configuration of all the loaded extensions.

    Parameters:
        app: the server application
        compact: whether to return a compact representation that includes only
            the differences from the base configuration of the server
    """
    config: "Configuration"
    defaults: "Configuration"

    config = deepcopy(app.configurator.result)

    if compact:
        # Figure out what the defaults were
        if app.configurator.loaded_files:
            defaults = app.configurator.loaded_files[0].pre_snapshot
        else:
            defaults = deepcopy(config)
    else:
        defaults = {}

    # Extension configurations might have been modified by the user so update
    # those from the extension manager
    ext_configs = app.extension_manager.get_configuration_snapshot_dict(
        disable_unloaded=True
    )
    config["EXTENSIONS"] = ext_configs

    # If the user requested a compact representation, compare the defaults with
    # the current configuration
    if compact:
        app.configurator.minimize_configuration(config, defaults)

    return config
