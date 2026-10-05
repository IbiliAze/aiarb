from typing import Any

import pytest

from aiarb.ports.calls import CallError, Cost, FatalCallError, RetryableCallError


class TestCost:
    def test_starts_at_zero(self) -> None:
        assert Cost() == Cost(usd=0.0, input_tokens=0, output_tokens=0)

    def test_costs_add_up(self) -> None:
        total = Cost(usd=1.25, input_tokens=100, output_tokens=20) + Cost(
            usd=0.5, input_tokens=50, output_tokens=5
        )

        assert total == Cost(usd=1.75, input_tokens=150, output_tokens=25)

    def test_sum_of_many_costs(self) -> None:
        costs = [Cost(usd=1.0, input_tokens=1, output_tokens=1)] * 3

        assert sum(costs, Cost()) == Cost(usd=3.0, input_tokens=3, output_tokens=3)

    @pytest.mark.parametrize(
        "fields", [{"usd": -0.01}, {"input_tokens": -1}, {"output_tokens": -1}]
    )
    def test_cannot_be_negative(self, fields: dict[str, Any]) -> None:
        with pytest.raises(ValueError, match="cannot be negative"):
            Cost(**fields)


class TestCallErrors:
    @pytest.mark.parametrize("error_type", [RetryableCallError, FatalCallError])
    def test_both_kinds_are_call_errors_and_carry_the_spend(
        self, error_type: type[CallError]
    ) -> None:
        error = error_type("rate limited", cost=Cost(usd=0.4))

        assert isinstance(error, CallError)
        assert error.cost == Cost(usd=0.4)
        assert str(error) == "rate limited"

    def test_spend_defaults_to_zero(self) -> None:
        assert CallError("no Claude Code install").cost == Cost()
