# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Policy: the shipping agent must never access billing tools."""

from types import SimpleNamespace

from app.agent import (
    BILLING_TOOL_NAMES,
    ShippingSpecialist,
    deny_billing_tools,
)


def _fake(name: str) -> SimpleNamespace:
    return SimpleNamespace(name=name)


def test_shipping_agent_has_no_billing_tool_in_its_toolset() -> None:
    names = {t.name for t in ShippingSpecialist.tools if hasattr(t, "name")}
    assert not (names & BILLING_TOOL_NAMES)


def test_shipping_agent_enforces_the_policy_at_call_time() -> None:
    assert ShippingSpecialist.before_tool_callback is deny_billing_tools


def test_billing_tool_call_is_blocked() -> None:
    ctx = SimpleNamespace(agent_name="ShippingSpecialist")
    result = deny_billing_tools(_fake("lookup_invoice"), {"invoice_id": "INV-1"}, ctx)
    assert result is not None
    assert "Access denied" in result["error"]


def test_shipping_tool_call_is_allowed() -> None:
    ctx = SimpleNamespace(agent_name="ShippingSpecialist")
    assert deny_billing_tools(_fake("get_order_status"), {"order_id": "ORD-1"}, ctx) is None
