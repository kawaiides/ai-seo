# AEGIS AEO Gate — GitHub Action

Block PRs whose target pages score below an AEO threshold.

## Usage

```yaml
- name: AEGIS AEO gate
  uses: aegis-autopilot/aegis-action@v0.1
  with:
    api-key: ${{ secrets.AEGIS_API_KEY }}
    threshold: 70
    targets: |
      https://staging.example.com/blog/${{ github.head_ref }}
      https://staging.example.com/pricing
```

For local HTML drafts (e.g. a static site generator's build output):

```yaml
- run: hugo --destination=public
- uses: aegis-autopilot/aegis-action@v0.1
  with:
    api-key: ${{ secrets.AEGIS_API_KEY }}
    paste: 'true'
    threshold: 65
    targets: |
      public/blog/announcing-x/index.html
      public/blog/announcing-y/index.html
```

## Inputs

| Name             | Required | Default                          | Description                                                 |
|------------------|----------|----------------------------------|-------------------------------------------------------------|
| `api-key`        | yes      | —                                | Org-scoped Bearer key with `audit:write` scope.             |
| `targets`        | yes      | —                                | URLs (default) or file paths (with `paste: 'true'`).        |
| `threshold`      | no       | `65`                             | Minimum AEO score per target. Below = fail.                 |
| `api-url`        | no       | `https://aegis-autopilot.com`    | Override for self-hosted installs.                          |
| `paste`          | no       | `false`                          | Treat `targets` as local file paths instead of URLs.        |
| `timeout`        | no       | `30`                             | Per-request HTTP timeout in seconds.                        |
| `fail-on-error`  | no       | `true`                           | Treat network errors as a build failure.                    |

## Outputs

| Name           | Description                                       |
|----------------|---------------------------------------------------|
| `min-score`    | Lowest score across all targets.                  |
| `failed-count` | Number of targets below `threshold`.              |
| `report`       | Compact JSON: `{threshold, results: [...]}`.      |

## Exit codes

`0` clean, `1` below threshold, `2` usage, `3` auth, `4` network.
