# IDtracker Pipeline Controller — Mac/SSH

This version runs on the Mac and controls Firebird through SSH.

## What the GUI does

- recursively scans a Firebird top-level folder
- finds MP4, AVI, TOML, and `session_*`
- matches TOMLs to videos conservatively
- displays and edits TOML thresholds
- creates immutable remote run folders
- submits existing SLURM scripts
- uses `afterok` dependencies for post-processing
- monitors submitted jobs

## What it does not do

It does not invent an IDtracker command and does not contain placeholder
post-processing commands. You must provide the exact paths to your proven
Firebird scripts.

## Install on the Mac

```bash
unzip idtracker-pipeline-mac-ssh-v0.2.0.zip
cd idtracker-pipeline-mac
bash scripts/install_mac.sh
bash scripts/launch_mac.sh
```

## First setup

On the Connection tab:

1. enter `firebird` or the full SSH host
2. select the private key
3. enter the recursive Firebird search root
4. enter the remote run root
5. enter the exact IDtracker, BA, and fight script paths
6. click **Test SSH**
7. click **Verify Scripts**
8. save settings

See `remote_helpers/PIPELINE_SCRIPT_CONTRACT.md` for the exported variables
available to the Firebird scripts.

Test one TOML end-to-end before submitting a large batch.
