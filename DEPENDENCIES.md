# Dependency notes

This document lists the reasons why specific dependencies are pinned down to
exact versions. Make sure to consider these points before updating dependencies
to their latest versions.

## bcrypt

bcrypt restricted to <5.0.0 because 5.0.0 changes how it handles passwords
longer than 72 characters, which breaks `passlib` (which has not been
updated for years now). We need to wait until the `libpass` maintainer
officially takes over the `passlib` project and releases a new version
there. We can then upgrade `passlib` and remove the `bcrypt` restriction.
