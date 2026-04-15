# Development Guidelines for Skybrush Server

This document provides essential information for agentic coding assistants.

Agents must NEVER COMMIT changes, unless explicitly requested by the user.

Agents must NEVER PUSH changes.

## Build, Lint, and Test Commands

```bash
# Install/update dependencies
uv sync

# Type checking
uv run ty check

# Linting (auto-fixes issues)
uv run ruff check --fix

# Format code
uv run ruff format

# Run all tests
uv run pytest

# Run tests in a specific file
uv run pytest test/test_model.py

# Run a specific test function
uv run pytest test/test_model.py::test_attitude

# Run tests matching a pattern
uv run pytest -k "test_uavstatus"

# Run tests with coverage
uv run pytest --cov=src/flockwave/server
```

## Code Style Guidelines

### Import Organization

- Group imports: standard library → third-party → local imports
- Use absolute imports from the `flockwave` package
- Use `TYPE_CHECKING` block for type-only imports

### Type Annotations & Naming

- Use modern union syntax with `|` (e.g., `int | None`, `str | list[str]`)
- Use `TypedDict` for structured data with known fields
- Define type aliases for complex types
- Use `TypeVar` with `bound=` for generic types (e.g., `T = TypeVar("T", bound="UAVDriver")`)
- Classes: PascalCase (`CommandExecutionManager`, `UAVStatusInfo`)
- Functions/Methods/Variables: snake_case (`get_cache_dir`, `charging`)
- Constants: UPPER_SNAKE_CASE (`PACKAGE_NAME`)
- Private members: Leading underscore (`_driver`, `_voltage`)

### Classes and Properties

- Use properties with getters/setters for validation and conversion
- Store internal values with leading underscore
- Include `json()` method `json` property for serialization

### Error Handling

- Create custom exceptions inheriting from `FlockwaveError` (in `src/flockwave/server/errors.py`)
- Use `NotSupportedError` for unimplemented operations
- Use `CommandInvocationError` for user command errors
- Provide default error messages in constructors

### Async Concurrency

- Use `trio` for async operations (not `asyncio`)
- Use `async`/`await` for I/O operations
- Use `AsyncGenerator` for data streams
- Import from `trio` and `trio_util`

### Documentation

- Use Google-style docstrings with `Args:`, `Returns:`, and `Raises:` sections if applicable
- Add module-level docstrings
- Mark features with `@versionadded` or `@deprecated` decorators

### Module Structure

- Use descriptive module-level docstrings
- Keep related classes/functions in the same file

### Logging

- Use hierarchical loggers: `log = base_log.getChild("submodule")`
- Import base logger from `flockwave.server.logger`

### Testing

- Tests in `test/` directory
- Test functions named with `test_` prefix
- Use descriptive test names

### File Organization

- `src/flockwave/server/` - main server code
- `src/flockwave/server/ext/` - extension modules
- `src/flockwave/server/model/` - data model classes
- `src/flockwave/server/utils/` - utility functions
- `test/` - tests mirroring source structure
