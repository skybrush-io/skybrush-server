---
name: pydantic-refactor-of-ext-config
description: Refactors a Skybrush server extension's configuration from a raw dict JSON schema to a Pydantic model. For class-based extensions the base class becomes TypedConfigExtension; for module-level extensions (no class) only the schema is replaced.
---

# Skill: Pydantic Refactor of Extension Config

Refactors a Skybrush server extension's configuration from a raw dict JSON schema to a Pydantic model. For class-based extensions the base class becomes `TypedConfigExtension`; for module-level extensions (no class) only the `schema` is replaced.

## Reference

Canonical examples of this refactor (view with `git show <hash>`):

- `6fc14264` — `auth` extension: class-based, simple `bool` field
- `d17f6235` — `show` extension: class-based, enum field with `WithJsonSchema` + `Annotated`
- `5e4be6fe` — `auth_basic` extension: module-level (no class), nested array-of-objects schema
- `c7a6638e` — `audit_log` extension: class-based, simple `float` field

Review these for a complete before/after picture.

## Process

I will guide you through each extension one at a time. For each extension I will:

1. Read the current extension code and config schema
2. Ask you questions about the config fields
3. Apply the rewrite
4. Verify with lint and tests

## Prerequisites

Run this from the project root (where `pyproject.toml` lives).

---

## Step-by-step for each extension

### 1. Locate the extension

I will find the extension files. Extensions are in `src/flockwave/server/ext/` — either a single `<name>.py` or a package `<name>/` with `extension.py`.

I will read the module-level `schema` dict and the `run` method to understand what config fields exist.

### 2. Ask about config fields

For each field in the old `schema` dict, I will ask:

- **Field name** — already known from the schema
- **Pydantic type** — `str`, `int`, `float`, `bool`, or an existing enum class
- **Default value** — already known from the schema
- **Title and description** — already known from the schema
- **Any enum-related display metadata** — `options.enum_titles` etc.

### 3. Convert the schema

Create a `ConfigModel(BaseModel)` with:

- Simple types (`str`, `int`, `float`, `bool`) used as-is
- Enum types kept as the Python type for validation, with `Annotated[EnumType, WithJsonSchema(...)]` to inline the JSON schema (avoids Pydantic v2's `$defs`/`$ref` which breaks the WebUI's JSONEditor v2.5.4)
- `enum_titles` and other metadata in `WithJsonSchema` (not in `Field(json_schema_extra=...)` — `WithJsonSchema` replaces the generated schema entirely)
- All strings derived from the enum class itself (e.g. `[m.value for m in MyEnum]`, `[m.describe() for m in MyEnum]`) to avoid duplication

See `d17f6235` (show extension) for a full example.

### 4. Change imports

- Replace `from flockwave.server.ext.base import Extension` with `from flockwave.server.ext.base import TypedConfigExtension`
- Add `from pydantic import BaseModel, Field, WithJsonSchema`
- Add `from typing import Annotated`

### 5. Change the class

The extension class must inherit from `TypedConfigExtension[ConfigModel]` instead of `Extension`.

- Old: `class MyExtension(Extension):`
- New: `class MyExtension(TypedConfigExtension[ConfigModel]):`

The `TypedConfigExtension` base class (from `flockwave.server.ext.base`) provides:
- `self.config` — the validated Pydantic model instance, available after `configure()` is called
- `configure(configuration: ConfigModel)` — called automatically during `load()` after `self.log` and `self.app` are set
- The `run()` method no longer receives `configuration` or `logger` — they are available via `self.config` and `self.log`

### 6. Add `configure()` method

Move config reading logic from `run()` here. The config is already validated when this is called.

```python
def configure(self, configuration: ConfigModel) -> None:
    self._some_field = configuration.some_field
    # ... convert enum fields if needed ...
```

### 7. Update `run()` method

In the standard pattern:
- Remove `configuration` and `logger` parameters
- Signature becomes `async def run(self, app):`
- Use `self.log` instead of the `logger` parameter
- Any config-dependent setup that was in `run` should already be in `configure()`

However, some extensions may still need `configuration` and `logger` as parameters if `run()` is invoked directly by other code rather than solely through the extension manager lifecycle.

### 8. Update module-level variables

- Replace the dict `schema = {...}` with `schema = ConfigModel`
- Keep `construct = MyExtension`, `dependencies`, `description` as-is (unless they also need updating)

---

## Variation: Module-level extensions (no class)

Some extensions have no class — just an `async def run()` function at module level and no `construct`. The module itself IS the extension instance.

For these, we keep the existing structure and only replace the `schema` dict with a Pydantic model:

### Changes needed

1. Add `from pydantic import BaseModel, Field`
2. Create a `ConfigModel(BaseModel)` class in the same file
3. Replace the `schema` dict with `schema = ConfigModel`
4. Update `run()` to use attribute access on the typed `configuration` parameter (e.g. `configuration.sources` instead of `configuration.get("sources", ())`)
5. Remove any validation logic that becomes redundant (Pydantic validates types/defaults)

### Nested schemas (arrays of objects)

For fields like `sources: list[dict[str, str]]`, embed the nested item schema via `Field(json_schema_extra={"items": {...}})` — it overrides Pydantic's auto-generated `items` key.

See `5e4be6fe` (auth_basic extension) for a full example.

No class inheritance changes, no `configure()` method, no `TypedConfigExtension` — just the schema swap. The validated Pydantic model instance is passed as `configuration` to `run()`.

### Verify

- Run `uv run ruff check --fix src/flockwave/server/ext/<name>/extension.py` (or the single-file path)
- Run `uv run ruff format src/flockwave/server/ext/<name>/extension.py`
- Run `uv run python -c "import json; from flockwave.server.ext.<name>.extension import ConfigModel; print(json.dumps(ConfigModel.model_json_schema(), indent=2))"` and verify no `$defs` key in the output
- Run `uv run pytest --cov=src --cov-report=html -vv -s -x` (full suite with coverage)
- Run `pre-commit run --all-files` to catch any remaining issues (end-of-file, trailing whitespace, ruff, formatting, uv-lock)

---

## Key constraints

- NEVER commit. Wait for user to review and commit.
- NEVER push.
- Work on one extension at a time.
- Ask before making changes that differ from this pattern.
