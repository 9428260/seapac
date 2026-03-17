import pickle
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from seapac_agents.execution import TradeAction, validate_trade_action
from simulation.agents.market import _planned_trade_controls
from simulation.model import ALFPSimulationModel


class ExecutionTradingTests(unittest.TestCase):
    def _build_data_file(self) -> str:
        ts = pd.DataFrame(
            [
                {
                    "timestamp": "2026-01-01 12:00:00",
                    "prosumer_id": "seller",
                    "prosumer_type": "Residential",
                    "load_kw": 1.0,
                    "pv_kw": 3.0,
                    "price_buy": 120.0,
                    "price_sell": 60.0,
                    "price_p2p": 80.0,
                },
                {
                    "timestamp": "2026-01-01 12:00:00",
                    "prosumer_id": "buyer",
                    "prosumer_type": "Residential",
                    "load_kw": 4.0,
                    "pv_kw": 0.0,
                    "price_buy": 120.0,
                    "price_sell": 60.0,
                    "price_p2p": 80.0,
                },
            ]
        )
        payload = {"timeseries": ts}
        tmpdir = tempfile.mkdtemp(prefix="exec-trading-test-")
        data_path = Path(tmpdir) / "sample.pkl"
        with open(data_path, "wb") as f:
            pickle.dump(payload, f)
        return str(data_path)

    def test_validate_trade_action_accepts_sell_grid(self) -> None:
        action = TradeAction(step=0, action="sell_grid", surplus_kw=2.0, bid_price=70.0)
        self.assertEqual(validate_trade_action(action), [])

    def test_planned_trade_controls_extracts_quota_and_price(self) -> None:
        controls = _planned_trade_controls(
            [
                {"action": "sell_p2p", "surplus_kw": 1.5, "bid_price": 91.0},
                {"action": "sell_p2p", "surplus_kw": 0.5, "bid_price": 89.0},
                {"action": "sell_grid", "surplus_kw": 0.75},
            ]
        )
        self.assertTrue(controls["has_explicit_plan"])
        self.assertEqual(controls["p2p_sell_kw"], 2.0)
        self.assertEqual(controls["grid_sell_kw"], 0.75)
        self.assertEqual(controls["bid_price"], 90.0)

    def test_simulation_caps_p2p_to_recommended_quantity(self) -> None:
        data_path = self._build_data_file()
        decisions = {
            "ess_schedule": [],
            "trading_recommendations": [
                {
                    "timestamp": "2026-01-01 12:00:00",
                    "action": "sell_p2p",
                    "surplus_kw": 1.0,
                    "bid_price": 90.0,
                }
            ],
            "demand_response_events": [],
        }

        model = ALFPSimulationModel(
            phase=4,
            data_path=data_path,
            n_steps=1,
            seed=42,
            alfp_decisions=decisions,
        )
        model.run()
        summary = model.summary()

        self.assertEqual(summary["planned_p2p_sell_kwh"], 0.25)
        self.assertEqual(summary["planned_grid_sell_kwh"], 0.0)
        self.assertEqual(summary["total_matched_kwh"], 0.25)
        self.assertEqual(summary["blocked_surplus_kwh"], 0.25)
        self.assertEqual(summary["p2p_execution_ratio_pct"], 100.0)

    def test_simulation_blocks_p2p_when_only_sell_grid_is_recommended(self) -> None:
        data_path = self._build_data_file()
        decisions = {
            "ess_schedule": [],
            "trading_recommendations": [
                {
                    "timestamp": "2026-01-01 12:00:00",
                    "action": "sell_grid",
                    "surplus_kw": 2.0,
                    "bid_price": 70.0,
                }
            ],
            "demand_response_events": [],
        }

        model = ALFPSimulationModel(
            phase=4,
            data_path=data_path,
            n_steps=1,
            seed=42,
            alfp_decisions=decisions,
        )
        model.run()
        summary = model.summary()

        self.assertEqual(summary["planned_p2p_sell_kwh"], 0.0)
        self.assertEqual(summary["planned_grid_sell_kwh"], 0.5)
        self.assertEqual(summary["total_matched_kwh"], 0.0)
        self.assertEqual(summary["blocked_surplus_kwh"], 0.5)


if __name__ == "__main__":
    unittest.main()
