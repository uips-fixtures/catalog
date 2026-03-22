# Schema compatibility contract

This document states what consumers of schemas published under
`https://uips-fixtures.github.io/catalog/schemas/` can rely on.
The JSON Schema files alone cannot express all of these guarantees.

---

## General rules (apply to all schemas)

### Versioning

Schemas are versioned by directory: `schemas/v0.1/`, `schemas/v2/`, etc.

- **Published schema URLs are permanent.** A URL that resolves today will
  continue to resolve indefinitely — it will never return 404.
  Within a version, the URL points to the current document, not a frozen
  snapshot; additive changes update the live document at the same URL.
- **Additive changes** (new optional fields) may be made within a version
  without notice. Lenient parsers are unaffected. The URL does not change.
- **Breaking changes** (removing or renaming fields, changing types or
  semantics, making optional fields required) always result in a new version
  directory. The previous version remains accessible.

### Field stability

Every field defined in a schema is stable within its version:
its name, type, and meaning will not change.

### Ordering

No ordering guarantee is given for any array in any schema
unless explicitly stated in that schema's section below.
Always index by a named key, never by array position.

### Optional fields

All known optional fields are always present in the output, typed
`["T", "null"]`. A field is either always there (possibly null) or not
defined in the schema at all. Consumers need only handle one absent state:
`null`. Field absence means the schema does not define that field for this
version.

---

## activity-catalog v0.1

Schema: [`v0.1/activity-catalog.schema.json`](v0.1/activity-catalog.schema.json)

### Purpose

A curated catalog of workflow activities for a named set. The canonical
entity is a **workflow primitive**: a node that can be placed in a workflow
graph and configured through members. It is not a NuGet package entry.
Provenance is attached per activity via a `source` object; the schema is not
tied to any single distribution model.

### Stable keys

| Array | Stable key | Scope |
|---|---|---|
| `sources[]` | `(kind, id)` | global within file |
| `activities[]` | `id` (compound string) | global within file |
| `activities[].members[]` | `name` | within the activity |

A catalog contains at most one entry in `sources[]` per `(kind, id)` pair —
version selection (latest-stable) ensures this invariant. The three-field
match used in referential integrity is therefore deterministic.

The activity `id` is a compound string: `{fullName}@{source.id}/{source.version}`.
This format is part of the contract; consumers may parse it to extract
`fullName`, `source.id`, and `source.version`. `fullName`, `source.id`, and
`source.version` are guaranteed to contain neither `@` nor `/`; no escaping
is needed. `fullName` alone is not guaranteed unique across sources or sets.

### Source kinds

| `kind` | What it represents |
|---|---|
| `nuget-package` | Activities extracted from a NuGet package |
| `dotnet-runtime` | Activities from the .NET WF4 runtime (e.g. CoreWF) |

`sources[]` is the authoritative inventory of what is in the file.
Every activity's `source` object matches an entry in `sources[]` by `(kind, id, version)`.

### Referential integrity

Every activity's `source` object (`kind`, `id`, `version`) matches exactly
one entry in `sources[]`. `sources[]` contains only sources that have at
least one activity in `activities[]`.

### Member kinds

Each entry in `members[]` carries a `memberKind` that reflects its role in
the Windows Workflow Foundation activity model:

| `memberKind` | WF4 concept | `argumentDirection` |
|---|---|---|
| `argument` | `InArgument<T>` / `OutArgument<T>` / `InOutArgument<T>` | `"In"`, `"Out"`, or `"InOut"` |
| `variable-scope` | `Variable<T>` or `Collection<Variable>` scoped to this activity | always `null` |
| `child` | `Activity`, `ActivityAction<T>`, `ActivityFunc<T,R>`, or collections thereof | always `null` |
| `property` | plain CLR design-time property | always `null` |

`members[]` contains only members visible in Studio (browsable).

### dataType format

`dataType` values follow these rules:

- Fully namespace-qualified: `System.String`, never `string`
- C#-style generics: `System.Collections.Generic.List<System.String>`
- No assembly qualification
- No whitespace around `<`, `>`, or `,`
- Arrays use `[]` suffix: `System.String[]`

### Compatibility notes

- `schema.id = "activity-catalog"` and `schema.version = "v0.1"` are
  constant for all v0.1 files. Verify both before parsing.
- `argumentDirection` is non-null only when `memberKind` is `"argument"`.
- `isRequiredArgument` is always `false` when `memberKind` is not `"argument"`.
