# with-workbench

A CLI tool for running Posit Workbench in Docker and executing commands against it.

## Installation

Install as a tool using `uv` (recommended):

```bash
uv tool install git+https://github.com/posit-dev/with-workbench.git
```

Or install from a local clone for development:

```bash
git clone https://github.com/posit-dev/with-workbench.git
cd with-workbench
uv tool install -e .
```

## Requirements

- Python 3.11+, or `uv`
- Docker
- A valid Posit Workbench license key

## Usage

### Basic Usage

Run Posit Workbench with default settings:

```bash
export RSW_LICENSE=YOUR-LICENSE-KEY
with-workbench
```

This will:
1. Pull the specified Posit Workbench Docker image
2. Start a container with your license key
3. Wait for Workbench to be ready (via `/health-check` endpoint)
4. Create a test user for authentication
5. Output connection credentials

### Options

| Option          | Default     | Description                                                                                      |
|-----------------|-------------|--------------------------------------------------------------------------------------------------|
| `--version`     | `release`   | Workbench version. Use `release` for latest stable, `preview` for daily builds, or a specific version like `2026.01.1`. |
| `--license-key` |             | Workbench license key. Can also be set via `RSW_LICENSE` environment variable.                   |
| `--image`       |             | Container image to use, including tag. Overrides `--version`.                                    |
| `--port`        | `8787`      | Port to expose Workbench on. Automatically finds next available port if in use.                  |
| `--user`        | `testuser`  | Username to create for testing.                                                                  |
| `--password`    | (generated) | Password for test user. Auto-generated if not specified.                                         |
| `--quiet`       | `false`     | Suppress progress indicators during image pull.                                                  |
| `--stop`        |             | Stop a running container by ID, or use `CONTAINER_ID` env var if not specified.                  |

Example:

```bash
with-workbench --version 2026.01.1 --port 8788 --user myuser
```

### Start-Only Mode

When you run `with-workbench`, it starts Workbench and outputs shell variables you can use:

```bash
with-workbench --license-key $RSW_LICENSE
# Outputs:
# WORKBENCH_URL=http://localhost:8787
# WORKBENCH_USER=testuser
# WORKBENCH_PASSWORD=...
# CONTAINER_ID=...
```

You can eval the output to set the variables in your shell:

```bash
eval $(with-workbench)
echo "Workbench running at $WORKBENCH_URL"
echo "Login with $WORKBENCH_USER / $WORKBENCH_PASSWORD"

# Stop Workbench when done (--stop without argument uses $CONTAINER_ID)
with-workbench --stop
```

### User Handling

The tool creates a PAM user inside the container for authentication:

- **Default user (`testuser`)**: Created with auto-generated password, output in credentials.
- **Custom user**: Specify with `--user myuser`. User is created if it doesn't exist.
- **`rstudio` user**: This user is pre-created in the Workbench image and used by internal services. The tool will use it as-is without modifying the password to avoid service disruption.

### Docker Image Mapping

The `--version` option maps to Docker image tags:

| Version     | Image                                        |
|-------------|----------------------------------------------|
| `release`   | `rstudio/rstudio-workbench:jammy`            |
| `preview`   | `rstudio/rstudio-workbench-preview:jammy-daily` |
| `2026.01.1` | `rstudio/rstudio-workbench:jammy-2026.01.1`  |

## GitHub Actions

> **Coming Soon:** GitHub Action support is planned. See the [proposal](with-workbench-proposal.md) for details.

The planned GitHub Action interface will support:

```yaml
- name: Start Workbench
  id: workbench
  uses: posit-dev/with-workbench@main
  with:
    version: 2026.01.1
    license-key: ${{ secrets.RSW_LICENSE_KEY }}
    user: testuser

- name: Run E2E tests
  env:
    WORKBENCH_URL: ${{ steps.workbench.outputs.WORKBENCH_URL }}
    TEST_USER: ${{ steps.workbench.outputs.WORKBENCH_USER }}
    TEST_PASSWORD: ${{ steps.workbench.outputs.WORKBENCH_PASSWORD }}
  run: npm run test:e2e

- name: Stop Workbench
  if: always()
  uses: posit-dev/with-workbench@main
  with:
    stop: ${{ steps.workbench.outputs.CONTAINER_ID }}
```

## Differences from with-connect

| Aspect             | with-connect                    | with-workbench                     |
|--------------------|---------------------------------|------------------------------------|
| License            | File-based (mounted)            | Key-based (`RSW_LICENSE` env var)  |
| Authentication     | API key via bootstrap endpoint  | PAM user creation                  |
| Default port       | 3939                            | 8787                               |
| Readiness check    | HTTP server log message         | `/health-check` endpoint           |
| Output variables   | `CONNECT_API_KEY`, `CONNECT_SERVER` | `WORKBENCH_USER`, `WORKBENCH_PASSWORD`, `WORKBENCH_URL` |

## Development

```bash
# Clone and install in development mode
git clone https://github.com/posit-dev/with-workbench.git
cd with-workbench
uv sync

# Run directly
uv run python main.py --version 2026.01.1

# Run tests (coming soon)
uv run pytest
```

## Related

- [with-connect](https://github.com/posit-dev/with-connect) - Similar tool for Posit Connect
- [Posit Workbench Docker Images](https://hub.docker.com/r/rstudio/rstudio-workbench)
