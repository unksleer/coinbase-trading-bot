import hashlib
import json
import os
import tempfile


MODE_ADVISORY = "ADVISORY"
MODE_APPLY_APPROVED = "APPLY_APPROVED"
VALID_MODES = {MODE_ADVISORY, MODE_APPLY_APPROVED}


def normalize_mode(value):
    mode = str(value or MODE_ADVISORY).strip().upper()
    return mode if mode in VALID_MODES else MODE_ADVISORY


def recommendation_id(pair, params):
    normalized = {
        "pair": pair.upper(),
        "short": int(params["short"]),
        "long": int(params["long"]),
        "stop_loss": float(params["stop_loss"]),
        "take_profit": float(params["take_profit"]),
    }
    payload = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def assess_candidate(candidate, baseline, benchmark_profit,
                     min_validation_trades=4, min_stability_pass_rate=0.60,
                     min_profit_factor=1.05):
    """Return an evidence-based recommendation decision and rejection reasons."""
    reasons = []
    val_trades = int(candidate.get("val_trades", 0))
    val_profit = float(candidate.get("val_net_profit", 0.0))
    val_pf = float(candidate.get("val_profit_factor", 0.0))
    profitable_folds = int(candidate.get("profitable_folds", 0))
    total_folds = int(candidate.get("validation_folds", 0))
    stability = float(candidate.get("stability_pass_rate", 0.0))
    baseline_profit = float(baseline.get("val_net_profit", 0.0))

    if val_trades < min_validation_trades:
        reasons.append(f"validation trades {val_trades} < {min_validation_trades}")
    if val_profit <= 0:
        reasons.append("validation net profit is not positive")
    if val_pf < min_profit_factor:
        reasons.append(f"validation profit factor {val_pf:.2f} < {min_profit_factor:.2f}")
    if total_folds < 2 or profitable_folds != total_folds:
        reasons.append(f"profitable validation folds {profitable_folds}/{total_folds}; all must pass")
    if val_profit <= baseline_profit:
        reasons.append("candidate does not outperform current parameters out of sample")
    if val_profit <= float(benchmark_profit):
        reasons.append("candidate does not outperform buy-and-hold after costs")
    if stability < min_stability_pass_rate:
        reasons.append(
            f"neighbor stability {stability:.0%} < {min_stability_pass_rate:.0%}"
        )

    return {"eligible": not reasons, "reasons": reasons}


def load_approved_ids(path):
    try:
        with open(path) as handle:
            data = json.load(handle)
        return {str(item) for item in data.get("approved", [])}
    except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError):
        return set()


def should_apply_recommendation(mode, rec_id, approved_ids):
    return normalize_mode(mode) == MODE_APPLY_APPROVED and rec_id in set(approved_ids)


def atomic_write_json(path, payload):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".learner-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True, default=_json_default)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def _json_default(value):
    """Convert NumPy/Pandas scalar-like values without importing those packages."""
    if hasattr(value, "item"):
        return value.item()
    if hasattr(value, "tolist"):
        return value.tolist()
    raise TypeError(f"Object of type {value.__class__.__name__} is not JSON serializable")
