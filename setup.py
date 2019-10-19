"""Setup script for the Flockwave server."""

from glob import glob
from os.path import basename, splitext
from setuptools import setup, find_packages


requires = [
    "attrs>=19.1.0",
    "bidict>=0.11.0",
    "bitstring>=3.1.3",
    "blinker>=1.4",
    "click>=6.2",
    "colorama>=0.3.5",
    "colorlog>=2.6.0",
    "flockwave-conn[midi,serial] @ https://git.collmot.com/collmot/flockwave-conn/archive/1.2.0.tar.gz",
    "flockwave-gps @ https://git.collmot.com/collmot/flockwave-gps/archive/0.9.0.tar.gz",
    "flockwave-logger @ https://git.collmot.com/collmot/flockwave-logger/archive/1.0.0.tar.gz",
    "flockwave-spec @ https://git.collmot.com/collmot/flockwave-spec/archive/0.19.1.tar.gz",
    "hexdump>=3.3",
    "jsonschema>=3.0.1",
    "netifaces>=0.10.5",
    "numpy>=1.11.1",
    "pynmea2>=1.12.0",
    "python-baseconv>=1.1.3",
    "python-dotenv>=0.10.3",
    "pytz>=2015.7",
    "sentry-sdk>=0.12.3",
    "six>=1.10.0",
    "quart>=0.10.0",
    "quart-trio>=0.4.0",
    "trio>=0.12.1",
    "trio-util>=0.1.0",
    "zeroconf>=0.23.0",
]

__version__ = None
exec(open("src/flockwave/server/version.py").read())

setup(
    name="flockwave-server",
    version=__version__,
    packages=find_packages("src"),
    package_dir={"": "src"},
    py_modules=[
        splitext(basename(path))[0]
        for path in glob("src/*.py")
        if not path.endswith("conftest.py")
    ],
    include_package_data=True,
    python_requires=">=3.7",
    install_requires=requires,
    extras_require={},
    setup_requires=["pytest-runner"],
    entry_points={"console_scripts": ["flockwaved = flockwave.server.launcher:start"]},
)
