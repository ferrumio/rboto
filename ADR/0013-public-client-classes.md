# ADR-0013: Export explicit client classes from the facade

- Status: Accepted
- Date: 2026-10-28
- Deciders: rboto maintainers

## Context

Documentation alternated between service factories and imports from service
packages. Users had to learn different import paths for construction and type
annotations. Some class names also obscured the service or used inconsistent
capitalization.

## Decision

The primary API is `from rboto import S3Client` and its equivalents for every
registered service. The facade exposes the actual service class through lazy
module attributes, with an explicit generated stub for static type checking.
Importing the facade alone does not import native packages. Importing a client
class requires that service package.

Class names retain recognizable service names and acronym capitalization.
Use `StepFunctionsClient`, `SESV2Client`, `EventBridgePipesClient` and
`EventBridgeSchedulerClient` for the four renamed clients.

Keep service distributions, extras, factories and old service-package imports
compatible. Descriptors separate the canonical `client_class` from an optional
`native_client_class`. A distinct native name also remains a Python alias.
Existing native symbols and service exception bases retain their names.

All public constructors accept `Session` with the same conflict checks as the
factories. The resolver is shared, and factories return the canonical class.
Missing service packages get installation guidance; broken dependencies and
native import errors retain their original diagnostics.

## Consequences

- Examples and annotations use one public import location.
- Optional services remain optional, and imports do not initiate network calls.
- Wildcard imports require every exported service; documentation uses explicit imports.
- Existing async methods, native output ownership and modeled exceptions are unchanged.
- Naming changes can be implemented without changing the compiled native ABI.
