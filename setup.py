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
    "flockwave-conn[midi,serial] @ https://git.collmot.com/collmot/flockwave-conn/archive/1.10.2.tar.gz",
    "flockwave-ext @ https://git.collmot.com/collmot/flockwave-ext/archive/1.3.1.tar.gz",
    "flockwave-flockctrl @ https://git.collmot.com/collmot/flockwave-flockctrl/archive/0.9.1.tar.gz",
    "flockwave-gps @ https://git.collmot.com/collmot/flockwave-gps/archive/0.14.0.tar.gz",
    "flockwave-logger @ https://git.collmot.com/collmot/flockwave-logger/archive/1.2.0.tar.gz",
    "flockwave-parsers @ https://git.collmot.com/collmot/flockwave-parsers/archive/1.1.1.tar.gz",
    "flockwave-spec @ https://git.collmot.com/collmot/flockwave-spec/archive/0.38.0.tar.gz",
    "jsonschema>=3.0.1",
    "paramiko>=2.7.1",
    "pynmea2>=1.12.0",
    "python-baseconv>=1.1.3",
    "python-dotenv>=0.10.3",
    "pyledctrl @ https://git.collmot.com/collmot/pyledctrl/archive/3.0.1.tar.gz",
    "scp>=0.13.2",
    "sentry-sdk>=0.12.3",
    "quart>=0.10.0",
    "quart-trio>=0.4.0",
    "tinyrpc[msgpack]>=1.0.4",
    "trio>=0.16.0",
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
    extras_require={"dev": ["click-man>=0.3.0"], "radiation": ["numpy>=1.11.1"]},
    setup_requires=[],
    entry_points={"console_scripts": ["flockwaved = flockwave.server.launcher:start"]},
)
