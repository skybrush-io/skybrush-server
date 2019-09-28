from pkgutil import extend_path

# Declare "flockwave" as a namespace package
__path__ = extend_path(__path__, __name__)
