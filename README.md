# Skybrush Server

Skybrush Server is the server component behind the Skybrush ecosystem; it handles
communication channels to drones and provides an abstraction layer on top of them
so frontend apps (like Skybrush Live) do not need to know what type of drones
they are communicating with.

The server also provides additional facilities like clocks, RTK correction
sources, weather providers and so on. It is extensible via extension modules
that can be loaded automatically at startup or dynamically while the server is
running. In fact, most of the functionality in the server is implemented in the
form of extensions; see the `flockwave.server.ext` module in the source code
for the list of built-in extensions. You may also develop your own extensions to
extend the functionality of the server.

## Custom mapping
Custom mapping for a specific joystick can be set by modifying the following lines in the code.
https://github.com/sb-fork/skybrush-server/blob/b50bc20f05bfac0967394b6f42948e2d99956ed5/src/flockwave/server/ext/mavlink/driver.py#L886
```
The mapping is as follows:

message = spec.rc_channels_override(
   chan1_raw = (Roll)
   chan2_raw = (Pitch)
   chan3_raw = (Throttle)
   chan4_raw = (Yaw),
   chan5_raw = 0,
   chan6_raw = 0,
   chan7_raw = 0,
   chan8_raw = 0,
)
``` 


## Installation

1. Install `poetry`. `poetry` will manage a separate virtual environment for this
   project to keep things nicely separated. You won't pollute the system Python
   with the dependencies of the Skybrush server and everyone will be happier.
   See https://python-poetry.org/ for installation instructions.

2. Check out the source code of the server.

3. Run `poetry install` to install all the dependencies and the server itself
   in a separate virtualenv. The virtualenv will be created in a folder named
   `.venv` in the project folder.

4. Run `poetry run skybrushd` to start the server.

## Documentation

- [User guide](https://doc.collmot.com/public/skybrush-live-doc/latest/)

## License

Skybrush Server is free software: you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later
version.

Skybrush Server is distributed in the hope that it will be useful, but WITHOUT
ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for
more details.

You should have received a copy of the GNU General Public License along with
this program. If not, see <https://www.gnu.org/licenses/>.
