# M3本机验证记录

日期：2026-10-27（Australia/Sydney）。范围：确定性规划、平滑决策、生命周期、合成世界、六步编排、SQLite持久化及安装包结构。

## 真正pytest结果

| 项目 | 结果 |
| --- | --- |
| Python / pytest | 3.12.3 / 9.1.1，项目专用`.venv` |
| 用户交付版本 | 160 passed in 30.90s（原135 + M3新增25） |
| 本机修复后 | 176 passed in 30.55s |
| 新增回归 | 16个，修复前其中15个复现缺口 |

项目根目录复跑：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

原M3测试见[运行测试](../../tests/test_m3_runtime.py)，新增回归见[边界测试](../../tests/test_m3_regressions.py)。预算截断DP、小人群资源/现金/资产/保底对账、重跑与倒序玩家复现、summary/full聚合一致、流失冻结和部分获取检查均通过。

## 三条假设的决定

接受为`scenario_resolve_v1_assumed`显式假设，并写入[情景模块](../design/modules/scenario_config.md)：

1. `HP_t = hp_index * (1 + hp_growth) ** (t - 1)`：每个情景区间递增，首期参考值。默认区间42天；不是未声明的按日连续增长。
2. `B_ref_k = B_ref_first * (1 + growth_per_debut_step) ** (k - 1)`：k只计稳定事件顺序中的新主输出，支援不增加k。
3. 普通型贯穿占比0、贯穿型1：理想类型，不是对真实角色混合伤害占比的实测结论。

本轮未调整这些数值公式或配置JSON。若以后改变增长单位或类型映射，须升级解析版本并生成新科学身份。

## 本机修复

| 缺口 | 修复与验证 |
| --- | --- |
| 实际科学版本仅在运行记录中补写 | 新增共享versions.py；config_id哈希前纳入planner/runtime/scenario_resolver，版本变化测试确认ID改变、RNG不变；Runtime拒绝过期身份 |
| writer失败时单抽内存已生效 | 完整事件包与队列写入成功后提交钱包、库存、保底和来源批次索引；注入writer失败验证全部不变 |
| 按单行分块拆开现金/出货互相外键 | 可嵌套atomic_buffer，在完整事件边界检查阈值；chunk_rows=1/2验证外键一致，投影失败撤回缓冲 |
| 回滚仍累计已写行数 | 仅COMMIT成功后增加row_counts，失败事务不残留 |
| 同库重跑覆盖运行状态或撞主键 | 已存在run_id在修改前拒绝，包括不同attempt；保留原completed状态，重跑使用独立数据库 |
| catalog摘要漏掉实际维度语义 | 纳入时间跨度、支援增益、保底组及目标路径等输入，同主键不同维度内容报错 |
| 当期新角色冻结时gear_value为NULL | PostEval前物化所有持有角色装备值；强制首期获取并流失，确认冻结/持有非NULL，后续装备不增长 |

版本：公共契约仍为1.1.0，规划`planner_dp_v1`，编排`runtime_six_step_v2`，解析`scenario_resolve_v1_assumed`，写入器`storage_writer_v2`，物理SQL格式`storage_v1`。

## 默认配置身份

| 时点 | 默认config_id |
| --- | --- |
| M3交付时，实际实现版本尚未完整参与哈希 | `dd6b3d5519ae0e00878fa224b49287daecb968a95699b8fbc17d6572cc59566e` |
| 当前，实际科学版本纳入哈希 | `e56c9a82f0ad3a5e4c2df0b3abfe8639dff115bb5efdcc23652f1a383b1747cc` |

种子1101的两组固定RNG样例仍为`0x1.72de6ced9870cp-3`与`0x1.07b4523773879p-1`。旧记录保留原ID，不覆盖改写。

## 安装包验证

使用setuptools 84.2.1、wheel 0.48.0实际构建wheel，安装到`build/wheel-smoke`隔离目录并确认从该目录导入。验证包内存在`zzz_research/storage/schema.sql`，安装后的SQLiteWriter可创建数据库，外键检查为空。

`obs_battle/obs_build/obs_coverage/obs_team_member`四张表均已建立且行数为0。此次只确认结构和打包，没有执行M1公开数据采集或导入。

## 当前边界

这里只验收现有小人群运行与上述故障场景，没有完成默认3000人规模、25候选多种子搜索、统计报告或性能目标验收。同库attempt分区、断点续跑和原子替换发布尚未实现；重跑使用独立数据库。块事务之前的完整诊断片段可在failed运行中保留，下游必须筛选completed运行。M1观测、M4搜索和M5报告仍待后续实施。
