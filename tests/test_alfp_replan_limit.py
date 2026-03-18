import unittest

from alfp.pipeline.graph import (
    MAX_PLAN_REPLANS,
    _route_after_policy_gate,
    _route_after_sandbox,
    _route_after_validation,
    replan_node,
)


class ALFPReplanLimitTests(unittest.TestCase):
    def test_validation_routes_to_replan_only_before_second_retry(self) -> None:
        state = {
            "validation_metrics": {
                "kpi": {
                    "MAPE_pass": False,
                    "peak_acc_pass": True,
                }
            },
            "plan_retry_count": MAX_PLAN_REPLANS - 1,
            "max_plan_retries": 99,
        }

        self.assertEqual(_route_after_validation(state), "replan")

        state["plan_retry_count"] = MAX_PLAN_REPLANS
        self.assertEqual(_route_after_validation(state), "decision")

    def test_governance_replan_routes_stop_after_second_retry(self) -> None:
        state = {
            "policy_gate_result": {"status": "REPLAN_REQUIRED"},
            "simulation_result": {"replan_required": True},
            "plan_retry_count": MAX_PLAN_REPLANS,
            "max_plan_retries": 99,
        }

        self.assertEqual(_route_after_policy_gate(state), "save_memory")
        self.assertEqual(_route_after_sandbox(state), "save_memory")

    def test_replan_node_clamps_retry_count_to_two(self) -> None:
        out = replan_node(
            {
                "plan_retry_count": MAX_PLAN_REPLANS,
                "max_plan_retries": 99,
                "messages": [],
            }
        )

        self.assertEqual(out["plan_retry_count"], MAX_PLAN_REPLANS)
        self.assertEqual(out["max_plan_retries"], MAX_PLAN_REPLANS)


if __name__ == "__main__":
    unittest.main()
