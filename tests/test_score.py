import unittest

from releasenote.config import WorkspaceConfig
from releasenote.models import ChangeEntry, Flag, QAResult
from releasenote.score import (
    BAND_READY,
    BAND_REVIEW,
    BAND_RISKY,
    BAND_READY_MIN,
    BAND_REVIEW_MIN,
    GATE_FAIL,
    GATE_PASS,
    band_for,
    compute_score,
    evaluate_gate,
)


def _entry(entry_id="A"):
    return ChangeEntry(
        id=entry_id,
        type="fix",
        title="Fix login",
        description="Login no longer fails",
        benefit="Reliable access",
        source=entry_id,
    )


def _qa(*flags):
    return QAResult(flags=list(flags))


def _flag(code, severity, entry_id="A"):
    return Flag(code=code, severity=severity, message=code, entry_id=entry_id)


class BandTests(unittest.TestCase):
    def test_thresholds(self):
        self.assertEqual(band_for(BAND_READY_MIN), BAND_READY)
        self.assertEqual(band_for(100), BAND_READY)
        self.assertEqual(band_for(BAND_READY_MIN - 1), BAND_REVIEW)
        self.assertEqual(band_for(BAND_REVIEW_MIN), BAND_REVIEW)
        self.assertEqual(band_for(BAND_REVIEW_MIN - 1), BAND_RISKY)
        self.assertEqual(band_for(0), BAND_RISKY)


class ComputeScoreTests(unittest.TestCase):
    def test_zero_flags_scores_100_ready(self):
        result = compute_score(_qa(), [_entry()])
        self.assertEqual(result["score"], 100)
        self.assertEqual(result["band"], BAND_READY)
        self.assertEqual(result["penalties"], [])
        self.assertEqual(result["gate"], {"status": GATE_PASS, "reasons": []})

    def test_penalty_is_scaled_by_entry_count(self):
        one = compute_score(
            _qa(_flag("missing_source", "high")), [_entry()]
        )
        many = compute_score(
            _qa(_flag("missing_source", "high")),
            [_entry(str(i)) for i in range(50)],
        )
        self.assertLess(one["score"], many["score"])
        self.assertEqual(many["score"], 99)

    def test_high_flags_score_below_medium_for_same_entries(self):
        entries = [_entry(str(i)) for i in range(4)]
        high = _qa(*[_flag("missing_source", "high") for _ in range(2)])
        medium = _qa(*[_flag("vague_language", "medium") for _ in range(2)])
        self.assertLess(
            compute_score(high, entries)["score"],
            compute_score(medium, entries)["score"],
        )

    def test_severity_ordering(self):
        entries = [_entry()]
        scores = {
            severity: compute_score(
                _qa(_flag("code", severity)), entries
            )["score"]
            for severity in ("high", "medium", "low")
        }
        self.assertLess(scores["high"], scores["medium"])
        self.assertLess(scores["medium"], scores["low"])

    def test_clamped_to_zero(self):
        flags = [_flag("missing_source", "high") for _ in range(10)]
        result = compute_score(_qa(*flags), [_entry()])
        self.assertEqual(result["score"], 0)
        self.assertEqual(result["band"], BAND_RISKY)

    def test_penalties_sorted_by_points_then_code(self):
        flags = [
            _flag("zeta", "medium"),
            _flag("alpha", "high"),
            _flag("beta", "high"),
        ]
        result = compute_score(_qa(*flags), [_entry()])
        codes = [item["code"] for item in result["penalties"]]
        self.assertEqual(codes, ["alpha", "beta", "zeta"])
        points = [item["points"] for item in result["penalties"]]
        self.assertEqual(points, sorted(points, reverse=True))

    def test_penalty_shape_and_count(self):
        flags = [
            _flag("missing_source", "high"),
            _flag("missing_source", "high"),
        ]
        result = compute_score(_qa(*flags), [_entry()])
        self.assertEqual(
            result["penalties"],
            [
                {
                    "code": "missing_source",
                    "severity": "high",
                    "count": 2,
                    "points": 60.0,
                }
            ],
        )

    def test_deterministic(self):
        flags = [_flag("vague_language", "medium"), _flag("missing_source", "high")]
        entries = [_entry()]
        first = compute_score(_qa(*flags), entries)
        second = compute_score(_qa(*flags), entries)
        self.assertEqual(first, second)


class GateTests(unittest.TestCase):
    def test_no_policy_passes(self):
        gate = evaluate_gate(10, _qa(_flag("missing_source", "high")))
        self.assertEqual(gate, {"status": GATE_PASS, "reasons": []})

    def test_min_score_failure(self):
        config = WorkspaceConfig.from_dict({"min_score": 85})
        gate = evaluate_gate(70, _qa(), config)
        self.assertEqual(gate["status"], GATE_FAIL)
        self.assertEqual(len(gate["reasons"]), 1)
        self.assertIn("min_score", gate["reasons"][0])

    def test_max_high_failure(self):
        config = WorkspaceConfig.from_dict({"max_high": 0})
        qa = _qa(_flag("missing_source", "high"), _flag("missing_title", "high"))
        gate = evaluate_gate(90, qa, config)
        self.assertEqual(gate["status"], GATE_FAIL)
        self.assertEqual(len(gate["reasons"]), 1)
        self.assertIn("max_high", gate["reasons"][0])

    def test_both_limits_reported(self):
        config = WorkspaceConfig.from_dict({"min_score": 85, "max_high": 0})
        qa = _qa(_flag("missing_source", "high"))
        gate = evaluate_gate(70, qa, config)
        self.assertEqual(gate["status"], GATE_FAIL)
        self.assertEqual(len(gate["reasons"]), 2)

    def test_min_score_boundary_passes(self):
        config = WorkspaceConfig.from_dict({"min_score": 70})
        self.assertEqual(evaluate_gate(70, _qa(), config)["status"], GATE_PASS)

    def test_compute_score_embeds_gate(self):
        config = WorkspaceConfig.from_dict({"min_score": 85, "max_high": 0})
        result = compute_score(
            _qa(_flag("missing_source", "high")), [_entry()], config
        )
        self.assertEqual(result["gate"]["status"], GATE_FAIL)
        self.assertEqual(result["score"], 70)


if __name__ == "__main__":
    unittest.main()
