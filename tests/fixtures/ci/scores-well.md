# Spec: a fixture that scores well

**Author:** test fixture
**Date:** 2026-10-19
**Status:** Draft

## Context

A small spec that covers every section the validator looks for, so the CI helper's tests have a spec that passes with only the HTTP warning.

## Functional Requirements

- FR-1: The tool MUST print a greeting.
- FR-2: The tool MUST exit 0 after printing it.

## Non-Functional Requirements

- NFR-1: The tool MUST finish in under 100 ms.

## Acceptance Criteria

### AC-1: It greets (FR-1)

Given the tool, when it runs, then it prints a greeting.

### AC-2: It exits cleanly (FR-2, NFR-1)

Given the tool, when it runs, then it exits 0 within 100 ms.

## Edge Cases

- EC-1: The terminal is closed. The tool exits 0 anyway.

## API Contracts

```ts
type Run = () => { stdout: string; exitCode: 0 };
```

## Data Models

| Field | Type | Constraint |
|---|---|---|
| greeting | string | not empty |

## Out of Scope

- OS-1: Translations, because nobody asked.
