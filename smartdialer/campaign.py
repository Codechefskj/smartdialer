"""Helpers for wiring up a campaign in the Store for demos/tests."""
from .store import Store


def build_campaign(store: Store, campaign_id: str, num_agents: int, num_borrowers: int) -> None:
    for _ in range(num_agents):
        store.add_agent(campaign_id)
    for i in range(num_borrowers):
        store.add_borrower(campaign_id, phone=f"+1555000{i:04d}")
