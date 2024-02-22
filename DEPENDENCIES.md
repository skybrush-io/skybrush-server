# Dependency notes

This document lists the reasons why specific dependencies are pinned down to
exact versions. Make sure to consider these points before updating dependencies
to their latest versions.

## `jsonschema`

### Android build

In an Android build, `jsonschema` must be pinned down to version 4.17.3 because
this is the latest version that does not depend on `rpds-py`. `rpds-py`
requires a Rust compiler to compile it from source, making it harder to deploy
Skybrush Server in an Android environment where no pre-built wheel is available
for `rpds-py`.

The pin can be removed if `rpds-py` gets included officially in
`python-for-android`.

### macOS build

On macOS, `jsonschema >= 4.20` is required as older versions seem to have
problems in the bundled executable version with loading the schemas.

### Other platforms

For sake of simplicitly, other platforms should be consistent with the macOS
build, i.e. `jsonschema >= 4.20` should be used.
