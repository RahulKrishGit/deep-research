# Local Environment Secrets Design

## Goal

Allow developers to keep all runtime API keys in one local `.env` file so
live application checks can run without committing or serializing secrets.

## Scope

This design applies to the existing environment-only secrets: `OPENAI_API_KEY`,
`TAVILY_API_KEY`, and the optional LangSmith values `LANGSMITH_API_KEY` and
`LANGSMITH_PROJECT`.

## Design

`load_config(config_path, strict=False)` will load a `.env` file before it
applies configuration overrides and strict runtime-secret validation. The
loader will locate `.env` beside the supplied YAML configuration file, so
`load_config("config.yaml")` loads the repository-root `.env`.

The loader will use `python-dotenv` with `override=False`. Values already set
by the shell, CI, container, or deployment environment therefore take
precedence over local `.env` values. A missing `.env` remains valid and has no
effect.

Secrets remain environment variables only. They must not be added to
`ConfigSettings`, `config.yaml`, logs, tracker metadata, span outputs, or
telemetry. `.env` remains ignored by Git; `.env.example` is the only committed
template and contains blank values.

## Behavior

- Local development: copy `.env.example` to `.env`, add personal keys, then
  call `load_config("config.yaml")` normally.
- CI and deployment: inject environment variables through the platform; those
  values override `.env` if one happens to be present.
- Strict mode: `.env` values are available before required-secret validation.
- Live smoke tests: use the normal config loader and do not print secret
  values.

## Error Handling

No error is raised when `.env` is absent. Existing errors for missing or blank
required secrets in strict mode remain unchanged and identify environment
variable names only, never their values.

## Tests

Tests will verify that a sibling `.env` supplies runtime secrets, a pre-set
process environment value wins over `.env`, a missing `.env` is harmless, and
secret-bearing values never appear in `ConfigSettings.model_dump()` or tracker
metadata.

## Documentation

The setup guide will state that `.env` is loaded automatically, that users
should copy `.env.example` to `.env`, and that shell/CI environment variables
take precedence.
