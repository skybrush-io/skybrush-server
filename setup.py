"""Setup script for the Flockwave server."""

from setuptools import setup, find_packages

requires = [
    "bitstring>=3.1.3",
    "blinker>=1.4",
    "click>=6.2",
    "colorama>=0.3.5",
    "colorlog>=2.6.0",
    "enum34>=1.1.2",
    "eventlet>=0.17.4",
    "Flask>=0.10.1",
    "flask-socketio>=1.0",
    "flockwave-spec>=0.4.0",
    "jsonschema>=2.5.1",
    "pyserial>=2.7",
    "python-socketio>=0.6.1",
    "pytz>=2015.7"
]

__version__ = None
exec(open("flockwave/server/version.py").read())

setup(
    name="flockwave-server",
    version=__version__,
    packages=find_packages(),
    include_package_data=True,
    install_requires=requires,
    entry_points="""
    [console_scripts]
    flockwave-server=flockwave.server.launcher:start
    """
)
