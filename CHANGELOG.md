# Changelog

All notable changes to this project will be documented in this file.
This project follows Semantic Versioning.

## [Unreleased]
- Define a supported public surface for CogniHub integration
- Add tool call/result contract models with stable serialization
- Introduce explicit ToolRegistry/ToolRuntime and remove global registries
- Remove import-time env reads and add configuration models
- Add CogniHub adapter helpers for tool specs and tool call parsing
- Add CI workflow (lint/type/test/build) and tool loop contract test
- Move heavy web dependencies behind extras and drop auto-install behavior

## [1.0.0] - 2026-01-28
- Initial release
