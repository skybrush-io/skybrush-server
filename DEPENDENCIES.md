# Dependency notes

This document lists the reasons why specific dependencies are pinned down to
exact versions. Make sure to consider these points before updating dependencies
to their latest versions.

## `jsonschema`

`jsonschema` is pinned down to version 4.17.3 because this is the latest
version that does not depend on `rpds-py`. `rpds-py` requires a Rust compiler
to compile it from source, making it harder to deploy Skybrush Server in an
Android environment where no pre-built wheel is available for `rpds-py`.

The pin can be removed if `rpds-py` gets included officially in
`python-for-android`.
