/# Repository Guidelines

## Project Structure & Module Organization
- `camellia/api/` holds NetEase/WPFLauncher API clients.
- `camellia/crypto/` contains crypto helpers (AES/ChaCha/RSA/Skip32).
- `camellia/mc/` contains Minecraft protocol + proxy logic.
- `camellia/models/` provides API response dataclasses.
- `camellia/gui/` implements the PySide6 GUI.
- `camellia_cli.py` is the CLI entrypoint and interactive flow.
- `camellia_gui.py` launches the Qt GUI.
- `USAGE.md` documents runtime behavior and login notes.
- `requirements.txt` lists runtime dependencies.
- `__pycache__/` is local bytecode; do not commit.

## Build, Test, and Development Commands
```bash
python3 -m pip install -r requirements.txt
```
Installs runtime dependencies (`pycryptodome`, `PySide6`).
```bash
python3 camellia_cli.py
```
Runs the CLI; choose no-proxy or local-proxy mode as prompted.
```bash
python3 camellia_gui.py
```
Runs the cross-platform GUI.

## Coding Style & Naming Conventions
- Python 3.9+; 4-space indentation and type hints are used throughout.
- Use `snake_case` for functions/variables, `PascalCase` for classes.
- Keep modules focused by concern (e.g., crypto vs. network/proxy).
- No formatter/linter config is present; match existing import grouping and formatting.

## Testing Guidelines
- No test suite or framework is configured in this repository.
- If you add tests, prefer `tests/` with `pytest` and `test_*.py` names, and document the command in `USAGE.md`.

## Commit & Pull Request Guidelines
- No Git history is present here, so commit conventions are unspecified.
- Use short, imperative commit messages (e.g., "Add proxy retry") and include issue IDs when available.
- PRs should state behavior changes, how to run (`python3 camellia_cli.py`, `python3 camellia_gui.py`), and any auth/proxy impacts.

## Configuration & Security Notes
- Cookie login expects a file containing a `sauth_json` JSON blob; keep credentials out of the repo.
- Proxy mode opens a local TCP listener; note port/address changes in PRs.
