# List of MAVLink dialects that we want to bundle into the server
dialects = "ardupilotmega minimal standard"

hiddenimports = []
for dialect in dialects.split():
    hiddenimports.append("pymavlink.dialects.v10." + dialect)
    hiddenimports.append("pymavlink.dialects.v20." + dialect)
