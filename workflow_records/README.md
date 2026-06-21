# Workflow Records

这个目录用于保存本地 forecast workflow 的可重建记录。

运行 `save_forecast_run()` 时，系统会自动在这里写一份 workflow artifact：

- `*.json`：完整结构化记录，包含 market、snapshot、parser features、evidence、forecast。
- `*.md`：方便人工阅读的摘要，包含 query summary、top evidence、forecast reasoning。

这些运行记录默认被本目录 `.gitignore` 忽略，避免把本地证据、模型输出、运行状态误推到 GitHub。

可用环境变量覆盖目录：

```bash
WORKFLOW_RECORDS_DIR=/path/to/local/workflow_records
```
