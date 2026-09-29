"""The weights object: every number that shapes a page, changed by name for the benchmark."""

from __future__ import annotations

import unittest

from tailor_engine.selection.weights import DEFAULT_WEIGHTS, Weights, with_changes


class Changes(unittest.TestCase):
    def test_a_weight_is_changed_by_name_and_the_rest_kept(self) -> None:
        changed = with_changes(["family_boost=0", "depth_curve=0,0.2,0.5,0.8,0.9,1"])
        self.assertEqual(changed.family_boost, 0.0)
        self.assertEqual(changed.depth_curve, (0.0, 0.2, 0.5, 0.8, 0.9, 1.0))
        self.assertEqual(changed.standing, DEFAULT_WEIGHTS.standing)
        self.assertEqual(with_changes([]), DEFAULT_WEIGHTS)

    def test_an_unknown_name_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "no weight named"):
            with_changes(["standng=0.5"])

    def test_values_a_weight_cannot_take_are_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "merit_complexity is a share"):
            with_changes(["merit_complexity=1.5"])
        with self.assertRaisesRegex(ValueError, "cost must be a finite number of 0 or more"):
            with_changes(["cost=-0.1"])
        with self.assertRaisesRegex(ValueError, "should be name=number"):
            with_changes(["cost"])
        with self.assertRaisesRegex(ValueError, "should be name=number"):
            with_changes(["cost=high"])
        # A credit above 1 would count a neighbouring capability as meeting a requirement in full.
        with self.assertRaisesRegex(ValueError, "transferable is a share or credit"):
            with_changes(["transferable=1.5"])
        # The demand is divided by its step: 0 would stop every page.
        with self.assertRaisesRegex(ValueError, "demand_step must be more than 0"):
            with_changes(["demand_step=0"])


class DerivedShares(unittest.TestCase):
    def test_complements_are_exact_so_results_do_not_move(self) -> None:
        # Each pair was two literals before (0.6 and 0.4); deriving one must give the same float exactly.
        self.assertEqual(DEFAULT_WEIGHTS.merit_brand, 0.4)
        self.assertEqual(DEFAULT_WEIGHTS.order_emphasis, 0.4)
        self.assertEqual(DEFAULT_WEIGHTS.other_capabilities_share, 0.4)

    def test_the_largest_depth_step_follows_the_curve(self) -> None:
        self.assertAlmostEqual(DEFAULT_WEIGHTS.largest_depth_step, 0.30)
        self.assertAlmostEqual(Weights(depth_curve=(0.0, 0.5, 1.0)).largest_depth_step, 0.5)


if __name__ == "__main__":
    unittest.main()
