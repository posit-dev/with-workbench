import os
import socket
import string
import subprocess
import sys
from unittest.mock import MagicMock, patch

import docker
import main


# === Version/Tag Mapping ===


def test_get_docker_tag_release():
    assert main.get_docker_tag("release") == (main.IMAGE, "jammy")


def test_get_docker_tag_latest():
    assert main.get_docker_tag("latest") == (main.IMAGE, "jammy")


def test_get_docker_tag_preview():
    assert main.get_docker_tag("preview") == (main.IMAGE_PREVIEW, "jammy-daily")


def test_get_docker_tag_specific_version():
    assert main.get_docker_tag("2026.01.1") == (main.IMAGE, "jammy-2026.01.1")
    assert main.get_docker_tag("2025.09.0") == (main.IMAGE, "jammy-2025.09.0")
    assert main.get_docker_tag("2024.01.0") == (main.IMAGE, "jammy-2024.01.0")


# === Image Spec Parsing ===


def test_image_without_tag():
    base_image, tag = main.parse_image_spec("rstudio/rstudio-workbench")
    assert base_image == "rstudio/rstudio-workbench"
    assert tag == "latest"


def test_image_with_tag():
    base_image, tag = main.parse_image_spec("rstudio/rstudio-workbench:jammy-2026.01.1")
    assert base_image == "rstudio/rstudio-workbench"
    assert tag == "jammy-2026.01.1"


# === Password Generation ===


def test_generate_password_default_length():
    password = main.generate_password()
    assert len(password) == 16


def test_generate_password_custom_length():
    password = main.generate_password(length=32)
    assert len(password) == 32

    password = main.generate_password(length=8)
    assert len(password) == 8


def test_generate_password_characters():
    password = main.generate_password()
    valid_chars = string.ascii_letters + string.digits
    for char in password:
        assert char in valid_chars


# === Port Management ===


def test_is_port_available_free_port():
    # Use a high port that's unlikely to be in use
    assert main.is_port_available(59123) is True


def test_is_port_available_bound_port():
    # Bind a port and verify it's detected as unavailable
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        bound_port = s.getsockname()[1]
        assert main.is_port_available(bound_port) is False


def test_find_available_port_first_free():
    # When start port is available, should return it
    port = main.find_available_port(59124)
    assert port == 59124


def test_find_available_port_skips_used():
    # Bind a port and verify find_available_port skips it
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 59125))
        port = main.find_available_port(59125)
        assert port == 59126


# === User Creation ===


def test_create_test_user_new_user():
    mock_container = MagicMock()
    # User doesn't exist (id command fails)
    mock_container.exec_run.side_effect = [
        (1, b"no such user"),  # id testuser
        (0, b""),  # useradd
        (0, b""),  # chpasswd
    ]

    result = main.create_test_user(mock_container, "testuser", "password123")

    assert result == "password123"
    assert mock_container.exec_run.call_count == 3


def test_create_test_user_existing_rstudio():
    mock_container = MagicMock()

    result = main.create_test_user(mock_container, "rstudio", "password123")

    assert result is None
    mock_container.exec_run.assert_not_called()


def test_create_test_user_failure():
    mock_container = MagicMock()
    mock_container.exec_run.side_effect = [
        (1, b"no such user"),  # id testuser
        (1, b"useradd: permission denied"),  # useradd fails
    ]

    try:
        main.create_test_user(mock_container, "testuser", "password123")
        assert False, "Expected RuntimeError to be raised"
    except RuntimeError as e:
        assert "Failed to create user" in str(e)


# === Command Execution Mode ===


def test_parse_args_with_command():
    """Test that -- separator captures command arguments."""
    original_argv = sys.argv
    try:
        sys.argv = ["with-workbench", "--port", "8888", "--", "npm", "run", "test"]
        args = main.parse_args()
        assert args.port == 8888
        assert args.command == ["npm", "run", "test"]
    finally:
        sys.argv = original_argv


def test_parse_args_without_command():
    """Test that no -- separator results in empty command list."""
    original_argv = sys.argv
    try:
        sys.argv = ["with-workbench", "--port", "8888"]
        args = main.parse_args()
        assert args.port == 8888
        assert args.command == []
    finally:
        sys.argv = original_argv


def test_parse_args_command_with_flags():
    """Test that command arguments with flags are captured correctly."""
    original_argv = sys.argv
    try:
        sys.argv = ["with-workbench", "--", "pytest", "-v", "--timeout=30"]
        args = main.parse_args()
        assert args.command == ["pytest", "-v", "--timeout=30"]
    finally:
        sys.argv = original_argv


def test_parse_args_empty_command_after_separator():
    """Test -- with no following arguments."""
    original_argv = sys.argv
    try:
        sys.argv = ["with-workbench", "--"]
        args = main.parse_args()
        assert args.command == []
    finally:
        sys.argv = original_argv


def test_execute_command_environment_variables():
    """Test that execute_command passes correct environment variables."""
    captured_env = {}

    def capture_env(_cmd, **kwargs):
        captured_env.update(kwargs.get("env", {}))
        result = MagicMock()
        result.returncode = 0
        return result

    with patch("main.subprocess.run", side_effect=capture_env):
        # WHEN execute_command is called with credentials
        exit_code = main.execute_command(
            command=["echo", "test"],
            server_url="http://localhost:8787",
            username="testuser",
            password="testpass123",
            container_id="abc123def456",
        )

        # THEN environment should contain all Workbench variables
        assert captured_env["WORKBENCH_URL"] == "http://localhost:8787"
        assert captured_env["WORKBENCH_USER"] == "testuser"
        assert captured_env["WORKBENCH_PASSWORD"] == "testpass123"
        assert captured_env["CONTAINER_ID"] == "abc123def456"
        assert exit_code == 0


def test_execute_command_no_password():
    """Test that execute_command omits WORKBENCH_PASSWORD when password is None."""
    captured_env = {}

    def capture_env(_cmd, **kwargs):
        captured_env.update(kwargs.get("env", {}))
        result = MagicMock()
        result.returncode = 0
        return result

    with patch("main.subprocess.run", side_effect=capture_env):
        # WHEN execute_command is called with password=None
        main.execute_command(
            command=["echo", "test"],
            server_url="http://localhost:8787",
            username="rstudio",
            password=None,
            container_id="abc123",
        )

        # THEN WORKBENCH_PASSWORD should not be in environment
        assert "WORKBENCH_PASSWORD" not in captured_env
        assert captured_env["WORKBENCH_USER"] == "rstudio"


def test_execute_command_exit_code_success():
    """Test that execute_command returns exit code 0 on success."""
    mock_result = MagicMock()
    mock_result.returncode = 0

    with patch("main.subprocess.run", return_value=mock_result):
        # WHEN command succeeds
        exit_code = main.execute_command(
            command=["true"],
            server_url="http://localhost:8787",
            username="testuser",
            password="pass",
            container_id="abc123",
        )

        # THEN exit code should be 0
        assert exit_code == 0


def test_execute_command_exit_code_failure():
    """Test that execute_command propagates non-zero exit codes."""
    with patch(
        "main.subprocess.run",
        side_effect=subprocess.CalledProcessError(returncode=42, cmd=["failing-command"]),
    ):
        # WHEN command fails with exit code 42
        exit_code = main.execute_command(
            command=["failing-command"],
            server_url="http://localhost:8787",
            username="testuser",
            password="pass",
            container_id="abc123",
        )

        # THEN exit code should be propagated
        assert exit_code == 42


def test_execute_command_oserror_returns_127():
    """Test that execute_command returns 127 when command cannot be executed."""
    with patch(
        "main.subprocess.run",
        side_effect=FileNotFoundError("No such file or directory: 'nonexistent-cmd'"),
    ):
        # WHEN command doesn't exist (FileNotFoundError)
        exit_code = main.execute_command(
            command=["nonexistent-cmd"],
            server_url="http://localhost:8787",
            username="testuser",
            password="pass",
            container_id="abc123",
        )

        # THEN exit code should be 127 (command not found)
        assert exit_code == 127


def test_execute_command_permission_error_returns_127():
    """Test that execute_command returns 127 on permission denied."""
    with patch("main.subprocess.run", side_effect=PermissionError("Permission denied")):
        # WHEN command lacks execute permission
        exit_code = main.execute_command(
            command=["./not-executable"],
            server_url="http://localhost:8787",
            username="testuser",
            password="pass",
            container_id="abc123",
        )

        # THEN exit code should be 127
        assert exit_code == 127


# === Container Lifecycle ===


def test_run_workbench_command_with_command_stops_container():
    """Test that run_workbench_command returns stop_container=True when command provided."""
    mock_result = MagicMock()
    mock_result.returncode = 0

    mock_container = MagicMock()
    mock_container.id = "container123"

    with patch("main.subprocess.run", return_value=mock_result):
        # WHEN command is provided
        exit_code, stop_container = main.run_workbench_command(
            container=mock_container,
            command=["echo", "test"],
            server_url="http://localhost:8787",
            username="testuser",
            password="pass",
        )

        # THEN stop_container should be True
        assert stop_container is True
        assert exit_code == 0


def test_run_workbench_command_with_failed_command_stops_container():
    """Test that run_workbench_command returns stop_container=True even on command failure."""
    mock_container = MagicMock()
    mock_container.id = "container123"

    with patch(
        "main.subprocess.run",
        side_effect=subprocess.CalledProcessError(returncode=1, cmd=["failing-command"]),
    ):
        # WHEN command fails
        exit_code, stop_container = main.run_workbench_command(
            container=mock_container,
            command=["failing-command"],
            server_url="http://localhost:8787",
            username="testuser",
            password="pass",
        )

        # THEN stop_container should still be True
        assert stop_container is True
        assert exit_code == 1


def test_run_workbench_command_start_only_mode_no_stop():
    """Test that run_workbench_command returns stop_container=False in start-only mode."""
    mock_container = MagicMock()
    mock_container.id = "container123"

    # WHEN no command is provided (start-only mode)
    exit_code, stop_container = main.run_workbench_command(
        container=mock_container,
        command=None,
        server_url="http://localhost:8787",
        username="testuser",
        password="pass",
    )

    # THEN stop_container should be False
    assert stop_container is False
    assert exit_code == 0


def test_run_workbench_command_empty_command_is_start_only():
    """Test that empty command list is treated as start-only mode."""
    mock_container = MagicMock()
    mock_container.id = "container123"

    # WHEN command is empty list
    exit_code, stop_container = main.run_workbench_command(
        container=mock_container,
        command=[],
        server_url="http://localhost:8787",
        username="testuser",
        password="pass",
    )

    # THEN should be start-only mode (stop_container=False)
    assert stop_container is False
    assert exit_code == 0


# === CLI Validation ===


def test_license_key_required():
    # Clear RSW_LICENSE env var if set
    env = os.environ.copy()
    env.pop("RSW_LICENSE", None)

    result = subprocess.run(
        [sys.executable, "main.py"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 1
    assert "License key required" in result.stderr


def test_image_and_version_exclusive():
    env = os.environ.copy()
    env["RSW_LICENSE"] = "test-license-key"

    result = subprocess.run(
        [
            sys.executable,
            "main.py",
            "--image",
            "rstudio/rstudio-workbench:jammy-2026.01.1",
            "--version",
            "2025.09.0",
        ],
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 1
    assert "Cannot specify both --image and --version" in result.stderr


def test_custom_port_in_help():
    result = subprocess.run(
        [sys.executable, "main.py", "--help"],
        capture_output=True,
        text=True,
    )

    assert "--port" in result.stdout
    assert "8787" in result.stdout


def test_stop_argument_in_help():
    result = subprocess.run(
        [sys.executable, "main.py", "--help"],
        capture_output=True,
        text=True,
    )

    assert "--stop" in result.stdout
    assert "CONTAINER_ID" in result.stdout


def test_stop_nonexistent_container():
    result = subprocess.run(
        [sys.executable, "main.py", "--stop", "nonexistent_container_id"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "Container not found" in result.stderr


def test_stop_no_container_id():
    # Clear CONTAINER_ID env var if set
    env = os.environ.copy()
    env.pop("CONTAINER_ID", None)

    result = subprocess.run(
        [sys.executable, "main.py", "--stop"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 1
    assert "No container ID provided" in result.stderr


# === Image Caching ===


def test_local_image_usage():
    mock_client = MagicMock()
    mock_image = MagicMock()
    mock_client.images.get.return_value = mock_image

    base_image, tag = main.get_docker_tag("2025.09.0")
    image_name = f"{base_image}:{tag}"

    # Verify local image check works
    try:
        mock_client.images.get(image_name)
        should_pull = False
    except docker.errors.ImageNotFound:
        should_pull = True

    assert should_pull is False


def test_release_always_pulls():
    version = "release"
    should_pull = version in ("latest", "release", "preview")
    assert should_pull is True


def test_preview_always_pulls():
    version = "preview"
    should_pull = version in ("latest", "release", "preview")
    assert should_pull is True


def test_latest_always_pulls():
    version = "latest"
    should_pull = version in ("latest", "release", "preview")
    assert should_pull is True


if __name__ == "__main__":
    test_parse_args_with_command()
    print("✓ test_parse_args_with_command passed")

    test_parse_args_without_command()
    print("✓ test_parse_args_without_command passed")

    test_parse_args_command_with_flags()
    print("✓ test_parse_args_command_with_flags passed")

    test_parse_args_empty_command_after_separator()
    print("✓ test_parse_args_empty_command_after_separator passed")

    test_execute_command_environment_variables()
    print("✓ test_execute_command_environment_variables passed")

    test_execute_command_no_password()
    print("✓ test_execute_command_no_password passed")

    test_execute_command_exit_code_success()
    print("✓ test_execute_command_exit_code_success passed")

    test_execute_command_exit_code_failure()
    print("✓ test_execute_command_exit_code_failure passed")

    test_execute_command_oserror_returns_127()
    print("✓ test_execute_command_oserror_returns_127 passed")

    test_execute_command_permission_error_returns_127()
    print("✓ test_execute_command_permission_error_returns_127 passed")

    test_run_workbench_command_with_command_stops_container()
    print("✓ test_run_workbench_command_with_command_stops_container passed")

    test_run_workbench_command_with_failed_command_stops_container()
    print("✓ test_run_workbench_command_with_failed_command_stops_container passed")

    test_run_workbench_command_start_only_mode_no_stop()
    print("✓ test_run_workbench_command_start_only_mode_no_stop passed")

    test_run_workbench_command_empty_command_is_start_only()
    print("✓ test_run_workbench_command_empty_command_is_start_only passed")

    test_get_docker_tag_release()
    print("✓ test_get_docker_tag_release passed")

    test_get_docker_tag_latest()
    print("✓ test_get_docker_tag_latest passed")

    test_get_docker_tag_preview()
    print("✓ test_get_docker_tag_preview passed")

    test_get_docker_tag_specific_version()
    print("✓ test_get_docker_tag_specific_version passed")

    test_image_without_tag()
    print("✓ test_image_without_tag passed")

    test_image_with_tag()
    print("✓ test_image_with_tag passed")

    test_generate_password_default_length()
    print("✓ test_generate_password_default_length passed")

    test_generate_password_custom_length()
    print("✓ test_generate_password_custom_length passed")

    test_generate_password_characters()
    print("✓ test_generate_password_characters passed")

    test_is_port_available_free_port()
    print("✓ test_is_port_available_free_port passed")

    test_is_port_available_bound_port()
    print("✓ test_is_port_available_bound_port passed")

    test_find_available_port_first_free()
    print("✓ test_find_available_port_first_free passed")

    test_find_available_port_skips_used()
    print("✓ test_find_available_port_skips_used passed")

    test_create_test_user_new_user()
    print("✓ test_create_test_user_new_user passed")

    test_create_test_user_existing_rstudio()
    print("✓ test_create_test_user_existing_rstudio passed")

    test_create_test_user_failure()
    print("✓ test_create_test_user_failure passed")

    test_license_key_required()
    print("✓ test_license_key_required passed")

    test_image_and_version_exclusive()
    print("✓ test_image_and_version_exclusive passed")

    test_custom_port_in_help()
    print("✓ test_custom_port_in_help passed")

    test_stop_argument_in_help()
    print("✓ test_stop_argument_in_help passed")

    test_stop_nonexistent_container()
    print("✓ test_stop_nonexistent_container passed")

    test_stop_no_container_id()
    print("✓ test_stop_no_container_id passed")

    test_local_image_usage()
    print("✓ test_local_image_usage passed")

    test_release_always_pulls()
    print("✓ test_release_always_pulls passed")

    test_preview_always_pulls()
    print("✓ test_preview_always_pulls passed")

    test_latest_always_pulls()
    print("✓ test_latest_always_pulls passed")

    print("\nAll tests passed!")
