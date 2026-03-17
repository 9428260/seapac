import unittest

import pandas as pd

from seapac_agents.evaluation import EvaluationConfig, run_evaluation


class EvaluationTests(unittest.TestCase):
    def test_run_evaluation_uses_actual_execution_outputs(self) -> None:
        df = pd.DataFrame(
            {
                "community_net_kw": [10.0, -2.0],
                "community_load_kw": [100.0, 80.0],
                "avg_price_buy_krw_per_kwh": [120.0, 80.0],
                "dr_reduction_kw": [2.0, 0.0],
            }
        )
        summary = {
            "phase": 4,
            "n_steps_run": 2,
            "community_saving_krw": 500.0,
            "total_trades": 1,
            "total_matched_kwh": 2.5,
            "ess_total_discharged_kwh": 4.0,
            "ess_saving_krw": 100.0,
            "total_dr_reduction_kwh": 0.5,
            "validation_approved": True,
            "simulation_approved": True,
            "validation_errors_count": 0,
            "simulation_approval_errors_count": 0,
        }
        decisions = {
            "demand_response_events": [
                {"recommended_reduction_kw": 2.0},
            ]
        }
        report = run_evaluation(
            summary,
            df,
            decisions=decisions,
            config=EvaluationConfig(baseline_peak_kw=120.0, ess_degradation_cost_per_kwh=10.0),
        )

        self.assertEqual(report.grade, "A")
        self.assertEqual(report.kpis["energy_cost"]["total_grid_cost_krw"], 300.0)
        self.assertEqual(report.kpis["energy_cost"]["price_source"], "timeseries_avg_price_buy")
        self.assertEqual(report.kpis["user_acceptance"]["acceptance_rate_pct"], 100.0)
        self.assertEqual(report.kpis["operational_value"]["value_added_krw"], 560.0)

    def test_run_evaluation_rejects_unapproved_execution(self) -> None:
        df = pd.DataFrame(
            {
                "community_net_kw": [1.0],
                "community_load_kw": [50.0],
                "dr_reduction_kw": [0.0],
            }
        )
        summary = {
            "phase": 4,
            "n_steps_run": 1,
            "community_saving_krw": 1000.0,
            "total_trades": 3,
            "total_matched_kwh": 5.0,
            "ess_total_discharged_kwh": 2.0,
            "ess_saving_krw": 200.0,
            "validation_approved": True,
            "simulation_approved": False,
            "execution_approved": False,
            "validation_errors_count": 0,
            "simulation_approval_errors_count": 1,
        }

        report = run_evaluation(summary, df, config=EvaluationConfig(baseline_peak_kw=60.0))

        self.assertEqual(report.grade, "D")
        self.assertFalse(report.kpis["execution_quality"]["execution_approved"])


if __name__ == "__main__":
    unittest.main()
