# Troubleshooting

- **Target is not empty:** choose a new empty folder. v1.2 does not migrate an
  existing Vault.
- **Prerequisite missing:** install only the command named by the installer,
  then rerun it.
- **Provider check fails:** verify local sign-in or enter the key in the hidden
  local prompt; do not share the key.
- **Scheduled task problem:** rerun the installer after fixing the shown error,
  or use `--uninstall` to remove only the scheduled task.

When opening an issue, include the non-sensitive error message, platform, and
version. Do not attach Vault exports, logs containing note content, or secrets.

