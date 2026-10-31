# M0本机验证记录

日期：2026-10-27（Australia/Sydney）。范围：公共契约、配置解析、来源登记及事件索引RNG。

## 环境与结果

在项目根目录创建独立`.venv`并安装真正的pytest；未向系统Python安装项目测试依赖。

| 项目 | 本次结果 |
| --- | --- |
| Python | 3.12.3 |
| pytest | 9.1.1 |
| 原始测试，修改前 | 63 passed in 1.68s |
| 增加边界修复与参考向量后 | 88 passed in 1.91s |

执行命令：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

新机器准备环境：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install pytest
.\.venv\Scripts\python.exe -m pytest -q
```

## RNG确认

- 接受`canonical_json([domain_tag,[master_seed,*event_key]])`的UTF-8编码。
- 接受人口键`[player_id,attribute_type]`，保留现有随机映射。
- 精确参考向量和批量生成约束以[RNG模块](../design/modules/randomness.md)为准；新增[回归测试](../../tests/test_rng_contract.py)。
- 修复前新增25个用例中有14个失败，确认原测试未覆盖的边界缺口；修复后25个全部通过。
- 非法位宽、非整数参数和未知事件键版本现在提前报错，直接构造也不能绕过校验。

默认配置身份在修复前后相同：

```text
ff4bc332a0a87265c235de0c46662625d0746b27a834787d65721a9fb3911f8e
```

本次没有修改配置JSON；两组固定RNG样例的逐位输出相同。测试中的固定协议样例用于防止编码漂移，不作为业务参数默认值。

## 验收边界

这里确认的是M0现有测试和新增RNG回归；战斗、抽卡执行、钱包编排、玩家仿真及统计分析尚未实现或验收，不能从本次通过推导它们已经正确。
