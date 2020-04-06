"""Default configuration for the Skybrush gateway server.

This script will be evaluated first when the gateway attempts to load its
configuration. Configuration files may import variables from this module
with `from flockwave.gateway.config import SOMETHING`, and may also modify
them if the variables are mutable.
"""

# IP address on which the gateway will be listening for incoming HTTP requests
HOST = "127.0.0.1"

# Port on which the gateway will be listening for incoming HTTP requests
PORT = 8082

# Maximum number of workers to launch at the same time
MAX_WORKERS = 4

# Secret key used for JWT tokens sent to the gateway when someone wants to
# spin up a new worker. Only tokens signed with this key will be accepted.
JWT_SECRET = b"bhu8nji9"
