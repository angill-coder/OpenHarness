# 回归看板 — report-assistant

## 分数曲线

| 版本 | 父版 | 打开的 directive | dev overall | test overall | 红线失败 |
|------|------|------------------|-------------|--------------|---------|
| v0 | - | - | 2.58 | 2.46 | 3 |
| v1 | v0 | require_citation | 3.09 | 3.09 | 2 |
| v2 | v1 | require_citation, require_metric_definitions | 3.21 | 3.21 | 2 |
| v3 | v2 | require_citation, require_metric_definitions, verifier_check_omissions | 4.05 | 4.05 | 0 |
| v4 | v3 | require_citation, require_metric_definitions, verifier_check_omissions, require_risk_and_next_step | 4.55 | 4.55 | 0 |
| v5 | v4 | require_citation, require_metric_definitions, verifier_check_omissions, require_risk_and_next_step, match_audience_length | 4.75 | 4.75 | 0 |
