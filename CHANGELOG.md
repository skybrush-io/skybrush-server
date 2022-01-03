# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.27.1] - 2022-01-03

### Fixed

- Fixed a bug in the RTK base station handling that sometimes resulted in an
  unhandled error message when the RTK base station was not sending valid
  messages at all.

## [1.27.0] - 2021-12-29

### Added

- Added support for beacons. Extensions may now register beacon objects in the
  server; beacons are shown on the map in clients that support them (e.g.,
  Skybrush Live from version 1.25.0).

- RTK extension now registers the position of the base station as a beacon.

- The Light control panel in Skybrush Live is now also supported for Crazyflie
  drones.

- Added a module that provides the magnetic vector for a given GPS coordinate;
  this can be used by clients to calculate the magnetic declination to show it
  on the user interface.

## [1.26.0] - 2021-12-06

### Added

- Added support for Socket.IO protocol v5 as the transport layer between
  Skybrush Live and Skybrush Server. Support for Socket.IO v4 is still kept
  until older versions of Skybrush Live that rely on Socket.IO v4 are phased
  out.

### Fixed

- Serial ports connected to common autopilots and bootloaders do not get detected
  as RTK base station candidates any more.

- In Linux and macOS, the `skybrushd` startup script now resolves relative
  configuration file names from the current directory, as expected.

## [1.25.0] - 2021-11-02

### Added

- The server can now retrieve the current value of the planetary K-index from
  various data sources. Future versions of Skybrush Live will make use of this
  facility to show the Kp-index on the user interface.

### Fixed

- Fixed a trajectory encoding bug for Crazyflie drones when a segment of
  a trajectory was described with a 7-degree polynomial.

## [1.24.2] - 2021-09-03

### Fixed

- The `go` command for Crazyflie drones now plans a trajectory that is always
  at least 1 second long.

## [1.24.0] - 2021-08-21

### Added

- Safety fence distance and safety action for Crazyflies is now configurable in
  the server settings.

- RTK base stations can now be configured to a fixed coordinate in ECEF (Earth
  centered Earth fixed).

### Fixed

- Fixed a bug with the initialization sequence after re-connection to
  a Crazyflie drone after a temporary loss of connection (e.g., a reboot)

## [1.23.0] - 2021-08-13

### Added

- Safety fence is now automatically set up for Crazyflie drones with a 1m
  safety distance from the axis-aligned bounding box of the trajectory.

### Fixed

- Fixed spurious log messages when a Crazyflie radio is unplugged from the USB
  port while the server is running.

## [1.22.0] - 2021-08-09

### Added

- The interval between consecutive status information messages can now be
  configured in the Crazyflie extension, allowing you to track the Crazyflie
  more precisely from the server if needed.

## [1.21.2] - 2021-08-06

### Added

- The OSC extension now sends whether a given drone has been seen recently by
  the server or not, allowing one to ignore drones that have probably been
  turned off.

## [1.21.1] - 2021-07-20

### Fixed

- macOS executable is now signed using an ad-hoc signature to allow execution
  on Apple Silicon. Note that Apple Silicon is not supported officially yet;
  this is the first step towards full native support.

## [1.21.0] - 2021-07-19

### Added

- The server now provides an optional OSC extension that allows one to forward
  the (geodetic or local) positions of the drones to an OSC server for further
  processing.

## [1.20.0] - 2021-07-14

### Added

- Crazyflie drones can now be armed or disarmed from the server with the
  standard arming commands.

### Fixed

- Restored compatibility with Python 3.7.

## [1.19.0] - 2021-07-12

### Added

- The server now attempts to re-connect to an RTK base station if the base
  station disappeared briefly for at most 30 seconds while it was being used.
  This helps to recover automatically in cases when the RTK base station was
  plugged into an unpowered USB hub and the device disappeared briefly from the
  OS due to power issues on the USB port.

- The RTK extension of the server can now be configured to send RTK corrections
  only for a subset of all supported GNSS types; for instance, one can now
  configure the extension to send corrections for GPS and GLONASS only, saving
  bandwidth in geographical areas where BeiDou is not relevant.

### Changed

- The server now requires an RTK base station to broadcast its own antenna
  position at least once every 30 seconds, otherwise the last antenna position
  will be invalidated.

## [1.18.0] - 2021-05-10

This is the release that serves as a basis for changelog entries above. Refer
to the commit logs for changes affecting this version and earlier versions.
