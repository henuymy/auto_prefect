"""Placeholder flow for running multiple report methods after one login."""

from prefect import flow


@flow(name="notify-multi-method-flow")
def notify_multi_method_flow():
    raise NotImplementedError("notify_multi_method_flow will be implemented after service migration.")

