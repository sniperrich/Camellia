nCamellia CLI

Requirements
- Python 3.9+
- pycryptodome (`pip install -r requirements.txt`)
- PySide6 for the GUI (`pip install -r requirements.txt`)

Run
```
python3 camellia_cli.py
```

GUI
```
python3 camellia_gui.py
```

Notes
- Cookie login expects a file like `test_sauth` containing a JSON `sauth_json` blob.
- 4399 login requires a working username/password.
- The GUI auto-saves login accounts at `~/.camellia/accounts.json`; passwords are stored in plain text only if “记住密码（明文存储）” is enabled.
- The CLI now offers two modes:
  - No proxy: prints the remote server address.
  - Local proxy: starts a local TCP proxy and prints a local address to connect to.
- In proxy mode, Yggdrasil join is attempted automatically when the server sends an encryption request.
- Stop the proxy with Ctrl+C.
