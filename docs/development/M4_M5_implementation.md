# M1/M4/M5 实现说明（观测、统计、搜索、报告、看板与 CLI）

原始实现日期：2026-10-27（Australia/Sydney）；本机修订：2026-10-28。本文保留云端运行记录，本机验证及恢复结果见[M4/M5验证记录](M4_M5_verification.md)。

## 新增模块

| 路径 | 职责 |
| --- | --- |
| `observations/adapter_shiyu.py` | ShiyuStats 原始 CSV → `obs_*`；字段与有效通关规则逐条对应固定修订 `d930cec5` 源码 |
| `observations/quality.py` | 公开文件覆盖（真实元数据）、`obs_coverage`、排除统计与质量检查 |
| `analytics/observed.py` | sample_players、两种出场率（分开命名）、分制度成绩分位数、配队频率、投资关联（缺失单列） |
| `analytics/cohorts.py` | 固定获取队列三口径、退出占比、未知率；留存与累计人均现金（分母=初始人数）；收入结构；两个搜索目标 |
| `analytics/reference.py` | L2 预注册参考轨迹（主输出×投资×养成×支援），写 `fact_fixed_benchmark_panel` |
| `analytics/survival.py` | 首次观测失效 KM、动态可用率、逐轨迹矩阵；Cox 默认关闭并记录原因 |
| `analytics/search.py` | 25 点 × 筛选/复核种子 × 痛感扰动；独立 run 数据库 → 校验后合并；非支配、入选频率、ρ、敏感性 |
| `storage/analytics_schema.sql` | storage.md 10.4 汇总表与实验清单（不改动 `schema.sql`） |
| `pipeline.py` | 阶段编排、计时、失败写 manifest；临时文件完成后原子发布 |
| `report.py` | REPORT.md 与无依赖 SVG 图；未执行阶段明确标注 |
| `app.py` | 自包含静态四 Tab 看板；安装 streamlit 时可 `streamlit run` 并即时重算代理 CI |
| `__main__.py` | engineering.md 13.2 的 CLI，另加 `run-all` |

## 关键决定

1. **原始战斗数据**：ShiyuStats 原始 CSV 在 Hugging Face 数据集，开发环境不可访问。适配器按固定修订源码的列名与规则实现（SD：S 评级=3 星；2025-12-30 起计分制 5-x 关卡；DA：floor>3 记 2-1，1 星即合格；同 uid 隔块重复整块排除），测试用格式一致的合成夹具。仓库内只放入同修订的 MIT 元数据（采集期映射、文件清单、角色可得性）与 LICENSE。
2. **投资分组**用 `characters.json` 的 `availability == "Limited S"` 判定“限定 S”，不使用 F2P/Whale 标签；影画未知不并入 0 组。
3. **λ/a 作用方式**：`shield.defense_bonus`、`shield.damage_reduction` 乘 λ；`support.default_gain_scale` 乘 a；均以配置基线为乘数（不写死 0.8/0.25）。基准点 (1,1) 与 Ref_Mechanism 得到同一 config_id，自动复用。
4. **参考轨迹投资方案**取 `gacha.target_paths` 中除支援目标外的路径；支援策略为“从不”或“该支援出道即加入”，在评估前固定。
5. **缩减规模**：`--players N` 是显式覆盖，改变 config_id，实验标记 `reduced_scale`，报告首节声明。

## 运行完整规模

```powershell
.\.venv\Scripts\python.exe -m zzz_research run-all --config config/experiment.json --out outputs/full --workers 8
```

完整协议 650 个独立 run × 3000 人。参考耗时（本次 2 核云容器，summary 模式）约 0.09 秒/玩家/run，单核约 49 CPU·小时；已完成的 run 数据库按 (config_id, seed) 复用，可中断续跑。

导入公开原始数据（需先按固定修订手动下载）：

```powershell
.\.venv\Scripts\python.exe -m zzz_research prepare-observations --out outputs/obs --snapshot-dir <下载目录> --snapshot-id <快照名> --revision <数据集修订>
```

## 已知边界

- 未执行真实公开数据导入；L1 结果部分仅有文件覆盖。
- 未运行完整 ZSim；Cox 默认关闭。
- 缩减规模结果只验证协议与流程，不支持商业结论。

## 本次基准实验（缩减规模，云容器实测）

`run-all --players 200 --workers 2`，输出 `outputs/benchmark_n200/`。默认 config_id `e56c9a82…`；人群覆盖使各 run 的 config_id 不同，实验标记 `reduced_scale`。

| 项目 | 结果 |
| --- | --- |
| 阶段 | validate、benchmarks、observations（仅元数据）、3 参照情景、search、report、dashboard 全部 completed |
| 搜索 | 25 候选 × 5 筛选种子 + 25 × 7 扰动 × 3 复核种子 = 650 逻辑 run，650 个独立 run，全部 completed，外键检查为空 |
| 计算量 | 650 个 summary run 合计约 12,243 秒模拟时间（平均 18.8 秒/run，200 人） |
| 中断与续跑 | 后台任务 2 小时上限时已完成 637 个；续跑只执行剩余 13 个，其余按 (config_id, seed) 复用 |

模拟结果（假设情景、200 人，只用于验证协议，不支持商业结论）：

- 参照情景期末全体留存：Ref_HP 0.905，Ref_Mechanism 0.945，Ref_Support 0.95；累计人均现金 914–926 元，差异小。
- 机制强度 λ 从 0 到 2，核心层期末留存均值 0.982 → 0.955；支援缩放 a 从 0.8 到 1.6，0.966 → 0.975。
- 收入候选均值差异约3%，各候选的种子SD约8%；这两项不能直接用于CRN配对差异判定。收入排序ρ=0.44、留存排序ρ=0.95可复算；现已补充同seed差值的n/均值/SD/SE/范围，少量复核种子只作描述，不据此宣称显著、等效或商业最优。
- 痛感系数 ±25% 只使留存平均变化约 ±0.3%，收入不变。已核对一例：额外流失者都在最后一期退出，因此不影响窗口内收入。

## 存储性能修正

`schema.sql` 末尾新增外键子表侧索引（仅物理索引，不改字段与约束语义）。延迟外键在插入父行时按子表列查找，缺索引会退化为全表扫描：200 人 full 模式单 run 由 498–622 秒降到 24 秒，80 人由 92 秒降到 8.4 秒；索引只影响查找方式，不改变写入的数据，外键检查为空。
