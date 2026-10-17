# flashloan-rs build

`pyproject.toml` pins `maturin>=1.5,<2.0` for reproducible builds. The Python
bridge keeps the seven execution primitives unchanged; `rust_available()`
stays `False` on machines without the compiled extension.

Local verification:

```powershell
.\scripts\build_release.ps1 -Target develop
python -c "import flashloan_rs; print(flashloan_rs.__name__)"
```

Release wheel:

```powershell
.\scripts\build_release.ps1 -Target wheel
```

The script prints the active Python, Rust and maturin versions. Record those
versions with every release artifact so a later rebuild can be compared.
