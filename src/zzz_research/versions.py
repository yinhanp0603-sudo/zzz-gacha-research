"""不依赖计算模块的科学实现版本登记；配置与实现共用这一维护源。"""

PLANNER_VERSION = "planner_dp_v2"
RUNTIME_VERSION = "runtime_six_step_spending_v3"
SCENARIO_RESOLVER_VERSION = "scenario_resolve_v2_observed_anchor_assumed"


def scientific_algorithm_versions() -> dict[str, str]:
    """参与config_id的实际实现版本；存储/显示产物版本不在这里混入。"""
    return {
        "planner": PLANNER_VERSION,
        "runtime": RUNTIME_VERSION,
        "scenario_resolver": SCENARIO_RESOLVER_VERSION,
    }
