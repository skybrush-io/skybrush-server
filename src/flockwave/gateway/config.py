"""Default configuration for the Skybrush gateway server.

This script will be evaluated first when the gateway attempts to load its
configuration. Configuration files may import variables from this module
with `from flockwave.gateway.config import SOMETHING`, and may also modify
them if the variables are mutable.
"""

# Port on which the gateway will be listening for incoming HTTP requests
PORT = 8080
