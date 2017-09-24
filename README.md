# FlockWave Backend Server Installation Guide

## Windows
_Tested under Python 2.7.11 with pip 8.1.2_

- (Optional) - Set up a virtual environment to avoid cluttering of packages:
  - Install virtualenv: `pip install virtualenv`
  - Create an environment: `virtualenv server_env`
  - Activate it: `server_env\Scripts\activate`
- Clone the repository: `git clone git@biolfiz1.elte.hu:flockwave-server.git`
- Install the requirements:
  - Change to the directory: `cd flockwave-server`
  - Let pip install the packages: `pip install -r requirements.txt`
- Get `MarkupSafe`:
  - Download the appropriate precompiled binary version from [here](http://www.lfd.uci.edu/~gohlke/pythonlibs/#markupsafe)
  - Install the wheel from the command line: `pip install <path_to_file>.whl`
- You can now run the server in one of the following modes:
  - No HTTPS: `flockwave-server` -> Access it at `http://localhost:5000/`
  - With HTTPS: `flockwave-server --ssl-cert etc/ssl/cert.pem` -> Access it at `https://localhost:5000/`
  - Publicly on the network with HTTPS: `flockwave-server --host 0.0.0.0 --ssl-cert flockwave-server\etc\ssl\cert.pem`
- If you are running the server with SSL, you need to visit the url of the debug screen (`https://localhost:5000/debug/` by default) and force the browser to accept the certificate before you can accept it through websocket from the client.
- Using an xBee receiver:
  - I didn't need to install any additional software, on Windows 10 it worked automatically, but you may need to get the drivers from [here](http://www.ftdichip.com/FTDrivers.htm)
  - After making sure that the peripheral shows up in the Device Manager look for the COM port number it was registered to under the Ports section.
  - Edit the configuration file located at `flockwave-server\flockwave\server\config.py`:
    - Disable the fake drones: set `EXTENSIONS -> fake_uavs -> count` to `0`
    - Define the communication port: set `EXTENSIONS -> flockctrl -> connection` to `serial:COM<x>` where `<x>` is the port found in the device manager.
