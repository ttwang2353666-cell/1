# 参与贡献 / Contributing

欢迎反馈问题和提交代码。English speakers are welcome too: issues and pull requests in English are fine.

## 反馈问题 / Reporting problems

这是直接控制硬件的程序，不同机型的 BIOS 差别很大，请在问题中附上：

1. 机型（例如 OMEN 16-wf0xxx）、发行版与内核版本（`uname -r`）；
2. `omenhub probe` 的输出（服务没有运行时用 `sudo omenhubd --probe`）；
3. `journalctl -u omenhubd -b` 中的相关日志；
4. 操作步骤、预期结果和实际结果。

Please include the laptop model, distribution and kernel, the output of `omenhub probe` (or `sudo omenhubd --probe` when the daemon is not running), the relevant `journalctl -u omenhubd -b` lines, and the steps to reproduce.

## 开发 / Development

不需要 HP 笔记本，也不需要 root，模拟硬件即可运行完整的服务、命令行与托盘程序：

```bash
python3 -m omenhub.daemon --mock --config /tmp/omenhub-demo/config.json --socket /tmp/omenhub-demo/omenhub.sock
```

```bash
python3 -m omenhub --socket /tmp/omenhub-demo/omenhub.sock status
```

提交前请运行测试与检查（CI 会运行同样的检查）：

```bash
python3 -m pytest
```

```bash
python3 -m pyflakes omenhub tests
```

```bash
shellcheck install.sh uninstall.sh
```

```bash
reuse lint
```

## 约定 / Conventions

- 代码风格与注释语言跟随周围的代码（注释使用中文）。
- 界面文字放在 `omenhub/i18n.py`，必须同时提供简体中文、繁体中文与英文，测试会检查。
- BIOS 命令以原版 OmenSuperHub 的行为为准；任何写入硬件的改动都要在 `MockBios` / `FakeMsrDevice` 上补充测试。
- 不要在没有用户操作的情况下写入新的硬件设置；涉及电压、功率的功能要有范围限制和恢复手段。

## 发布新版本 / Releasing

1. 修改 `pyproject.toml` 与 `omenhub/__init__.py` 中的版本号，并在 `CHANGELOG.md` 中加入对应版本的说明；
2. 合并到 main 后，在 GitHub 的 Actions 页面手动运行 “Release” 工作流（或推送 `v版本号` 标签）。工作流会检查版本号、运行测试、构建源码包与 wheel，并创建标签和 Release。

Bump the version in `pyproject.toml` and `omenhub/__init__.py`, add a `CHANGELOG.md` entry, merge to main, then run the
“Release” workflow from the Actions tab (or push a `v<version>` tag).

## 许可证 / License

本项目以 GPL-3.0-only 发布，提交的代码同样以该许可证授权。新文件请加上 SPDX 文件头（参考现有文件），并保证 `reuse lint` 通过；移植自原版的代码需要保留原作者的版权行。

Contributions are licensed under GPL-3.0-only. Add SPDX headers to new files (see the existing ones), keep `reuse lint` passing, and keep the original author's copyright line on code ported from OmenSuperHub.
