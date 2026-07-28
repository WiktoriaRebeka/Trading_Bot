"""Local round-trip test for MsiEngine export_state / import_state."""
from orderflow_engine.msi_engine import MsiCandle, MsiEngine, MsiPhase

# Pola stanu strukturalnego w export_state (bez metadanych pomocniczych).
STRUCTURAL_EXPORT_KEYS = (
    "symbol",
    "candle_count",
    "last_processed_ts",
    "state",
    "schema_version",
)


def make_candles():
    return [
        MsiCandle(ts=1_700_000_000_000, open=100, high=105, low=95, close=102, volume=10),
        MsiCandle(ts=1_700_000_060_000, open=102, high=110, low=96, close=108, volume=12),
        MsiCandle(ts=1_700_000_120_000, open=108, high=115, low=97, close=113, volume=15),
        MsiCandle(ts=1_700_000_180_000, open=113, high=118, low=100, close=116, volume=11),
        MsiCandle(ts=1_700_000_240_000, open=116, high=120, low=101, close=119, volume=9),
        MsiCandle(ts=1_700_000_300_000, open=119, high=122, low=102, close=121, volume=8),
        MsiCandle(ts=1_700_000_360_000, open=121, high=125, low=103, close=124, volume=7),
        MsiCandle(ts=1_700_000_420_000, open=124, high=128, low=104, close=127, volume=6),
        MsiCandle(ts=1_700_000_480_000, open=127, high=130, low=105, close=129, volume=5),
        MsiCandle(ts=1_700_000_540_000, open=129, high=132, low=106, close=131, volume=4),
        MsiCandle(ts=1_700_000_600_000, open=131, high=135, low=107, close=134, volume=3),
        MsiCandle(ts=1_700_000_660_000, open=134, high=138, low=108, close=137, volume=2),
        MsiCandle(ts=1_700_000_720_000, open=137, high=140, low=109, close=139, volume=1),
    ]


def structural_export(d: dict) -> dict:
    return {k: d[k] for k in STRUCTURAL_EXPORT_KEYS}


def deep_diff(a, b, path=""):
    diffs = []
    if type(a) is not type(b):
        return [(path, a, b)]
    if isinstance(a, dict):
        keys = set(a) | set(b)
        for k in sorted(keys):
            p = f"{path}.{k}" if path else k
            if k not in a:
                diffs.append((p, "<missing>", b[k]))
            elif k not in b:
                diffs.append((p, a[k], "<missing>"))
            else:
                diffs.extend(deep_diff(a[k], b[k], p))
    elif isinstance(a, list):
        if len(a) != len(b):
            diffs.append((path + ".len", len(a), len(b)))
        for i, (x, y) in enumerate(zip(a, b)):
            diffs.extend(deep_diff(x, y, f"{path}[{i}]"))
    elif a != b:
        diffs.append((path, a, b))
    return diffs


def main():
    engine1 = MsiEngine("BTCUSDT")
    for c in make_candles():
        engine1.on_candle_close(c)

    if engine1.state.phase != MsiPhase.HL_LH_CYCLE:
        raise AssertionError(f"Expected HL_LH_CYCLE, got {engine1.state.phase}")
    if engine1.state.current_ob is None:
        raise AssertionError("current_ob is None after feed")

    s1 = engine1.export_state()
    engine2 = MsiEngine("BTCUSDT")
    engine2.import_state(s1)
    s2 = engine2.export_state()

    s1_struct = structural_export(s1)
    s2_struct = structural_export(s2)
    structural_equal = s1_struct == s2_struct
    structural_diffs = deep_diff(s1_struct, s2_struct)

    ob_high_ok = (
        engine2.state.current_ob is not None
        and engine1.state.current_ob is not None
        and engine2.state.current_ob.ob_high == engine1.state.current_ob.ob_high
    )

    print("=== ROUND-TRIP TEST ===")
    print(f"phase after feed: {engine1.state.phase.value}")
    print(f"current_ob chain: {engine1.state.current_ob.chain_id}")
    print(f"engine1 current_ob.ob_high: {engine1.state.current_ob.ob_high}")
    print(f"engine2 current_ob.ob_high: {engine2.state.current_ob.ob_high}")
    print(f"structural export equal (excl. all_obs_count): {structural_equal}")
    print(f"ob_high property match: {ob_high_ok}")
    print(f"candle_count s1/s2: {s1['candle_count']} / {s2['candle_count']}")
    print(f"last_processed_ts s1/s2: {s1['last_processed_ts']} / {s2['last_processed_ts']}")
    print(
        "all_obs_count (informational only, not part of round-trip assert): "
        f"s1={s1['all_obs_count']}, s2={s2['all_obs_count']}, "
        f"engine2._all_obs len={len(engine2._all_obs)}"
    )

    if structural_diffs:
        print("STRUCTURAL DIFFS:")
        for path, a, b in structural_diffs:
            print(f"  {path}: {a!r} != {b!r}")
    else:
        print("STRUCTURAL DIFFS: none")

    if structural_equal and ob_high_ok:
        print("RESULT: PASS")
    else:
        print("RESULT: FAIL")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
