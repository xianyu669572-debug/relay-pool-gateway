# Contributing

## 开发流程

1. 先读 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) 与 [docs/BASELINE.md](docs/BASELINE.md)。
2. TDD：先写失败测试 → 实现 → 全量测试通过。
3. 运行：
   ```powershell
   python -m unittest discover -s tests -v
   python scripts/benchmark.py --cases tests/benchmark/cases.jsonl --holdout tests/benchmark/holdout.jsonl
   python scripts/scan_repo.py
   ```
4. 提交信息用 Conventional Commits。

## 禁止事项

- 禁止提交真实密钥、真实 Windows 用户名、真实 Obsidian 库路径、真实个人信息；
- 禁止加入绕过访问控制、破解、越权功能；
- 禁止引入第三方运行时依赖（保持零依赖；如需，先开 issue 讨论）；
- 基准数据只允许合成数据。

## 行为准则

见 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。
