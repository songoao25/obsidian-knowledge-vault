# Customization

Rerun `python3 deploy.py` to change the schedule or provider. Use
`--template general` or `--template legal` only when creating a new empty Vault.

Do not manually add an unapproved directory to the taxonomy while automation is
running. First update the taxonomy and the matching template rule, then validate
the change in a disposable Vault. Keep API keys out of configuration files.

