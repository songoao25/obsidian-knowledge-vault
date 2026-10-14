# Install

## Before you start

Use a new, empty folder for this Vault. Do not point the installer at a Vault
with existing notes. Install Obsidian and Python 3.11 or later first.

## Run the installer

From a cloned repository, run:

```sh
python3 scripts/deploy.py
```

From a downloaded package, unzip it and run `python3 deploy.py` inside the
extracted top-level folder.

Choose the general or legal template, the run times, and an AI provider. The
installer displays its plan before changing anything. It stops when the target
folder is not empty, a prerequisite is missing, or no selected provider works.

## AI credentials

Enter a key only into the hidden prompt in your local terminal. Never place a
key in a command, a file, an issue, or an AI conversation.

