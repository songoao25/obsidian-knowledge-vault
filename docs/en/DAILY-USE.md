# Daily use

Put new notes, links, or files in `00 收件箱`. After a file has stopped changing,
the scheduled maintenance run examines it and records the result. It preserves
complete source material by default and does not silently merge notes, rewrite
core conclusions, or create content in protected manual-only areas.

Review results in `90 系统/94 维护记录`. Use Obsidian normally for writing and
editing. To stop scheduled automation without deleting your notes, run:

```sh
python3 deploy.py --uninstall
```

