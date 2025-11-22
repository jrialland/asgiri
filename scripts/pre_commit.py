#!/usr/bin/env python3
"""Pre-commit script that runs code quality checks and tests.

This script performs the following checks:
1. Ensure uv is installed and sync dependencies
2. Run black on asgiri and tests folders
3. Run isort on these folders
4. Run mypy on the asgiri package
5. Run bandit on the asgiri package
6. Run the unit tests using pytest

All commands are run within the project's virtual environment using uv.
"""

import subprocess
import sys
from pathlib import Path


def run_command(
    description: str,
    command: list[str],
    use_uv: bool = True,
    python_exe: Path | None = None,
) -> bool:
    """Run a command and return True if successful, False otherwise.

    Args:
        description: Description of what the command does
        command: Command to run as a list of strings
        use_uv: If True, run command with 'uv run' prefix
        python_exe: Path to Python executable (used when not using uv)

    Returns:
        True if command succeeded, False otherwise
    """
    print(f"\n{'=' * 70}")
    print(f"Running: {description}")
    print(f"{'=' * 70}")

    # Prepend 'uv run' if requested, otherwise use venv python with -m
    if use_uv:
        command = ["uv", "run"] + command
    elif python_exe:
        # Use python -m <module> for venv execution
        command = [str(python_exe), "-m"] + command

    try:
        subprocess.run(command, check=True, capture_output=False, text=True)
        print(f"✓ {description} - PASSED")
        return True
    except subprocess.CalledProcessError:
        print(f"✗ {description} - FAILED")
        return False
    except FileNotFoundError:
        print(f"✗ {description} - COMMAND NOT FOUND")
        print("  Make sure the required package is installed")
        return False


def main() -> int:
    """Run all pre-commit checks.

    Returns:
        0 if all checks passed, 1 otherwise
    """
    # Get the project root directory
    script_dir = Path(__file__).parent
    project_root = script_dir.parent

    print(f"Project root: {project_root}")
    print("Starting pre-commit checks...\n")

    # Check if uv is available
    print("=" * 70)
    print("Checking for uv...")
    print("=" * 70)

    use_uv = False
    python_exe = None

    try:
        result = subprocess.run(
            ["uv", "--version"], check=True, capture_output=True, text=True
        )
        print(f"✓ uv is installed: {result.stdout.strip()}")
        use_uv = True

        # Sync dependencies with uv
        if not run_command(
            "Syncing dependencies with uv",
            ["uv", "sync", "--group", "dev"],
            use_uv=False,
            python_exe=None,
        ):
            print("✗ Failed to sync dependencies")
            return 1

    except (subprocess.CalledProcessError, FileNotFoundError):
        print("⚠ uv is not installed, checking for .venv...")

        # Check if .venv exists
        venv_path = project_root / ".venv"
        if venv_path.exists():
            print(f"✓ Using virtual environment: {venv_path}")
            # On Windows, check for Scripts/python.exe
            if (venv_path / "Scripts" / "python.exe").exists():
                python_exe = venv_path / "Scripts" / "python.exe"
            # On Unix, check for bin/python
            elif (venv_path / "bin" / "python").exists():
                python_exe = venv_path / "bin" / "python"
            else:
                print("✗ Virtual environment found but python not located")
                return 1
            print(f"  Python: {python_exe}")
        else:
            print("✗ Neither uv nor .venv found!")
            print("  Please either:")
            print("  1. Install uv: https://docs.astral.sh/uv/")
            print("  2. Create a virtual environment and install dev deps")
            return 1

    # Run all checks
    checks = [
        ("Black formatting on asgiri", ["black", "asgiri"]),
        ("Black formatting on tests", ["black", "tests"]),
        ("isort on asgiri", ["isort", "asgiri"]),
        ("isort on tests", ["isort", "tests"]),
        ("mypy type checking on asgiri", ["mypy", "asgiri"]),
        ("bandit security check on asgiri", ["bandit", "-r", "asgiri"]),
        (
            "pytest unit tests",
            ["pytest", "tests", "-m", "not slow", "--tb=short"],
        ),
    ]

    results = []
    for description, command in checks:
        success = run_command(
            description, command, use_uv=use_uv, python_exe=python_exe
        )
        results.append((description, success))

    # # Run optional safety check (may fail if not authenticated)
    # # Safety requires auth and only supports requirements.txt for fixes
    # # For pyproject.toml projects, run without --apply-fixes
    # print(f"\n{'=' * 70}")
    # print("Running: safety dependency vulnerability scan (optional)")
    # print(f"{'=' * 70}")
    # print(
    #     "Note: Safety requires authentication. "
    #     "This check is optional and won't fail the build."
    # )
    #
    # safety_command = ["safety", "scan", "--target", "."]
    # if use_uv:
    #     safety_command = ["uv", "run"] + safety_command
    # elif python_exe:
    #     safety_command = [str(python_exe), "-m"] + safety_command
    #
    # try:
    #     subprocess.run(
    #         safety_command, check=False, capture_output=False, text=True
    #     )
    #     print("✓ safety dependency vulnerability scan - COMPLETED")
    # except (subprocess.CalledProcessError, FileNotFoundError):
    #     print(
    #         "⚠ safety scan skipped (not authenticated or not installed)"
    #     )
    #     print("  Run 'safety auth login' to enable vulnerability scanning")

    # Print summary
    print(f"\n{'=' * 70}")
    print("SUMMARY")
    print(f"{'=' * 70}")

    all_passed = True
    for description, success in results:
        status = "✓ PASSED" if success else "✗ FAILED"
        print(f"{status}: {description}")
        if not success:
            all_passed = False

    print(f"{'=' * 70}\n")

    if all_passed:
        print("✓ All checks passed!")
        return 0
    else:
        print("✗ Some checks failed. Please fix the issues above.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
