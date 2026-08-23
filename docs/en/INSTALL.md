# Install

## Before you start

Use a new, empty folder for this Vault. Do not point the installer at a Vault
with existing notes. Install Obsidian and Python 3.11 or later first.

## Run the installer

Clone the repository or download and unzip the package, then run:

```sh
python3 deploy.py
```

Choose the general or legal template, the run times, and an AI provider. The
installer displays its plan before changing anything. It stops when the target
folder is not empty, a prerequisite is missing, or no selected provider works.

## AI credentials

Enter a key only into the hidden prompt in your local terminal. Never place a
key in a command, a file, an issue, or an AI conversation.

