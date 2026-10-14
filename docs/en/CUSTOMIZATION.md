# Customization

Rerun the installer to change the schedule or provider: `python3 deploy.py` in an
extracted package, or `python3 scripts/deploy.py` in a cloned repository. Use
`--template general` or `--template legal` only when creating a new empty Vault.

Do not manually add an unapproved directory to the taxonomy while automation is
running. First update the taxonomy and the matching template rule, then validate
the change in a disposable Vault. Keep API keys out of configuration files.

