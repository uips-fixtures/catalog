# catalog

**Public.** Single source of truth for all uips-fixtures MCVEs and UiPath activity package metadata. Derivative output formats are generated from this repo.

## Responsibility

- Receives sanitised data pushed by the `catalog-private` sanitise-and-publish workflow
- Aggregation and derivative-build workflows run here against clean data
- Do not push raw or unsanitised data directly to this repo

## Data flow

```
catalog-private (sanitise-and-publish)
      │
      ▼
data/sources/{domain}/    ← sanitised source files
      │
      │  aggregate workflow
      ▼
data/aggregated/          ← single source of truth
      │
      │  publish workflow
      ▼
dist/                     ← derivative outputs (Pages, LLM index, feeds)
```

## Structure

```
data/
  sources/
    nuget/          ← sanitised UiPath activity package catalog files
    org/            ← GitHub org metadata
    mcve/           ← MCVE repo metadata
    interactions/   ← stars, forks, PR activity
  aggregated/       ← built by aggregate workflow, do not edit manually
dist/               ← built by publish workflow, do not edit manually
schemas/            ← JSON Schema for each domain and aggregated output
```

Do not edit `data/aggregated/` or `dist/` manually. All writes are performed by GitHub Actions.
