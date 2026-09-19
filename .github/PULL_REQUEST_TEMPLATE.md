## 变更

## 测试

- [ ] `python -m unittest discover -s tests -v` 全绿
- [ ] `python scripts/benchmark.py --cases tests/benchmark/cases.jsonl --holdout tests/benchmark/holdout.jsonl` 通过
- [ ] `python scripts/scan_repo.py` 通过

## 合规

- 未包含真实密钥/真实路径/真实个人数据；
- 未引入绕过访问控制功能；
- 未引入第三方运行时依赖（如有，说明理由）。
