import argparse
import os
import secrets
import socket
import string
import sys
import time

import docker
import requests

IMAGE = "rstudio/rstudio-workbench"
IMAGE_PREVIEW = "rstudio/rstudio-workbench-preview"
DEFAULT_VERSION = "release"
DEFAULT_PORT = 8787
DEFAULT_USER = "testuser"


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Run Posit Workbench in Docker for testing"
    )
    parser.add_argument(
        "--version",
        default=DEFAULT_VERSION,
        help=f"Workbench version (default: {DEFAULT_VERSION})",
    )
    parser.add_argument(
        "--license-key",
        required=False,
        help="Workbench license key (or set RSW_LICENSE env var)",
    )
    parser.add_argument(
        "--image",
        help="Custom container image (overrides --version)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Port to expose Workbench on (default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--user",
        default=DEFAULT_USER,
        help=f"Test username to create (default: {DEFAULT_USER})",
    )
    parser.add_argument(
        "--password",
        help="Password for test user (auto-generated if not specified)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress progress indicators",
    )
    parser.add_argument(
        "-e",
        "--env",
        action="append",
        dest="env_vars",
        help="Environment variables to pass to Docker container (KEY=VALUE, repeatable)",
    )
    parser.add_argument(
        "--stop",
        nargs="?",
        default=None,
        const="",
        metavar="CONTAINER_ID",
        help="Stop a running container (uses CONTAINER_ID env var if not specified)",
    )

    # Handle -- separator to capture command arguments
    if "--" in sys.argv:
        separator_index = sys.argv.index("--")
        main_args = sys.argv[1:separator_index]
        command_args = sys.argv[separator_index + 1:]
    else:
        main_args = sys.argv[1:]
        command_args = []

    args = parser.parse_args(main_args)
    args.command = command_args
    return args


def get_docker_tag(version: str) -> tuple[str, str]:
    """Map version string to Docker image and tag."""
    if version == "preview":
        return (IMAGE_PREVIEW, "jammy-daily")
    if version in ("latest", "release"):
        return (IMAGE, "jammy")
    return (IMAGE, f"jammy-{version}")


def parse_image_spec(image: str) -> tuple[str, str]:
    """Parse image:tag spec into components."""
    if ":" in image:
        base, tag = image.rsplit(":", 1)
        return (base, tag)
    return (image, "latest")


def get_docker_client():
    """Get Docker client, with friendly error if Docker isn't running."""
    try:
        return docker.from_env()
    except docker.errors.DockerException:
        raise RuntimeError(
            "Cannot connect to Docker. Is Docker Desktop running?"
        )


def has_local_image(client, image_name: str) -> bool:
    """Check if image exists locally."""
    try:
        client.images.get(image_name)
        return True
    except docker.errors.ImageNotFound:
        return False


def pull_image(client, base_image: str, tag: str, quiet: bool) -> None:
    """Pull Docker image from registry."""
    image_name = f"{base_image}:{tag}"

    print(f"Pulling image {image_name}...", file=sys.stderr)

    pull_stream = client.api.pull(
        base_image, tag=tag, platform="linux/amd64", stream=True, decode=True
    )

    if quiet:
        for _ in pull_stream:
            pass
    else:
        layer_progress = {}
        last_percent = -1
        for chunk in pull_stream:
            if "id" in chunk and "progressDetail" in chunk:
                detail = chunk["progressDetail"]
                if "current" in detail and "total" in detail:
                    layer_progress[chunk["id"]] = (detail["current"], detail["total"])

            if layer_progress:
                total_current = sum(p[0] for p in layer_progress.values())
                total_size = sum(p[1] for p in layer_progress.values())
                if total_size > 0:
                    percent = int(total_current * 100 / total_size)
                    if percent != last_percent:
                        print(f"\rPulling: {percent}%", end="", flush=True, file=sys.stderr)
                        last_percent = percent

        print("\r" + " " * 20 + "\r", end="", file=sys.stderr)

    print(f"Successfully pulled {image_name}", file=sys.stderr)


def ensure_image(
    client, base_image: str, tag: str, version: str, quiet: bool
) -> tuple[str, str]:
    """Ensure Docker image is available, pulling if needed.

    Returns the (base_image, tag) that was successfully obtained. This may differ
    from the input if we fall back to the preview registry.
    """
    image_name = f"{base_image}:{tag}"
    is_release = version in ("latest", "release", "preview")

    if not is_release and has_local_image(client, image_name):
        print(f"Using locally cached image {image_name}", file=sys.stderr)
        return (base_image, tag)

    try:
        pull_image(client, base_image, tag, quiet)
        return (base_image, tag)
    except Exception as e:
        # Try preview registry as fallback for main workbench images
        if base_image == IMAGE:
            print(
                f"Image not found in main registry, trying preview...",
                file=sys.stderr,
            )
            try:
                pull_image(client, IMAGE_PREVIEW, tag, quiet)
                return (IMAGE_PREVIEW, tag)
            except Exception:
                pass  # Fall through to local cache check

        if has_local_image(client, image_name):
            print(
                f"Pull failed, using locally cached image {image_name}",
                file=sys.stderr,
            )
            return (base_image, tag)
        raise RuntimeError(f"Failed to pull image: {e}")


def generate_password(length: int = 16) -> str:
    """Generate a random password."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def create_test_user(container, username: str, password: str) -> str | None:
    """Create a PAM user inside the container.

    Returns the password if user was created/modified, None if using existing user.
    """
    # The 'rstudio' user is pre-created in the image
    if username == "rstudio":
        print(
            "Using existing 'rstudio' user (password not modified)", file=sys.stderr
        )
        return None

    # Check if user already exists
    exit_code, output = container.exec_run(["id", username])
    user_exists = exit_code == 0

    if not user_exists:
        exit_code, output = container.exec_run(
            ["useradd", "-m", "-s", "/bin/bash", username]
        )
        if exit_code != 0:
            raise RuntimeError(f"Failed to create user: {output.decode()}")

    exit_code, output = container.exec_run(
        ["bash", "-c", f'echo "{username}:{password}" | chpasswd']
    )
    if exit_code != 0:
        raise RuntimeError(f"Failed to set password: {output.decode()}")

    print(f"Created test user: {username}", file=sys.stderr)
    return password


def is_port_available(port: int) -> bool:
    """Check if a port is available for binding."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("", port))
            return True
        except OSError:
            return False


def find_available_port(start_port: int, max_attempts: int = 100) -> int:
    """Find an available port starting from start_port."""
    for offset in range(max_attempts):
        port = start_port + offset
        if is_port_available(port):
            return port
    raise RuntimeError(
        f"No available port found in range {start_port}-{start_port + max_attempts - 1}"
    )


def wait_for_workbench(port: int, timeout: float = 120.0) -> bool:
    """Wait for Workbench to be fully ready via health-check endpoint."""
    deadline = time.time() + timeout
    health_url = f"http://localhost:{port}/health-check"
    while time.time() < deadline:
        try:
            response = requests.get(health_url, timeout=5)
            if response.status_code == 200:
                return True
        except requests.exceptions.RequestException:
            pass
        time.sleep(2)
    return False


def execute_command(
    container,
    command: list[str],
    server_url: str,
    username: str,
    password: str | None,
) -> int:
    """Execute a command inside the Workbench container.

    Returns the command's exit code, or 126 on Docker API errors.
    """
    env = {
        "WORKBENCH_URL": server_url,
        "WORKBENCH_USER": username,
        "CONTAINER_ID": container.id,
    }
    if password:
        env["WORKBENCH_PASSWORD"] = password

    try:
        exit_code, output = container.exec_run(command, environment=env)
        sys.stdout.write(output.decode("utf-8", errors="replace"))
        return exit_code
    except docker.errors.APIError as e:
        print(f"Error: Docker API error executing command: {e}", file=sys.stderr)
        return 126


def run_workbench_command(
    container,
    command: list[str] | None,
    server_url: str,
    username: str,
    password: str | None,
) -> tuple[int, bool]:
    """Execute command against Workbench and determine container cleanup.

    Returns (exit_code, stop_container). In command mode, stop_container is True.
    In start-only mode (command is None or empty), stop_container is False.
    """
    if command:
        exit_code = execute_command(
            container, command, server_url, username, password
        )
        return (exit_code, True)
    return (0, False)


def main() -> int:
    """Main entry point."""
    args = parse_args()

    # Handle --stop mode
    if args.stop is not None:
        container_id = args.stop or os.environ.get("CONTAINER_ID")
        if not container_id:
            raise RuntimeError(
                "No container ID provided and CONTAINER_ID env var not set"
            )
        client = get_docker_client()
        try:
            container = client.containers.get(container_id)
            container.stop()
            print(f"Stopped container {container_id}", file=sys.stderr)
            return 0
        except docker.errors.NotFound:
            raise RuntimeError(f"Container not found: {container_id}")

    # Get license key
    license_key = args.license_key or os.environ.get("RSW_LICENSE")
    if not license_key:
        raise RuntimeError(
            "License key required: use --license-key or set RSW_LICENSE env var"
        )

    # Validate args
    if args.image and args.version != DEFAULT_VERSION:
        raise RuntimeError("Cannot specify both --image and --version")

    client = get_docker_client()

    # Determine image
    if args.image:
        base_image, tag = parse_image_spec(args.image)
    else:
        base_image, tag = get_docker_tag(args.version)

    base_image, tag = ensure_image(client, base_image, tag, args.version, args.quiet)
    image_name = f"{base_image}:{tag}"

    # Generate password if not provided
    password = args.password or generate_password()

    # Find available port
    host_port = find_available_port(args.port)
    if host_port != args.port:
        print(
            f"Port {args.port} in use, using {host_port} instead", file=sys.stderr
        )

    # Start container
    container_env = {
        "RSW_LICENSE": license_key,
    }
    if args.env_vars:
        for env_var in args.env_vars:
            if "=" in env_var:
                key, value = env_var.split("=", 1)
                container_env[key] = value

    print(f"Starting Workbench container...", file=sys.stderr)
    container = client.containers.run(
        image=image_name,
        detach=True,
        ports={f"{DEFAULT_PORT}/tcp": host_port},
        platform="linux/amd64",
        environment=container_env,
    )

    server_url = f"http://localhost:{host_port}"
    stop_container = True  # Default to stopping; start-only mode will set to False

    try:
        print(
            f"Waiting for Workbench to be ready on port {host_port}...", file=sys.stderr
        )
        if not wait_for_workbench(host_port, timeout=120.0):
            print("\nContainer logs:", file=sys.stderr)
            print(container.logs().decode("utf-8", errors="replace"), file=sys.stderr)
            raise RuntimeError("Workbench did not start within 120 seconds")

        # Create test user
        actual_password = create_test_user(container, args.user, password)

        # Execute user command or enter start-only mode
        exit_code, stop_container = run_workbench_command(
            container,
            args.command,
            server_url,
            args.user,
            actual_password,
        )

        if not stop_container:
            # Start-only mode: output credentials
            print(f"WORKBENCH_URL={server_url}")
            print(f"WORKBENCH_USER={args.user}")
            if actual_password:
                print(f"WORKBENCH_PASSWORD={actual_password}")
            print(f"CONTAINER_ID={container.id}")

            print(f"\nWorkbench is running at {server_url}", file=sys.stderr)
            if actual_password:
                print(f"Login with {args.user} / {actual_password}", file=sys.stderr)
            else:
                print(f"Login with {args.user} (use default password)", file=sys.stderr)
            print(f"Stop with: with-workbench --stop {container.id}", file=sys.stderr)

        return exit_code
    finally:
        if stop_container:
            container.stop()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
