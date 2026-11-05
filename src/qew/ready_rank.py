"""Explain ready ordering with graph facts, not an invented effort or throughput score."""

from dataclasses import dataclass
from enum import StrEnum
from typing import TypedDict

from qew import ledger_graph
from qew.ledger_event import State
from qew.ledger_ids import sort_key
from qew.ledger_item import Kind
from qew.ledger_store import Snapshot


class Order(StrEnum):
    PRIORITY = "priority"
    DEPENDENTS = "dependents"


class Metrics(TypedDict):
    rank: int
    direct_dependents: int
    transitive_dependents: int
    unblocks_now: int
    chain_depth: int


@dataclass(frozen=True)
class Impact:
    direct_dependents: int
    transitive_dependents: int
    unblocks_now: int
    chain_depth: int

    def document(self, rank: int) -> Metrics:
        return {"rank": rank, "direct_dependents": self.direct_dependents, "transitive_dependents": self.transitive_dependents,
                "unblocks_now": self.unblocks_now, "chain_depth": self.chain_depth}

    def facts(self, rank: int, order: Order) -> str:
        return (f"[rank={rank} order={order.value} direct={self.direct_dependents} trans={self.transitive_dependents} "
                f"unlocks={self.unblocks_now} depth={self.chain_depth}]")


@dataclass(frozen=True)
class Ranking:
    ids: list[str]
    impacts: dict[str, Impact]


def ranked(snapshot: Snapshot, candidates: list[str], order: Order, limit: int) -> Ranking:
    direct = {need: [item for item in items if snapshot.folds[item].state not in (State.COMPLETED, State.CLOSED)]
              for need, items in ledger_graph.blocks(snapshot).items()}
    depths = ledger_graph.chain_depths(snapshot, candidates)
    impacts: dict[str, Impact] = {}

    def impact(item_id: str) -> Impact:
        if item_id in impacts:
            return impacts[item_id]
        reached = {item_id}
        stack = [item_id]
        while stack:
            for child in direct.get(stack.pop(), []):
                if child not in reached:
                    reached.add(child)
                    stack.append(child)
        unblocks_now = 0
        for child in direct.get(item_id, []):
            item = snapshot.items[child]
            if (snapshot.folds[child].state == State.APPROVED and item.kind != Kind.EPIC
                    and not {ledger_graph.APPROVAL_LABEL, ledger_graph.GATED_LABEL}.intersection(item.labels)
                    and set(ledger_graph.blockers(snapshot, child)) == {item_id}):
                unblocks_now += 1
        found = Impact(len(direct.get(item_id, [])), len(reached) - 1, unblocks_now, depths[item_id])
        impacts[item_id] = found
        return found

    if order == Order.PRIORITY:
        ids = sorted(candidates, key=lambda i: (snapshot.items[i].priority, -depths[i], -len(direct.get(i, [])), sort_key(i)))
    else:
        ids = sorted(candidates, key=lambda i: (-impact(i).transitive_dependents, -impact(i).unblocks_now, -impact(i).chain_depth,
                                               -impact(i).direct_dependents, snapshot.items[i].priority, sort_key(i)))
    for item_id in ids[:limit] if limit else ids:
        impact(item_id)
    return Ranking(ids, impacts)
