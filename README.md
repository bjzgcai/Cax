# Cactus-RaMAx

Cactus-RaMAx 帮助你在 `cactus-prepare` 生成的多轮对齐计划中，挑选部分轮次改用 RaMAx，再一键执行完整流程。项目提供一个 Textual 驱动的交互式终端界面：输入 `cactus-prepare` 指令即可解析输出、浏览每个步骤、切换 RaMAx 替换，并最终执行或导出命令列表。

## 环境准备

推荐使用 Conda 新建独立环境，并从源代码安装：

```bash
conda create -n cax python=3.10 -y
conda activate cax

# 安装依赖并以开发模式挂载
pip install -e .
```

如需在隔离环境之外使用，可将仓库打包后安装：

```bash
python -m build
pip install dist/cactus_ramax-*.whl
```

## 快速上手

### 1. 启动交互式 UI

直接运行：

```bash
cax ui
```

- 若未提供 `--prepare-args` 或 `--from-file`，程序会先弹出一个 Textual 输入框，提示你填写完整的 `cactus-prepare` 指令（例如 `cactus-prepare examples/... --outDir ...`）。
- 按 Enter 后，工具会执行该指令、解析输出，并进入计划编辑界面。
- 仍支持脚本化使用：
  ```bash
  cax ui --prepare-args "examples/evolverMammals.txt --outDir steps-output --outSeqFile ... --outHal ... --jobStore jobstore"
  ```
  或提供现有输出：
  ```bash
  cax ui --from-file steps-output/prepare_output.txt
  ```

### 2. 在 UI 中操作

- 列表展示所有对齐轮次，按空格切换是否使用 RaMAx；
- 面板展示当前轮次的原始命令或生成的 RaMAx 命令；
- `R`：立即执行完整计划；
- `S`：导出所有需要运行的命令到 `ramax_commands.txt`；
- `P`：刷新总览；
- `Q`：退出。

保存时会在 `--outDir` 或当前目录下创建 `ramax_commands.txt`，内容为逐行排列的 shell 命令，可直接用于批量执行或进一步修改。

## 日志与调试

- `cactus-prepare` 的原始输出会保存为 `steps-output/cax_prepare_debug.txt`（若 `--outDir` 变化则跟随调整），便于复现与排错。
- 执行阶段会沿用原始计划中的日志目录（如 `steps-output/logs/`），每个步骤输出各自的 log 文件。

## 反馈

欢迎提交 issue 或 PR，帮助我们继续完善 Cactus 与 RaMAx 的联合流程体验。
