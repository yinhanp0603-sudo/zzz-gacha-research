# M4/M5本机审核与运行库恢复记录

日期：2026-10-28（Australia/Sydney）。范围：软件测试、200人实验导出复算、压缩档案恢复、逐库检查、汇总重建及报告修订。未运行3000人全规模实验。

## 真实pytest与CLI

| 检查 | 本机结果 |
| --- | --- |
| 用户交付版本 | 205 passed in 45.21s |
| 修复后完整测试 | 262 passed in 51.52s，新增57个回归 |
| Windows多进程CLI | 2名玩家、2个运行、workers=2的小样本搜索通过 |
| CLI安装 | 项目以editable方式安装到专用`.venv`，模块入口可用 |

复跑：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

新增回归分别覆盖[观测](../../tests/test_m4_observation_regressions.py)、[统计](../../tests/test_m4_statistics_regressions.py)、[流水线](../../tests/test_m4_pipeline_regressions.py)与[报告](../../tests/test_m5_report_regressions.py)。

## 本轮修复

| 模块 | 修复 |
| --- | --- |
| 观测身份 | 适配器升shiyu_adapter_v2，按采集期隔离snapshot身份组；同期SD/DA/角色资料共享，跨期同UID不合并 |
| 观测质量与来源 | 空UID排除；未知影画保留；原始CSV许可不自动继承代码MIT；metadata-only重跑清理旧派生数据并写当前manifest |
| 三口径 | 未知按活跃、冻结、生命周期各自依赖传播，避免不相关缺失抹掉已知比率 |
| KM | 首次失败后出现缺失不撤销事件；首次未知排除，成功前缀后未知采用保守右删失并记录原因 |
| 搜索协议 | 最终CLI种子列表检查非负、去重、筛选/复核互斥；核心留存不可评估时拒绝计算伪前沿 |
| 缓存与发布 | 复用前检查身份、completed attempt、完整玩家期、FK及日志能力；汇总暂存成功再发布，失败保留旧完整结果 |
| 实验隔离 | CSV和报告按当前experiment_id筛选，不混入同目录历史实验 |
| 收入解释 | 新增CRN同seed配对差的n、均值、SD、SE、范围；删除用各自种子SD直接判定“无法区分”的推断 |

本轮未改数值配置JSON、玩家模拟公式或RNG键。默认科学配置仍为：

```text
e56c9a82f0ad3a5e4c2df0b3abfe8639dff115bb5efdcc23652f1a383b1747cc
```

## 数据库恢复与重建

| 检查 | 结果 |
| --- | --- |
| 传输包 | 28个runs_part压缩包＋1个reference_dbs压缩包 |
| 恢复文件 | 650个搜索库＋3个参照库，共653个 |
| ZIP内容与路径 | 与清单精确对应，拒绝目录逃逸/符号链接；流式读取验证CRC |
| 解压后SHA-256 | 653个逐文件回读匹配，差异0 |
| 科学与运行身份 | 配置哈希、run_id、config/seed及completed attempt检查通过 |
| 数据完整性 | 653库quick_check和FK检查通过；200×10玩家期覆盖通过 |
| 重建 | 650个搜索运行全部复用，执行模拟0次 |
| 合并库 | search.sqlite已重建；run_manifest 650行，生命周期与玩家期宽表各1,300,000行 |
| 搜索汇总 | 650条运行结果、200条种子汇总、7条秩稳定、150条参数敏感性 |
| 云端对照 | 重建逐run收入和留存与原云端导出完全一致 |

恢复约24.43秒；验证及重建约181.02秒，包含多轮只读校验，不是新模拟耗时。650个搜索库为summary日志，3个参照库为full日志；650指不同配置/种子运行，同seed候选共享CRN，并非650个相互独立的统计重复。

重建后再次回读全部653个原始库的SHA-256，仍与传输清单一致，确认汇总重建没有改写运行库。

三个完整参照库另核对四来源/实际抽数、现金汇总、逐期钱包守恒、期末资产及跨期/逐抽保底，共检查221,578次抽取的保底衔接，选定检查项均无差异。不能将此扩写为650个summary库都有逐抽明细可核对。

证据：

- 解压和SHA清单（`outputs/benchmark_n200/verification/archive_restore.json`，本机历史产物）
- 逐库与重建检查（`outputs/benchmark_n200/verification/database_audit.json`，本机历史产物）
- 三个参照库账目检查及SQL（`outputs/benchmark_n200/verification/reference_ledger_audit.json`，本机历史产物）
- 重建的search.sqlite（`outputs/benchmark_n200/search/search.sqlite`，本机历史产物）

原云端汇总、清单、报告和看板保存在恢复前备份（`outputs/benchmark_n200/verification/cloud_delivery_before_rebuild/search/experiment_manifest.json`，本机历史产物）。压缩包和原始传输清单继续保留；恢复旧运行不等于使用当前代码重新模拟，原环境和算法版本保留在各run_manifest及数据库审核记录中。

## 导出表独立复算与解释

[独立核对工具](../../tools/verify_benchmark_exports.py)不调用生产analytics helpers，核对650条运行覆盖、非支配集合、均值/样本SD/范围、入选频率、同seed基准差、rho和参数扰动，并核对新增配对差摘要。共11,593项数值/身份/覆盖断言通过，无差异；这是检查数量，不是总体准确率或置信度。

收入排序rho=0.4409388553、留存排序rho=0.9507442598，与云端导出一致。筛选种子边际描述亦一致：lambda从0到2时核心留存均值0.9815356→0.9552762；a从0.8到1.6时0.9655743→0.9754134。此处为固定网格、指定假设与有限种子的描述，不是现实规律或商业最优判断。

“候选均值差约3%小于各自种子SD约8%，所以无法区分”不是CRN配对检验依据，已修订。新增配对差摘要（`outputs/benchmark_n200/search/paired_differences.csv`，本机历史产物）仅描述同seed差值，3个复核种子不足以据此宣称等效、显著或最优。

对450个敏感性运行与各自P0基准进一步比较，共900,000个配对玩家期，现金逐项差值均为0。流失差异并非全部发生在末期：Budget_0最早第4期已有变化，但该层不产生现金；付费核心Budget_40的状态差异出现在第10期期末。记录的是重复情景中的配对案例，不是互不重叠的真实玩家人数。

- 独立CSV核对证据（`outputs/benchmark_n200/verification/local_export_audit.json`，本机历史产物）
- 流失与收入差异检查（`outputs/benchmark_n200/verification/churn_sensitivity_audit.json`，本机历史产物）

## 索引、来源与呈现

将当前schema与M3留存wheel中的schema分别建库比较，表定义及约束完全一致，只增加11个非唯一索引。基线wheel SHA-256为`9e26e2b4866b7c1096bb7f1b02472303d9589677e4ce563a8c21e5765c95cead`。未在本机重复测量云端“8–10分钟降至24秒”的性能案例。

当前版本另构建wheel并安装到独立目录，确认核心schema.sql、analytics_schema.sql、新观测/统计模块均包含，安装后的包可创建两类表结构。见安装包检查（`outputs/local_verification/m4_m5_20261028/package_smoke.json`，本机历史产物）。

已读固定修订的[评分配置源码](https://github.com/LvlUrArti/ShiyuStats/blob/d930cec50bfde460d1a6ebd9acef8b240a676000/scripts/comp_rates_config.py)、[CSV解析源码](https://github.com/LvlUrArti/ShiyuStats/blob/d930cec50bfde460d1a6ebd9acef8b240a676000/scripts/csv_to_pickle.py)及[通关规则](https://github.com/LvlUrArti/ShiyuStats/blob/d930cec50bfde460d1a6ebd9acef8b240a676000/scripts/composition.py)，核对模式、列名和阈值依据；真实战斗CSV仍未导入。

报告改为report_v2，补入配对差及当前证据边界；静态看板同步数据说明。既有10个SVG文件按SHA-256保持原样，没有为修复文字而重绘。浏览器安全策略拒绝直接访问本地HTML，因此未宣称完成浏览器渲染或可选Streamlit交互验收。见呈现刷新记录（`outputs/benchmark_n200/verification/presentation_refresh.json`，本机历史产物）。

## 剩余边界

未运行3000人规模、未导入真实战斗样本、未运行完整ZSim或探索性Cox；不把200人假设情景作为市场预测。当前完成的是测试、200人运行档案恢复及上述可复核产物审核。
