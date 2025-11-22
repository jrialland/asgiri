# Scripts

This folder contains utility scripts for the asgiri project.

## pre_commit.py

Runs code quality checks and tests before committing:

1. **Black** formatting on `asgiri` and `tests` folders
2. **isort** import sorting on these folders
3. **mypy** type checking on the `asgiri` package
4. **bandit** security check on the `asgiri` package
5. **pytest** unit tests

### Usage

```bash
python scripts/pre_commit.py
```

## setup_git_hooks.py

Installs the Git pre-commit hook to automatically run `pre_commit.py` before each commit.

### Setup

Run once to enable automatic pre-commit validation:

```bash
python scripts/setup_git_hooks.py
```

### Bypass Hook

If you need to commit without running the checks (not recommended):

```bash
git commit --no-verify
```

## Requirements

Make sure you have the following tools installed:

```bash
pip install black isort mypy bandit pytest
```
