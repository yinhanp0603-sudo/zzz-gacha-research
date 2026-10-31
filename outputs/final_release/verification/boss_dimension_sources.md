# 危局首领维表来源与分类口径

维表包含 **47 个历史赛季、141 个试炼房间及 6 个绝境房间**，主键为 `combat_season + stage_slot`。`S1/S2/S3` 对应三路试炼，`ADV` 对应绝境。

历史阵容覆盖 1.4.1–3.2.3；现有公开原始记录覆盖其中 **35 个赛季、36 个采集快照**。采集期与战斗赛季不同，关联使用观测导入时登记的战斗赛季，不能直接拿文件名当赛季。

## 名称与阵容证据

- 英文名及剧情隐藏名别名取自已下载的 ShiyuData `boss` 列。`Primordial Nightmare` 与带始主副标题的名称、`Discordant Solo - ???` 与 `Discordant Solo - Vesper`、`??? the Thrall` 与 `Ye Shiyuan the Thrall` 分别归入同一身份。
- 1.4–2.8 的日期可对照 [Fandom 历史索引](https://zenless-zone-zero.fandom.com/wiki/Deadly_Assault/History)。
- 3.1、3.2 的三路阵容另与[官方 3.1 更新公告](https://zzz.mihoyo.com/news/165415)、[官方 3.2 更新公告](https://zzz.mihoyo.com/news/166026)核对；3.0–3.2 的开始日期沿用历史目录中的日期。
- **132 个房间**有原始快照、社区历史页面或版本公告的独立对应证据。1.6.1、2.0.1、2.2.3、2.3.3、2.6.3 的 **15 个房间**保留所给历史目录，`schedule_source_status=supplied_schedule`；这些赛季没有已导入的观测记录，不填造实证数据。
- 2.7.2 原始记录中存在将 `??? the Thrall` 填入非第三路的情况。它只作为叶释渊的隐藏名别名，不作为薇斯珀或未知复合侵蚀体的有效别名。

## 机制证据

21 种机制分类都保留了对应的社区历史或攻略页面。分类概括首领交互与对策，不将某一期的属性弱点、区域增益和数值规则自动套用到所有历史期。

| 机制 | 核实口径 | 来源 |
| --- | --- | --- |
| 早期异常、部位、招架与分身交互 | 按危局各期特殊机制归类 | [2025-02-07](https://zenless-zone-zero.fandom.com/wiki/Deadly_Assault/2025-02-07)、[2025-02-21](https://zenless-zone-zero.fandom.com/wiki/Deadly_Assault/2025-02-21)、[2025-05-02](https://zenless-zone-zero.fandom.com/wiki/Deadly_Assault/2025-05-02) |
| 秽息妖鬼 | 异常叠层与秽盾削减效率 | [2025-07-25](https://zenless-zone-zero.fandom.com/wiki/Deadly_Assault/2025-07-25) |
| 彷徨猎手、叶释渊 | 高基础防御与贯穿增益、双实体交替与失衡窗口 | [2025-12-12](https://zenless-zone-zero.fandom.com/wiki/Deadly_Assault/2025-12-12) |
| 始主 | 秽盾切换、控制技与反制 | [17173：2.13–2.27](https://news.17173.com/content/02132026/182828854.shtml) |
| 薇斯珀 | 暴伤降低 40%、以太帷幕带来的异常增益、控制技反制 | [17173：机制解析](https://news.17173.com/content/02072026/101649733.shtml) |
| 猎血清道夫 | 侵蚀壁垒期异常增益与控制技反制 | [17173：2.7 配队](https://news.17173.com/content/05062026/182353214.shtml) |
| 亵渎者 | 空中击落与连续招架 | [17173：动作机制](https://news.17173.com/content/10192025/115549011.shtml) |
| 焚昼余火 | 五层赴火，每层扣减 25 个百分点的暴伤加成，合计 125 个百分点；不是总伤害降低 125% | [EnjoyGM：3.2 第三期](https://www.enjoygm.com/zh-TW/blog/zenless-zone-zero/zzz-v3-2-deadly-assault-phase-3-guide) |
| 基塔布鲁、库萨里库、异构焚昼余火 | 异常削盾、异常伤害降低 40% 与锋御识破、控制技反制后的锐化增益 | [EnjoyGM：3.2 全指南](https://www.enjoygm.com/zh-TW/blog/zenless-zone-zero/zzz-v3-2-deadly-assault-guide) |

`favored_archetype` 是根据机制提出的对策假设，不是观测占比、官方唯一配队或因果结论。真实配队响应另由 `boss_mechanism_archetype_stats.csv` 统计。

五大 `mech_regime` 是历史时代分组。旧 Boss 会跨世代回归，不能把某一时代的全部记录都视作同一种机制；具体机制比较应使用 `mechanism_code` 与 Boss 身份。
