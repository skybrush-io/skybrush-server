from pkgutil import extend_path

# Declare "flockwave.server.ext" as a namespace package
__path__ = extend_path(__path__, __name__)
