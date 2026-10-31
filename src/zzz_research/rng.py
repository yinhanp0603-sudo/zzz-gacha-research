"""M-RNG：事件索引公共随机数（hash_counter_v1）。

事件键 → U∈[0,1) 的纯函数映射。所有参数（域标签、摘要算法、字节数、字节序、
丢弃位数、均匀位数）都从 randomness 模块配置读取；不使用进程随机化的内置 hash。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .contracts import PoolKind, RngType, canonical_json

_KEY_ENCODER = json.JSONEncoder(separators=(",", ":"), ensure_ascii=False, allow_nan=False)
SUPPORTED_RNG_VERSIONS = frozenset({"hash_counter_v1"})
SUPPORTED_EVENT_KEY_VERSIONS = frozenset({"event_keys_v1"})


class RngError(ValueError):
    pass


def _check_key_part(part: Any) -> Any:
    if isinstance(part, bool):
        raise RngError("事件键不允许布尔值")
    if isinstance(part, (PoolKind, RngType)):
        return part.value
    if isinstance(part, int):
        return part
    if isinstance(part, str):
        if not part:
            raise RngError("事件键不允许空字符串")
        return part
    raise RngError(f"事件键只允许整数/字符串，得到 {type(part).__name__}")


@dataclass(frozen=True)
class HashCounterParams:
    rng_version: str
    domain_tag: str
    digest: str
    uint_bytes: int
    byte_order: str
    discard_low_bits: int
    uniform_bits: int
    event_key_version: str = "event_keys_v1"

    @classmethod
    def from_config(cls, cfg: Mapping[str, Any]) -> "HashCounterParams":
        p = cls(
            rng_version=cfg["rng_version"],
            domain_tag=cfg["domain_tag"],
            digest=cfg["digest"],
            uint_bytes=cfg["uint_bytes"],
            byte_order=cfg["byte_order"],
            discard_low_bits=cfg["discard_low_bits"],
            uniform_bits=cfg["uniform_bits"],
            event_key_version=cfg["event_key_version"],
        )
        p.validate()
        return p

    def validate(self) -> None:
        if not isinstance(self.rng_version, str) or self.rng_version not in SUPPORTED_RNG_VERSIONS:
            raise RngError(f"不支持的 rng_version {self.rng_version!r}")
        if (not isinstance(self.event_key_version, str)
                or self.event_key_version not in SUPPORTED_EVENT_KEY_VERSIONS):
            raise RngError(f"不支持的 event_key_version {self.event_key_version!r}")
        if (not isinstance(self.digest, str)
                or self.digest.lower().replace("-", "") not in hashlib.algorithms_available):
            raise RngError(f"不支持的摘要算法 {self.digest!r}")
        for name, value, minimum in (
            ("uint_bytes", self.uint_bytes, 1),
            ("discard_low_bits", self.discard_low_bits, 0),
            ("uniform_bits", self.uniform_bits, 1),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                raise RngError(f"{name} 必须是大于等于 {minimum} 的整数")
        # IEEE-754 binary64 只能无舍入地表示这一区间内的 53 位二进制分数。
        if self.uniform_bits > 53:
            raise RngError("uniform_bits 不能超过 53，以保证 U 严格小于 1")
        if self.byte_order not in ("big", "little"):
            raise RngError("byte_order 必须为 big/little")
        if self.uint_bytes * 8 - self.discard_low_bits != self.uniform_bits:
            raise RngError("uint_bytes*8 - discard_low_bits 必须等于 uniform_bits")
        if self.uint_bytes > hashlib.new(self.digest.lower().replace("-", "")).digest_size:
            raise RngError("uint_bytes 超过摘要长度")
        if not isinstance(self.domain_tag, str) or not self.domain_tag:
            raise RngError("domain_tag 必须是非空字符串")


class HashCounterRng:
    """hash_counter_v1：U = (x >> discard) / 2**uniform_bits，x 为摘要前 uint_bytes 字节的无符号整数。

    事件键编码：canonical_json([domain_tag, [master_seed, *event_key]]) 的 UTF-8 字节。
    """

    def __init__(self, master_seed: int, params: HashCounterParams):
        if isinstance(master_seed, bool) or not isinstance(master_seed, int) or master_seed < 0:
            raise RngError("master_seed 必须是非负整数")
        params.validate()
        self.master_seed = master_seed
        self.params = params
        self._algo = params.digest.lower().replace("-", "")
        _ctor = getattr(hashlib, self._algo, None)
        self._hash = _ctor if callable(_ctor) else (lambda b, _a=self._algo: hashlib.new(_a, b))
        self._scale = float(1 << params.uniform_bits)
        self._prefix_cache: dict[tuple, Any] = {}
        self._stream_suffix = tuple(("," + _KEY_ENCODER.encode(rt.value) + "]]").encode("utf-8")
                                    for rt in (RngType.S, RngType.UP))

    @classmethod
    def from_config(cls, master_seed: int, randomness_cfg: Mapping[str, Any]) -> "HashCounterRng":
        return cls(master_seed, HashCounterParams.from_config(randomness_cfg))

    # -- 核心映射 ---------------------------------------------------------

    def key_bytes(self, event_key: Sequence[Any]) -> bytes:
        if isinstance(event_key, (str, bytes)) or not event_key:
            raise RngError("事件键必须是非空序列")
        parts = [_check_key_part(p) for p in event_key]
        # 键部件已校验为 int/str：此时紧凑 JSON 数组编码与 canonical_json 逐字节相同
        # （无对象键需排序、无浮点），跳过通用的递归校验以降低逐抽开销。
        return _KEY_ENCODER.encode([self.params.domain_tag, [self.master_seed, *parts]]).encode("utf-8")

    def uniform(self, event_key: Sequence[Any]) -> float:
        d = self._hash(self.key_bytes(event_key)).digest()
        x = int.from_bytes(d[: self.params.uint_bytes], self.params.byte_order, signed=False)
        return (x >> self.params.discard_low_bits) / self._scale

    def hit(self, event_key: Sequence[Any], p: float) -> bool:
        """按 U < p 判定，因此 p=1 必定命中、p=0 必不命中。"""
        if not 0.0 <= p <= 1.0:
            raise RngError(f"概率必须在 [0,1]，得到 {p!r}")
        return self.uniform(event_key) < p

    def block(self, prefix: Sequence[Any], start: int, count: int) -> list[float]:
        """通用末尾计数槽；等价于 uniform(prefix + [slot])。

        抽取键的槽号位于 rng_type 之前，不能直接用本方法替代 draw_u；
        抽取张量应逐槽调用 draw_u 或完整的 draw_key。
        """
        if start < 0 or count < 0:
            raise RngError("start/count 必须非负")
        return [self.uniform([*prefix, slot]) for slot in range(start, start + count)]

    # -- 规范事件键（event_keys_v1） --------------------------------------

    def population_u(self, player_id: int, attribute_type: str) -> float:
        return self.uniform(population_key(player_id, attribute_type))

    def decision_u(self, player_id: int, t_idx: int, banner_id: str, decision_slot: int) -> float:
        return self.uniform(decision_key(player_id, t_idx, banner_id, decision_slot))

    def draw_u(
        self, player_id: int, t_idx: int, banner_id: str, pool_kind: PoolKind | str,
        draw_slot: int, rng_type: RngType | str,
    ) -> float:
        return self.uniform(draw_key(player_id, t_idx, banner_id, pool_kind, draw_slot, rng_type))

    def draw_pair(self, player_id: int, t_idx: int, banner_id: str, pool_kind: PoolKind | str,
                  draw_slot: int) -> tuple[float, float]:
        """(S 判定 U, UP 判定 U)，与两次 draw_u 逐位相同。

        同一 (玩家, 期, banner, 池) 的键前缀只校验、编码与哈希一次（hashlib copy），
        只追加槽号与流类型；不改变事件键，只改变计算方式。
        """
        if isinstance(draw_slot, bool) or not isinstance(draw_slot, int):
            raise RngError("draw_slot 必须是整数")
        pk = PoolKind(pool_kind).value
        ck = (player_id, t_idx, banner_id, pk)
        base = self._prefix_cache.get(ck)
        if base is None:
            parts = [_check_key_part(x) for x in (player_id, t_idx, banner_id, pk)]
            head = _KEY_ENCODER.encode([self.params.domain_tag, [self.master_seed, *parts]])[:-2] + ","
            base = self._hash(head.encode("utf-8"))
            if len(self._prefix_cache) > 100_000:
                self._prefix_cache.clear()
            self._prefix_cache[ck] = base
        slot = str(draw_slot).encode("ascii")
        out = []
        for suffix in self._stream_suffix:
            h = base.copy()
            h.update(slot + suffix)
            x = int.from_bytes(h.digest()[: self.params.uint_bytes], self.params.byte_order, signed=False)
            out.append((x >> self.params.discard_low_bits) / self._scale)
        return out[0], out[1]

    def churn_u(self, player_id: int, t_idx: int) -> float:
        return self.uniform(churn_key(player_id, t_idx))


# master_seed 由 HashCounterRng 统一前置，以下键不含 config_id/strategy_id。


def population_key(player_id: int, attribute_type: str) -> list[Any]:
    return [player_id, attribute_type]


def decision_key(player_id: int, t_idx: int, banner_id: str, decision_slot: int) -> list[Any]:
    return [player_id, t_idx, banner_id, decision_slot, "decision"]


def draw_key(
    player_id: int, t_idx: int, banner_id: str, pool_kind: PoolKind | str,
    draw_slot: int, rng_type: RngType | str,
) -> list[Any]:
    pk = PoolKind(pool_kind).value
    rt = RngType(rng_type).value
    return [player_id, t_idx, banner_id, pk, draw_slot, rt]


def churn_key(player_id: int, t_idx: int) -> list[Any]:
    return [player_id, t_idx, "churn"]
