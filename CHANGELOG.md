# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
