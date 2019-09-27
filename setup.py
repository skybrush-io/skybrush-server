"""Setup script for the Flockwave server."""

from setuptools import setup, find_packages

# TODO(ntamas): update this!
requires = [
    "bidict>=0.11.0",
    "bitstring>=3.1.3",
    "blinker>=1.4",
    "click>=6.2",
    "colorama>=0.3.5",
    "colorlog>=2.6.0",
    "enum-compat>=0.0.2",
    "eventlet>=0.18.4",
    "flockwave-gps>=0.8.0",
    "flockwave-spec>=0.18.0",
    "future>=0.16.0",
    "hexdump>=3.3",
    "ipaddress>=1.0.17",
    "jsonschema>=3.0.1",
    "memoized>=0.2.1",
    "mido>=1.1.14",
    "netifaces>=0.10.5",
    "numpy>=1.11.1",
    "pynmea2>=1.12.0",
    "pyserial>=3.0",
    "python-baseconv>=1.1.3",
    "pytz>=2015.7",
    "six>=1.10.0",
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
    """,
)
