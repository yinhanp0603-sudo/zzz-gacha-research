"""冻结 RNG 字节协议的参考向量与参数边界，不复制可调业务参数的默认值。"""

import dataclasses
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from zzz_research.config import ConfigError, resolve_config
from zzz_research.rng import (
    HashCounterParams,
    HashCounterRng,
    RngError,
    draw_key,
    population_key,
)


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def protocol_params():
    # 有意固定的协议样例；修改业务配置不应重写这组历史参考向量。
    return HashCounterParams(
        rng_version="hash_counter_v1",
        domain_tag="zzz_research_rng_v1",
        digest="SHA256",
        uint_bytes=8,
        byte_order="big",
        discard_low_bits=11,
        uniform_bits=53,
    )


@pytest.mark.parametrize(
    "key,payload,digest_hex,u_hex",
    [
        (
            population_key(17, "tier"),
            b'["zzz_research_rng_v1",[1101,17,"tier"]]',
            "2e5bcd9db30e1aa92aa625905af83c0151cf2a91a0594f19ca8bce0513863b8b",
            "0x1.72de6ced9870cp-3",
        ),
        (
            draw_key(17, 3, "SYN_P03_A", "character", 5, "s_check"),
            b'["zzz_research_rng_v1",[1101,17,3,"SYN_P03_A","character",5,"s_check"]]',
            "83da291bb9c3ca14bd3bc30655836d2f6594cfa430ff7b5435aa7cb2bfd0f55d",
            "0x1.07b4523773879p-1",
        ),
    ],
)
def test_frozen_protocol_vectors(protocol_params, key, payload, digest_hex, u_hex):
    rng = HashCounterRng(1101, protocol_params)
    assert rng.key_bytes(key) == payload
    assert hashlib.sha256(payload).hexdigest() == digest_hex
    assert rng.uniform(key).hex() == u_hex


INVALID_PARAMS = [
    {"uint_bytes": 0, "discard_low_bits": 0, "uniform_bits": 0},
    {"uint_bytes": 8, "discard_low_bits": -1, "uniform_bits": 65},
    {"uint_bytes": 8, "discard_low_bits": 10, "uniform_bits": 54},
    {"uint_bytes": 8.5},
    {"uint_bytes": True},
    {"discard_low_bits": 11.5},
    {"discard_low_bits": True},
    {"uniform_bits": 53.5},
    {"uniform_bits": True},
    {"domain_tag": 42},
    {"event_key_version": "event_keys_unknown"},
]


@pytest.mark.parametrize("updates", INVALID_PARAMS)
def test_rng_rejects_invalid_config_params(protocol_params, updates):
    raw = {**dataclasses.asdict(protocol_params), "event_key_version": "event_keys_v1", **updates}
    with pytest.raises(RngError):
        HashCounterRng.from_config(1101, raw)


def test_direct_constructor_validates_params(protocol_params):
    bad = dataclasses.replace(protocol_params, uint_bytes=0, discard_low_bits=0, uniform_bits=0)
    with pytest.raises(RngError):
        HashCounterRng(1101, bad)


@pytest.mark.parametrize("updates", INVALID_PARAMS)
def test_resolver_rejects_invalid_rng_before_run(tmp_path, updates):
    dst = tmp_path / "config"
    shutil.copytree(ROOT / "config", dst)
    path = dst / "modules" / "randomness.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw.update(updates)
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ConfigError):
        resolve_config(dst / "experiment.json")
