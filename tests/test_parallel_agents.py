import unittest

from parallel_agents.contracts import decisions_to_candidate_bundle
from parallel_agents.orchestrator import run_parallel_evaluation_and_convert
from parallel_agents.policy_agent import PolicyConfig


class ParallelAgentsTests(unittest.TestCase):
    def test_decisions_bundle_preserves_stepwise_site_state_and_actions(self) -> None:
        state_json_list = [
            {
                "time": "2026-01-01 00:00:00",
                "community_state": {"total_load": 100.0, "pv_generation": 20.0},
                "market_state": {"grid_price": 100.0},
                "ess_state": {"soc": 50.0, "capacity": 200.0},
            },
            {
                "time": "2026-01-01 00:15:00",
                "community_state": {"total_load": 110.0, "pv_generation": 10.0},
                "market_state": {"grid_price": 120.0},
                "ess_state": {"soc": 9.0, "capacity": 200.0},
            },
        ]
        decisions = {
            "ess_schedule": [
                {"timestamp": "2026-01-01 00:00:00", "action": "discharge", "power_kw": 10.0, "soc_kwh": 100.0, "net_load_kw": 80.0, "reason": "step0"},
                {"timestamp": "2026-01-01 00:15:00", "action": "discharge", "power_kw": 10.0, "soc_kwh": 18.0, "net_load_kw": 100.0, "reason": "step1"},
            ],
            "trading_recommendations": [],
            "demand_response_events": [],
        }

        bundle = decisions_to_candidate_bundle(decisions, state_json_list)

        self.assertEqual(len(bundle["step_bundles"]), 2)
        self.assertEqual(bundle["step_bundles"][0]["site_state"]["ess_soc"], 50.0)
        self.assertEqual(bundle["step_bundles"][1]["site_state"]["ess_soc"], 9.0)
        self.assertEqual(len(bundle["step_bundles"][0]["candidate_actions"]), 1)
        self.assertEqual(len(bundle["step_bundles"][1]["candidate_actions"]), 1)

    def test_parallel_evaluation_applies_policy_per_step(self) -> None:
        state_json_list = [
            {
                "time": "2026-01-01 00:00:00",
                "community_state": {"total_load": 100.0, "pv_generation": 20.0},
                "market_state": {"grid_price": 100.0},
                "ess_state": {"soc": 50.0, "capacity": 200.0},
            },
            {
                "time": "2026-01-01 00:15:00",
                "community_state": {"total_load": 110.0, "pv_generation": 10.0},
                "market_state": {"grid_price": 120.0},
                "ess_state": {"soc": 9.0, "capacity": 200.0},
            },
        ]
        decisions = {
            "ess_schedule": [
                {"timestamp": "2026-01-01 00:00:00", "action": "discharge", "power_kw": 10.0, "soc_kwh": 100.0, "net_load_kw": 80.0, "reason": "step0"},
                {"timestamp": "2026-01-01 00:15:00", "action": "discharge", "power_kw": 10.0, "soc_kwh": 18.0, "net_load_kw": 100.0, "reason": "step1"},
            ],
            "trading_recommendations": [],
            "demand_response_events": [],
        }

        updated = run_parallel_evaluation_and_convert(
            decisions,
            state_json_list=state_json_list,
            policy_config=PolicyConfig(max_charge_kw=50.0, max_discharge_kw=50.0, ess_soc_min_pct=10.0),
            use_async=False,
        )

        self.assertEqual(len(updated["ess_schedule"]), 1)
        self.assertEqual(updated["ess_schedule"][0]["timestamp"], "2026-01-01 00:00:00")
        self.assertIn("ess_1", updated["parallel_layer"]["rejected_actions"])
        self.assertEqual(updated["parallel_layer"]["evaluated_steps"], 2)


if __name__ == "__main__":
    unittest.main()
