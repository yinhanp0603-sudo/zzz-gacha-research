# M-RNG｜事件索引公共随机数

> 模块文档版本：1.0.1｜整套设计：1.1.0｜V2 已实现并完成千人跨种子复核；现行代码、发布清单（publication_manifest.json）与结果表已对齐。当前研究差异见[V2设计](../research_v2.md)。

[总入口](../../../prompt_spec.md) · [模块地图](../architecture.md) · [变更指南](../change_guide.md)

## 模块契约

| 项目 | 约定 |
| --- | --- |
| 负责 | 事件到随机值的映射；不依赖策略分支 |
| 输入 | master_seed和规范化事件键 |
| 输出 | 稳定对应的U值或张量块 |
| 单向依赖 | [M-CONTRACT](contracts.md) |
| 参数维护位置 | [randomness.json](../../../config/modules/randomness.json) |

数值参数以对应JSON为唯一维护源，[默认参数视图](../defaults.md)是自动生成的只读投影。下面保留的v1.0数值校验例用于解释公式，不覆盖配置；不得把示例复制成另一份全局默认值。

## 详细设计

统一主键使用稳定字符串/整数编码，不使用进程随机化的内置 hash：

- 人群静态属性：`(master_seed,player_id,attribute_type)`。
- 下池决策：`(master_seed,player_id,t_idx,banner_id,decision_slot,"decision")`。
- 抽取：`(master_seed,player_id,t_idx,banner_id,pool_kind,draw_slot,rng_type)`，其中rng_type区分S判定与UP判定。
- 流失：`(master_seed,player_id,t_idx,"churn")`。

config_id/strategy_id 不进入公共随机键。角色与音擎独立流；即便保证命中无需使用UP随机值，也不把后续槽位前移。同一期多banner有独立稳定ID。draw_slot为本事件内该池第几次抽取，不是pity；切换目标影画阶段不重置同池draw_slot。

默认 `hash_counter_v1`：以域标签 `zzz_research_rng_v1` 和规范化JSON数组形式的完整事件键组成UTF-8字节串，计算SHA256，取摘要前8字节的大端无符号整数x，令U=`(x >> 11) / (1 << 53)`。U在[0,1)，按 `U < p` 判定，因此p=1必定命中。预生成/缓存张量只改变计算时机，不改变事件映射；优化更换PRNG时建立新rng_version和config_id。资源/结果分支不得决定后续随机值的身份。

### M0冻结的字节协议

精确的输入是 `canonical_json([domain_tag, [master_seed, *event_key]]).encode("utf-8")`：两层数组，无空白，字符串保留UTF-8字符。人口事件键直接为 `[player_id, attribute_type]`，不另加前缀；`player_id`为整数，`attribute_type`为非空字符串。现有四类事件键的长度和字段类型可以区分事件家族。以后新增家族不得复用已有形状和含义；改变编码或键结构必须升级对应版本，不能静默改变历史映射。

以下是固定的协议测试样例，使用种子1101及上文的SHA256/8字节/右移11位/53位规则；它们不是另一份业务默认配置。U用Python浮点十六进制表示，以便逐位核对。

| 事件 | 字面量UTF-8输入 | SHA256摘要 | U.hex() |
| --- | --- | --- | --- |
| 人口属性 | `["zzz_research_rng_v1",[1101,17,"tier"]]` | `2e5bcd9db30e1aa92aa625905af83c0151cf2a91a0594f19ca8bce0513863b8b` | `0x1.72de6ced9870cp-3` |
| 角色池S判定 | `["zzz_research_rng_v1",[1101,17,3,"SYN_P03_A","character",5,"s_check"]]` | `83da291bb9c3ca14bd3bc30655836d2f6594cfa430ff7b5435aa7cb2bfd0f55d` | `0x1.07b4523773879p-1` |

实现必须拒绝未知的`rng_version`和`event_key_version`；读取配置和直接构造RNG都执行参数校验。字节数必须为正整数，丢弃位数为非负整数，均匀位数为1至53的整数，布尔值和浮点数不能代替整数。53位上限来自binary64输出精度，用来保证U严格小于1；它不属于可调行为参数。位数恒等式与摘要长度也必须满足。

`block(prefix,start,count)`是通用的末尾追加槽号工具，只等价于`uniform([*prefix,slot])`。抽取键的槽号位于`rng_type`之前，因此M1预生成抽取张量必须逐槽构造完整`draw_key`或调用`draw_u`，不能直接把该工具输出当作规范抽取流。固定参考测试见[测试文件](../../../tests/test_rng_contract.py)，本机验证见[M0验证记录](../../development/M0_verification.md)。

容量上界由已有资源、剩余预算和剩余目标最坏保底抽数共同确定。极端囤抽不能造成静默截断；需要扩容时按同一索引补块，不改变原槽值。改变任务执行顺序、线程数或块大小，同一配置和种子的结果必须一致。

同样seed只保证对应事件受到同一基础随机冲击，不保证不同状态的玩家产生同样结果；不作“不同动作全都严格等价”的额外主张。

## 更新与验收

修改本模块时先查看[影响矩阵](../change_guide.md)，更新对应配置/契约版本，并执行 `python tools/design_docs.py sync` 和 `python tools/design_docs.py check`。这两条仅维护设计资料；科学验证仍按[验证模块](validation.md)实施。模块输入输出字段不得在此复制出与[公共契约](contracts.md)或[数据库契约](storage.md)相冲突的定义。


