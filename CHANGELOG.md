# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [master]

### Added

- The MAVLink extension now exports a function that can be used to get a handle
  to a MAVLink network object from another extension. This will be used by the
  Sidekick extension from this version onwards to provide a more accurate
  mapping from system IDs to UAV IDs in case of more than 250 drones.

- The sizes of MAVLink networks managed by the MAVLink extension can now be
  limited. The default limit is 250 drones per network, from system ID 1 to
  system ID 250 (inclusive). This is identical to the typical setup used in
  drone shows. If you use drones with system IDs larger than 250, you must
  adjust the network size accordingly in the configuration file.

### Fixed

- Fixed a bug in the MAVLink geofence upload protocol that sometimes reported a
  successful geofence upload when the last packet in the upload sequence was
  received out-of-order by the UAV.

## [2.35.0] - 2025-09-04

### Added

- Added support for bulk parameter uploads with the `PRM-SET-MANY` command in
  the protocol. UAV drivers that do not implement bulk parameter uploads will
  fall back to single uploads in alphabetical order of the parameters.

- The `mavlink` extension now supports bulk parameter uploads for ArduPilot
  via MAVFTP. This needs to be turned on explicitly for the time being because
  ArduPilot does not signal parameter upload failures correctly to the GCS when
  using bulk uploads. The feature will be enabled permanently when the issue is
  fixed in ArduPilot itself.

## [2.34.0] - 2025-08-22

### Added

- Added `skynet`, an extension that provides support for high-performance
  MAVLink parsing and routing to allow the server to scale up to 5000 drones
  and beyond. This extension is currently in an experimental phase and no
  configuration or documentation is provided yet; let us know if you own a large
  fleet and you are interested in testing the extension at a large scale and
  we will help you set things up.

### Changed

- Started implementation of a more compact telemetry format for MAVLink links.

- Weather providers can now have an associated priority when they are registered.

- The takeoff time configuration and authorization logic is now separated from
  the `mavlink` extension so it became reusable in other extensions that provide
  support for a certain type of drone.

### Fixed

- `uv sync` should now work consistently out-of-the-box after the repository is
  checked out, even if the user has no access to private components of the
  server.

## [2.33.1] - 2025-08-15

### Fixed

- Improved resilience of geofence upload process to cases when packets are
  arriving delayed or out-of-order on the drone.

## [2.33.0] - 2025-08-11

### Added

- Added configuration options to the MAVLink extension to skip the initial
  autopilot version discovery handshake and the configuration of MAVLink
  stream rates if needed. These options can be used to achieve faster
  initialization for large swarms consisting of thosands of drones.

- Added a "Version info" page to the web UI that can be used to discover the
  version number of all Python packages that the server depends on.

### Changed

- `UAV-INF` and `SYS-MSG` messages are now rate-limited at 5 Hz to reduce traffic
  between Live and the server.

- MAVFTP file uploads now use an adaptive timeout that tries to estimate the
  round-trip time of the connection with an algorithm similar to how the TCP
  protocol deals with it.

- When logging messages from the MAVLink module, use the drone ID if it is
  available instead of the MAVLink network and system ID.

### Fixed

- Fixed a bug in the pyro event encoder function of the show file encoder that
  sometimes produced events out of order.

- Autopilot version requests from the MAVLink module are now constrained to
  at most one request in 2 seconds.

## [2.32.3] - 2025-07-31

### Changed

- MAVFTP file upload timeouts are now adaptive similarly to how TCP connections
  handle retransmissions.

### Fixed

- Better (more robust) handling of glitchy SMPTE timecodes. Timecodes with
  invalid hour components (larger than 23) are now ignored.

## [2.32.2] - 2025-07-21

### Fixed

- Revived the `flockwave.server.gateway` module that is currently used for the
  Skybrush Live online demo on <https://account.skybrush.io>

## [2.32.1] - 2025-07-17

### Changed

- JSON parsing and decoding is now performed with `orjson` instead of the
  built-in `json` module.

### Fixed

- Fixed an irrelevant exception that appeared when the server was shut down
  while a MAVFTP file upload was in progress.

## [2.32.0] - 2025-07-01

### Added

- Virtual UAVs can now enter sleep mode.

- Added instrumentation support with the `SKYBRUSH_INSTRUMENTS` environment
  variable. This is mostly useful for developers only.

### Changed

- Performance improvements in the parsing of MAVLink messages.

- The receive buffer size of UDP listener connections (used by the MAVLink module)
  is now increased to the maximum value allowed by the operating system. This
  helps in preventing dropped UDP packets when the system is under high load.

## [2.31.0] - 2025-06-23

### Added

- Added camera shutter test for MAVLink-based drones.

### Fixed

- Fixed name of configuration key for the point of no return in show clock
  synchronization, introduced in 2.30.0.

## [2.30.0] - 2025-06-23

### Added

- Servo channels can now be set explicitly for MAVLink-based drones with the
  `servo` command from the Messages panel in Live. The command has two
  parameters: the servo index (1-based) and the PWM value of the channel (in
  microseconds).

### Changed

- When synchronizing the show clock to a MIDI timecode, we now have a "point of
  no return" threshold. Adjustments to the MIDI clock are not reflected in the
  state of the show clock if the show clock is running and it is beyond the
  point of no return. However, the show clock is stopped if the MIDI clock is
  stopped even if the show clock is beyond the point of no return.

## [2.29.0] - 2025-06-15

### Added

- The MIDI timecode extension now allows the user to configure the maximum
  delay allowed between consecutive timecode messages before the MIDI clock is
  considered to have stopped. Raise the timeout if you are getting false
  positives in the log about the clock being stopped and then re-started a
  split second later. Lower the timeout for better responsiveness to stopping
  the MIDI clock on the timecode generator at the expense of larger probability
  for false positives.

### Changed

- Pre-arm messages from MAVLink drones are not logged by default in the server
  console any more. You can switch back to the old behaviour with the new
  `log_prearm` configuration subkey of the `statustext_messages` key of
  MAVLink network configuration objects. The reason for this change is that
  pre-arm messages can be retrieved by other means (e.g., by double-clicking
  on any drone in Skybrush Live that shows a `PREARM` error code), and pre-arm
  messages related to the polygon geofence may flood the logs when thousands of
  drones are turned at the same time in a location that is outside the _previous_
  geofence location.

- When logging transmission errors for communication channels, we now keep track
  of the number of _consecutive_ failed attempts and suspend logging errors on
  the channel when there were more than 5 failed attempts. This is to prevent
  flooding the logs if the networks are down due to reconfiguration while the
  server is running.

- The default timeout for MIDI tiemcode messages is raised to 1 second from
  200 milliseconds as larger delays might be expected (especially on Linux)
  when the system is under heavy load.

### Fixed

- Improved robustness of the main server loop for cases when internal command
  dispatch queues are filled up. This may typically happen when sending a
  unicast command to thousands of drones at the same time from the Live UI as
  the internal command dispatch queue has a size limit. (Note that you should
  use broadcast commands from Live if you want to wake up seveeral drones or
  send them to sleep mode).

- MAVLink autopilot version number is now queried again after a firmware update
  instead of returning the previously cached value.

## [2.28.0] - 2025-06-07

### Added

- MAVLink drones now support pyro channel tests.

## [2.27.1] - 2025-06-03

### Added

- Added support for the `X-LPS-CFG` command that allows one to configure the
  settings of a local positioning system.

### Changed

- Code related to generating RTH plans, yaw setpoints and pyro events in a
  `skyb` show file is now moved to a separate pro extension.

## [2.26.1] to [2.26.6] - 2025-05-27

No user-facing changes.

## [2.26.0] - 2025-04-28

### Added

- Over-the-air firmware updates are now supported on MAVLink drones running
  ArduPilot if the bootloader of the drone is capable of flashing the new
  firmware from an image file uploaded with MAVFTP. Currently not all flight
  controllers support this; refer to the corresponding page on the
  [ArduPilot documentation site](https://ardupilot.org/copter/docs/common-install-sdcard.html).

### Changed

- RTK correction packets are not sent on MAVLink networks when the server has
  not seen a MAVLink heartbeat from at least one drone in that network. This
  allows one to set up multiple MAVLink networks in the configuration file in
  advance; the server will send RTK correction packets only to those networks
  that are actually used.

## [2.25.2] - 2025-04-17

### Fixed

- Virtual UAVs simulated from the `virtual_uavs` extension now have proper
  horizontal velocity displays.

- The MIDI timecode extension is now more resilient to the disconnection of the
  MIDI timecode device. Disconnecting a device while the server is running will
  stop the clock and restart it when the device is connected again.

## [2.25.1] - 2025-03-24

### Fixed

- Sidekick and other extensions interested in fragmented MAVLink RTK correction
  packets are now notified only once for each such packet instead of as many
  times as the number of networks.

## [2.25.0] - 2025-03-18

### Added

- RSSI values can now be derived for UAVs from the RTCM message counts reported
  by newer versions of the Skybrush firmware (currently the development version
  only). This allows one to track the status of both the primary and the
  secondary telemetry channel. Live also needs to be updated to the development
  version to support more than one RSSI measure per drone.

- The `show` extension now exposes the metadata of the last uploaded show to
  other extensions via its own API.

### Changed

- Increased the sizes of some internal command queues to allow sending unicast
  commands to hundreds of drones one by one.

### Fixed

- When setting a parameter on MAVLink-based drones, the new value of the
  parameter is now queried explicitly if no `PARAM_VALUE` response arrives in
  time. This is necessary to modify parameters on ArduPilot-based drones using
  telemetry channels where MAVLink routing is disabled.

## [2.24.5] - 2025-02-22

### Added

- Added handler for `X-RTK-SAVE` that saves the current user-defined presets to
  the configuration file holding the user-defined presets.

- Added configuration option in the `rtk` extension to allow the user to
  specify an alternative configuration file for the user-defined presets.

## [2.24.4] - 2025-02-17

### Fixed

- Added missing command handlers for `UAV-CALIB` requests.

## [2.24.3] - 2025-02-15

### Added

- Added hover mission item type (only used in waypoint missions, not shows)

## [2.24.0] - 2025-02-13

### Added

- Added the concept of "show authorization scopes", to be used in later
  versions to allow starting a show in rehearsal mode or with lights only (no
  movement).

### Fixed

- A warning message is now printed when the limits imposed on the server by the
  license manager are reached.

- MAVLink signing timestamp synchronization now works across all networks
  instead of having a separate timestamp per network. This seems to work better
  in multi-network setups.

## [2.23.7] - 2025-01-20

### Fixed

- Fixed automatic inference of broadcast port when drones are sending packets
  from a UDP port number that is different from where they listen for broadcast
  packets.

## [2.23.0] - 2024-11-14

### Changed

- For extension authors: extensions binding to a TCP or UDP port to provide a
  service should register the port using the `use_port()` context manager if
  they wish to expose the port number to others. Registered ports will also be
  made public via the `SYS-PORTS` message.

## [2.22.0] - 2024-09-18

### Added

- MAVLink STATUSTEXT logging levels can now be configured separately for
  server logs (printed in the console) and client logs (forwarded to connected
  clients).

### Changed

- MAVLink STATUSTEXT messages with severity equal to INFO or DEBUG are not
  logged on the server console any more to reduce clutter. You can use the new
  MAVLink STATUSTEXT logging level configuration option to restore the old
  behaviour.

## [2.21.0] - 2024-09-07

### Changed

- MAVLink extension now uses _decimal_ numbers for system and component IDs
  instead of hexadecimal. Using hexadecimal versions in earlier versions caused
  confusion and made the log messages harder to interpret.

### Fixed

- "Server location changed to unknown" message displayed no longer as it was
  confusing during shutdown.

## [2.20.0] - 2024-09-03

### Added

- RTK correction message bandwidth is now tracked separately for inbound
  messages (received from the RTK base) and outbound messages (forwarded to
  UAVs and other components). You will need a recent version of Live to see
  both on the Live UI; older versions of Live will keep on showing the inbound
  bandwidth like before.

- Added a new extension to provide approximate location information for the
  server based on an arbitrary UAV in the UAV registry. Useful for seeding
  RTK corrections from virtual reference stations automatically if at least one
  UAV is seen by the server.

### Changed

- RTCM messages in the RTK module are now filtered by default such that only the
  basic RTCMv3 message set required for RTK corrections are forwarded to the
  UAVs. You can switch back to the full (unfiltered) message set in the settings
  of the RTK extension.

### Fixed

- Fixed the parsing of horizontal and vertical position uncertainty values from
  MAVLink `GPS_RAW_INT` messages. Earlier versions were overestimating the
  uncertainty by a factor of 10. Thanks to Jake1999 for reporting the issue.

## [2.19.1] - 2024-07-11

### Added

- Added support for parsing MSM5 and MSM6 RTK correction messages so you can
  now see the carrier-to-noise ratio in the RTK dialog box of Skybrush Live if
  your base station provides MSM5 or MSM6 messages.

### Fixed

- Fixed a bug in the decoder of chunked transfer encoding when using RTK
  corrections from NTRIP servers.

## [2.19.0] - 2024-07-08

### Added

- Low battery threshold in safety configuration can now be given as percentage
  as well

### Fixed

- MAVLink pressure sensor calibration messages may now also return "in
  progress" to consider them successful.

- Fixed a bug in the formatting of NMEA GGA messages for remote RTK stations
  that require the base station coordinates.

- MAVLink signature timestamps are now synchronized between broadcast and
  unicast streams of the same network.

## [2.18.1] - 2024-04-05

### Fixed

- Fixed a bug in the MAVLink signing setup code that resulted in an incorrect
  key when it is specified in bas64 representation instead of hex notation.

## [2.18.0] - 2024-02-13

### Added

- Added flight area definition and flight mode change mission commands,
  primarily for adaptive swarming missions

- Added ground altitude reference for mission items and new payload action
  types for camera based missions

## [2.17.5] - 2024-01-16

### Fixed

- Added missing "ID offset" field to MAVLink extension configuration schema so
  ID offsets can now be adjusted from the UI if you have multiple MAVLink
  networks.

- Fixed encoding bug in `.skyb` format when the takeoff time was non-zero.

## [2.17.3] - 2023-12-16

### Added

- MAVLink drones now support yaw setpoints during a show, provided that the
  onboard firmware supports yaw setpoints in the show data. This feature is
  experimental and has only been tested in simulator yet. We will publish more
  details about yaw control when the feature has been introduced fully through
  the entire Skybrush software stack.

### Fixed

- Fixed a crash in the Crazyflie extension with newer versions of `anyio`.

- Fixed a rare rounding error that caused an exception during show uploading
  due to an overflow.

## [2.17.2] - 2023-12-01

### Added

- The server can now be made aware of its own location with the `location`
  extension, and the `rtk` extension will use the location when connecting to
  an NTRIP server to send NMEA GGA messages periodically. This enables the use
  of NTRIP VRS servers (Virtual Reference Stations) where a country-wide
  network of base stations are used to provide RTK corrections at any arbitrary
  coordinate within the coverage area of the network.

### Fixed

- Fixed issues with reloading extensions that depend on `http_server` and
  provide their own blueprint.

- Fixed a congestion bug during MAVLink log downloads by not requesting more
  than 512 `LOG_DATA` chunks at a time.

## [2.17.0] - 2023-10-29

### Added

- MAVLink `RADIO_STATUS` messages are now parsed if they originate from
  a component that denotes a UDP-to-UART bridge. It is assumed that these
  messages come from the `mavesp8266` firmware; the RSSI values in the message
  are converted into percentages and stored on the server side. Live will be
  updated soon to display the RSSI values.

### Fixed

- Fixed a bug with drones that are capable of entering a sleep state where the
  autopilot type was not detected correctly after the drone was woken up.

## [2.16.0] - 2023-10-25

### Added

- MAVLink networks now support sending and receiving MAVLink signed messages.
  This feature is in an experimental stage yet and we appreciate any feedback
  from users.

## [2.15.0] - 2023-09-21

### Added

- The default accuracy and minimum duration of RTK surveys can now be configured
  in the `rtk` extension.

- MAVLink drones running a recent version of the Skybrush firmware (version
  20230920 or later, or, if you are compiling the firmware directly from our
  GitHub repo, then any version based on ArduCopter 4.3.7 or later) now report
  when they deviate significantly from their planned trajectory during a show.

- MAVLink drones report the takeoff and landing stages as informational messages
  during the execution of a show.

- Pro users can now load the `flight_report` extension, which can produce
  tabular reports about the takeoff and landing times of the drones as well as
  any error conditions, in various formats (HTML, CSV, JSON), for reporting
  purposes.

- RTK and RC override messages in the `mavlink` extension can now be routed to
  multiple connections.

- Broadcast messages from the `mavlink` extension can now be rate-limited to
  work around with packet loss issues in connections that cannot cope with
  bursty transmissions due to lack of flow control. This is a workaround that
  should be enabled only if you are experiencing packet loss problems and you
  suspect that it is due to lack of flow control.

## [2.14.0] - 2023-06-16

### Changed

- Behind-the-scenes updates to the `lps` (local positioning system) module.

### Fixed

- Fixed a bug that prevented adjusting the epoch of the show clock while the
  clock was running.

## [2.13.0] - 2023-05-27

### Added

- When working with MAVLink networks, you can now specify an offset that is
  added to the system ID before the final Skybrush ID of the drone is derived.
  This can be used to achieve continuous numbering if you have multiple
  networks with, say, 250 drones per network.

### Fixed

- Fixed a bug in the handling of NTRIP servers when the server responds with
  chunked transfer encoding.

- When reloading the `offline_maps` extension, the app now simply asks the user
  to restart the entire application instead of printing a cryptic error message
  to the console.

### Deprecated

- The top-level `connections` key of the MAVLink extension configuration object
  is deprecated. Define a MAVLink network first under the `networks` key with
  a unique ID and move the `connections` key there when migrating old config
  files.

## [2.12.2] - 2023-04-24

### Added

- Virtual UAVs now support (virtual) motor and LED tests.

### Fixed

- Fixed a bug in the handling of NTRIP server responses when the server
  responds with an HTTP/1.1 response line and not ICY.

## [2.12.1] - 2023-04-20

### Added

- Added support for a new clock that shows the time left until the end of the
  current show if the show duration is submitted by the client when it
  configures the start time of the show.

## [2.12.0] - 2023-04-11

### Added

- We now distinguish between altitude above _ground level_ (AGL) and altitude
  above _home level_ (AHL) in the server and also in Skybrush Live. Earlier
  versions used to call altitude above home level as AGL, this is now fixed and
  you should be looking for AHL instead of AGL if you want this information.
  AGL info will be provided only if terrain following is configured on the
  drone and it knows its own altitude above ground level.

- GPS horizontal and vertical accuracy data is now parsed from MAVLink messages
  and shown in Skybrush Live.

- Compass calibration now shows progress percentage in clients for MAVLink
  based drones. You might need to update Skybrush Live to see the progress bar.

- Added support for accelerometer calibration for MAVLink-based drones.

## [2.11.0] - 2023-04-05

### Added

- The Crazyflie extension can now be configured to select a specific controller
  type on the drones after a show upload.

## [2.10.2] - 2023-03-24

### Fixed

- When setting the clock of the server running on Linux during a connection from
  a Skybrush Live client, the new date and time is now also written back to the
  hardware clock of the server if it has one.

## [2.10.0] - 2023-02-14

### Added

- MAVLink-based drones are now marked as being in "sleep mode" if the heartbeat
  indicates that the flight controller is not running. You will not see this
  with the stock ArduPilot firmware, but if you use our own `mavesp8266` fork
  in your wifi module, you can configure the `mavesp8266` to be able to control
  power to the flight controller and it will generate heartbeats _on behalf of_
  the flight controller if the flight controller itself is powered down. This
  allows you to make full use of the "sleep" and "resume" buttons on the
  Skybrush Live UI with MAVLink-based drones, provided that you use our
  `mavesp8266` fork or implement similar functionality in the firmware of your
  wifi-to-serial bridge.

### Fixed

- Fixed MAVFTP uploads when the target path starts with `@`.

## [2.9.0] - 2023-01-18

### Added

- RTK presets can now be designated as auto-selectable; the first such preset
  will be used automatically when the server starts.

- The RTK extension now exposes methods that allow other extensions to retrieve
  the status of RTK corrections being received from the base station.

- MAVLink drones will now receive `RC_CHANNELS_OVERRIDE` messages when the
  server receives RC channel change events from a simulated RC via UDP packets.
  You need to enable the `rc_udp` extension to use this feature.

- RC simulation with UDP packets in the `rc_udp` extension now has a configurable
  timeout after which the RC connection is assumed to be lost.

## [2.7.1] - 2022-12-25

### Added

- The server is now aware of `systemd` on Linux systems and can be started as
  a `systemd` service with `Type=notify`.

### Fixed

- Fixed a bug in the "Save" button of the web UI that derives a minimal
  configuration file containing all the differences from the server defaults.

## [2.7.0] - 2022-12-19

### Added

- Added mission commands to supplement mission item parsing in industrial
  projects

### Fixed

- Fixed frame rate limit parsing in the `mocap` extension/

- Fixed mass parameter upload for Crazyflie drones

- Fixed geofence action descriptions

## [2.6.0] - 2022-11-20

### Added

- Added basic model classes for generic mission handling

## [2.5.1] - 2022-11-01

### Changed

- Crazyflie takeoff altitude is now configurable from the extension.

- Virtual UAV battery discharge time adjusted so we can simulate longer shows.

### Fixed

- Fixed display of Crazyflie yaw angles.

## [2.5.0] - 2022-09-14

### Added

- The base port number of the server can now be overridden by the `-p` command
  line switch, the `PORT` environment variable and the `PORT` configuration
  key, in this order of precedence (`-p` being the highest priority).

- Added a `motion_capture` extension that can be used as a base to add support
  for external motion capture systems for indoor drones.

- MAVLink extension now generates a CRC32 checksum into the uploaded `.skyb`
  files so the drones have one additional tool at their disposal to check the
  integrity of the uploaded file.

## [2.4.0] - 2022-06-26

### Added

- Shows can now be started automatically based on any arbitrary registered
  clock in the server. The primary use-case is to start a show based on an
  external MIDI timecode.

### Fixed

- Fixed automatic scanning for Crazyflie drones when multiple Crazyradios are
  connected; earlier versions did not preserve the assignment between radios
  and the discovered drones, resulting in warnings printed to the console.

- `show remove` and `show clear` commands are now accepted both by Crazyflie
  and MAVLink drones for sake of consistency.

## [2.3.0] - 2022-06-09

### Added

- Added an extension module that allows the server to cache map tiles
  downloaded by Skybrush Live so they can be used even when the computer
  running Live (and the server) is offline. Requires a license for Skybrush
  Live Pro.

### Changed

- On ArduPilot drones, the `FENCE_TYPE` parameter is now adjusted automatically
  when a geofence is uploaded. Earlier versions did not touch the `FENCE_TYPE`
  parameter even if the geofence configuration request contained limits for
  fence types that were not enabled before in the `FENCE_TYPE` bitmask.

### Fixed

- The RTK and the GPS extensions do not crash any more when trying to register a
  new beacon and the object registry is full (typically when the license limits
  are hit).

- Fixed the decoding of the git commit hash in ArduPilot version numbers.

## [2.2.0] - 2022-05-27

### Added

- Show specifications may now contain a preferred geofence action.

- ArduPilot-based drones can now configure the geofence action on the drone
  based on the action submitted in the show specification.

## [2.1.0] - 2022-05-19

### Added

- Added basic support for remote wakeup and shutdown of UAVs if the UAV driver
  supports it.

- Added experimental wakeup / shutdown support for MAVLink drones with a custom
  MAVLink message extension.

### Fixed

- Fixed a deadlock in the extension manager when the licensing extension
  initiated a forced shutdown due to an expired license.

## [2.0.0] - 2022-04-29

### Changed

- The source code of the server is now licensed under the GNU General Public
  License, version 3 or later.

### Fixed

- License module now prints the maximum number of drones if there is such a
  restriction in the license.

## [1.28.3] - 2022-04-10

### Changed

- Geofence errors are now turned into warnings for MAVLink-drones if we know
  that the drone is on the ground.

### Fixed

- Fixed binding of Skybrush Server to SSDP sockets on Windows.

## [1.28.2] - 2022-03-24

### Fixed

- Fixed a bug in the logging of start events for indoor shows

- macOS and Linux launcher scripts now look for the configuration file in the
  default installation folder if no configuration file is given explicitly.

## [1.28.0] - 2022-02-23

### Added

- The server configuration can now be exported from the web UI in JSON format.

- When the server is configured from a configuration file, the web UI now allows
  the user to save the current configuration to the configuration file.

- Basic user authentication can now be set up from the web UI.

- Server logs are now saved in a dedicated logging folder; logs are rotated
  at regular intervals.

- Extensions can now be reloaded from the web UI even if other extensions
  depend on them; the dependencies will be unloaded before the extension is
  reloaded and they will be restored afterwards.

- For uBlox RTK base stations, the server now requests the base station to send
  UTC timestamps at regular intervals and warn connected clients if the server
  clock is not synchronized to the clock of the RTK base station.

### Fixed

- Motor test on MAVLink-based drones now supports more than four rotors.

## [1.27.2] - 2022-01-03

### Fixed

- Fixed a bug in the RTK base station handling that sometimes resulted in an
  unhandled error message when the RTK base station was not sending valid
  messages at all.

- Fixed a bug that prevented the magnetic vector provider from working properly
  in macOS builds.

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
