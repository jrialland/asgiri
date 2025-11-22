#!/usr/bin/env python3
"""Setup script to install the Git pre-commit hook.

This script copies the pre-commit hook to .git/hooks/ and makes it executable.
Run this once to enable automatic pre-commit validation.
"""

import os
import stat
import sys
from pathlib import Path


def main() -> int:
    """Install the pre-commit hook.

    Returns:
        0 if successful, 1 otherwise
    """
    script_dir = Path(__file__).parent
    project_root = script_dir.parent

    # Path to the git hooks directory
    hooks_dir = project_root / ".git" / "hooks"

    if not hooks_dir.exists():
        print("❌ Error: .git/hooks directory not found.")
        print("   Make sure you're running this from the project root.")
        return 1

    # Create the hook file path
    hook_file = hooks_dir / "pre-commit"

    # Create the hook content
    hook_content = """#!/bin/sh
# Git pre-commit hook that runs code quality checks
# This hook runs the pre_commit.py script before allowing a commit

echo "Running pre-commit checks..."

# Run the pre-commit script
python scripts/pre_commit.py

# Capture the exit code
EXIT_CODE=$?

# If the script failed, prevent the commit
if [ $EXIT_CODE -ne 0 ]; then
    echo ""
    echo "❌ Pre-commit checks failed. Commit aborted."
    echo "Please fix the issues above and try again."
    exit 1
fi

echo ""
echo "✅ All pre-commit checks passed. Proceeding with commit."
exit 0
"""

    try:
        # Write the hook file
        hook_file.write_text(hook_content, encoding="utf-8")

        # Make it executable (on Unix-like systems)
        if os.name != "nt":  # Not Windows
            st = os.stat(hook_file)
            os.chmod(hook_file, st.st_mode | stat.S_IEXEC)

        print("✅ Pre-commit hook installed successfully!")
        print(f"   Location: {hook_file}")
        print()
        print("The hook will now run automatically before each commit.")
        print("To bypass the hook temporarily, use: git commit --no-verify")

        return 0

    except (IOError, OSError) as e:
        print(f"❌ Error installing pre-commit hook: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
