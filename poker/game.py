from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any

from treys import Card, Deck, Evaluator


class RoomError(Exception):
    pass


@dataclass
class Player:
    sid: str
    name: str
    seat: int

    chips: int = 1000
    buyin_total: int = 1000
    ready: bool = False

    hand: list[str] = field(default_factory=list)

    bet: int = 0  # bet in current betting round
    total_bet: int = 0  # total bet in this hand

    folded: bool = False
    all_in: bool = False

    last_action: str | None = None


@dataclass
class HandResult:
    winners: list[int]
    payouts: dict[int, int]
    ranking: list[tuple[int, int]]  # (seat, score) lower is better


class Table:
    def __init__(self, *, small_blind: int = 5, big_blind: int = 10):
        self.small_blind = small_blind
        self.big_blind = big_blind

        self.reset_table_state()

    def reset_table_state(self):
        self.started = False
        self.hand_no = 0

        self.stage = "waiting"  # waiting|preflop|flop|turn|river|showdown
        self.board: list[str] = []
        self.pot = 0

        self.dealer_seat: int | None = None
        self.sb_seat: int | None = None
        self.bb_seat: int | None = None
        self.utg_seat: int | None = None
        self.action_seat: int | None = None

        self.current_bet = 0
        self.min_raise = self.big_blind

        self._to_act: list[int] = []
        self._deck: Deck | None = None

        self.last_log: str | None = None

        self.showdown_reveal: dict[int, list[str]] = {}  # seat -> cards

    @staticmethod
    def _seat_order(seats: list[int], start_seat: int) -> list[int]:
        if not seats:
            return []
        seats_sorted = sorted(seats)
        start_idx = seats_sorted.index(start_seat) if start_seat in seats_sorted else 0
        return seats_sorted[start_idx:] + seats_sorted[:start_idx]

    @staticmethod
    def _next_seat(seats: list[int], current: int) -> int:
        order = sorted(seats)
        if not order:
            raise RoomError("no seats")
        if current not in order:
            return order[0]
        idx = order.index(current)
        return order[(idx + 1) % len(order)]

    @staticmethod
    def _prev_seat(seats: list[int], current: int) -> int:
        order = sorted(seats)
        if not order:
            raise RoomError("no seats")
        if current not in order:
            return order[-1]
        idx = order.index(current)
        return order[(idx - 1) % len(order)]

    @staticmethod
    def _format_card_html(c: str) -> str:
        rank = c[:-1].upper()
        suit = c[-1].lower()
        suit_map = {'s': '♠', 'h': '♥', 'd': '♦', 'c': '♣'}
        if rank == 'T':
            rank = '10'
        color = '#ff8585' if suit in 'hd' else 'inherit'
        return f'<span style="color:{color};font-weight:700;">{rank}{suit_map.get(suit, suit)}</span>'

    def _active_seats(self, players_by_seat: dict[int, Player]) -> list[int]:
        return [
            seat
            for seat, p in players_by_seat.items()
            if not p.folded and not p.all_in and p.chips > 0
        ]

    def _in_hand_seats(self, players_by_seat: dict[int, Player]) -> list[int]:
        return [seat for seat, p in players_by_seat.items() if not p.folded]

    def _ensure_turn(self, sid: str, players_by_seat: dict[int, Player], seat_of_sid: dict[str, int]):
        if self.action_seat is None:
            raise RoomError("牌局未开始")
        seat = seat_of_sid.get(sid)
        if seat is None:
            raise RoomError("你还未加入房间")
        if seat != self.action_seat:
            raise RoomError("还没轮到你")
        if players_by_seat[seat].folded:
            raise RoomError("你已弃牌")

    def _start_betting_round(self, players_by_seat: dict[int, Player], first_to_act: int):
        self.current_bet = max(p.bet for p in players_by_seat.values()) if players_by_seat else 0
        self.min_raise = max(self.big_blind, self.min_raise)

        seats = [seat for seat, p in players_by_seat.items() if not p.folded and not p.all_in]
        if not seats:
            self._to_act = []
            self.action_seat = None
            return

        order = self._seat_order(seats, first_to_act)
        self._to_act = order
        self.action_seat = self._to_act[0]

    def _remove_from_to_act(self, seat: int):
        if seat in self._to_act:
            self._to_act.remove(seat)
        self.action_seat = self._to_act[0] if self._to_act else None

    def _reset_round_bets(self, players_by_seat: dict[int, Player]):
        for p in players_by_seat.values():
            p.bet = 0
        self.current_bet = 0
        self.min_raise = self.big_blind

    def _deal(self, n: int) -> list[str]:
        if self._deck is None:
            raise RoomError("牌堆未准备")
        cards: list[str] = []
        for _ in range(n):
            c = self._deck.draw(1)[0]
            cards.append(Card.int_to_str(c))
        return cards

    def _post_blind(self, p: Player, amount: int) -> int:
        pay = min(amount, p.chips)
        p.chips -= pay
        p.bet += pay
        p.total_bet += pay
        self.pot += pay
        if p.chips == 0:
            p.all_in = True
        return pay

    def start_hand(self, players_by_seat: dict[int, Player]):
        seats_in_room = sorted(players_by_seat.keys())
        if len(seats_in_room) < 2:
            raise RoomError("至少需要 2 名玩家")

        self.started = True
        self.hand_no += 1
        self.stage = "preflop"
        self.board = []
        self.pot = 0
        self.showdown_reveal = {}

        self._deck = Deck()

        for p in players_by_seat.values():
            p.hand = []
            p.bet = 0
            p.total_bet = 0
            p.folded = False
            p.all_in = False
            p.last_action = None

        # Move dealer button
        if self.dealer_seat is None:
            self.dealer_seat = seats_in_room[0]
        else:
            self.dealer_seat = self._next_seat(seats_in_room, self.dealer_seat)

        # Deal hole cards
        for seat in self._seat_order(seats_in_room, self._next_seat(seats_in_room, self.dealer_seat)):
            players_by_seat[seat].hand = self._deal(2)

        # Post blinds
        if len(seats_in_room) == 2:
            sb_seat = self.dealer_seat
            bb_seat = self._next_seat(seats_in_room, self.dealer_seat)
        else:
            sb_seat = self._next_seat(seats_in_room, self.dealer_seat)
            bb_seat = self._next_seat(seats_in_room, sb_seat)

        self.sb_seat = sb_seat
        self.bb_seat = bb_seat

        sb_paid = self._post_blind(players_by_seat[sb_seat], self.small_blind)
        bb_paid = self._post_blind(players_by_seat[bb_seat], self.big_blind)

        self.current_bet = max(sb_paid, bb_paid)
        self.min_raise = self.big_blind

        # First to act preflop
        if len(seats_in_room) == 2:
            first = sb_seat
        else:
            first = self._next_seat(seats_in_room, bb_seat)

        # "枪口"：翻牌前第一个行动位置（UTG）
        self.utg_seat = first

        self._start_betting_round(players_by_seat, first)

    def _advance_stage(self, players_by_seat: dict[int, Player]):
        seats_in_room = sorted(players_by_seat.keys())
        if self.stage == "preflop":
            self.stage = "flop"
            self.board += self._deal(3)
        elif self.stage == "flop":
            self.stage = "turn"
            self.board += self._deal(1)
        elif self.stage == "turn":
            self.stage = "river"
            self.board += self._deal(1)
        elif self.stage == "river":
            self.stage = "showdown"
        else:
            return

        if self.stage != "showdown":
            self._reset_round_bets(players_by_seat)

            # First to act postflop
            if len(seats_in_room) == 2:
                first = self.dealer_seat  # dealer acts first postflop in heads-up
            else:
                first = self._next_seat(seats_in_room, self.dealer_seat)
            self._start_betting_round(players_by_seat, first)

    def _maybe_finish_early(self, players_by_seat: dict[int, Player]) -> bool:
        in_hand = [seat for seat, p in players_by_seat.items() if not p.folded]
        if len(in_hand) == 1:
            winner_seat = in_hand[0]
            players_by_seat[winner_seat].chips += self.pot
            self.last_log = f"座位 {winner_seat} 赢得底池 {self.pot}（其他人弃牌）"
            self.stage = "waiting"
            self.started = False
            self.action_seat = None
            self._to_act = []
            return True
        return False

    def _all_active_all_in_or_matched(self, players_by_seat: dict[int, Player]) -> bool:
        for p in players_by_seat.values():
            if p.folded:
                continue
            if p.all_in:
                continue
            if p.bet != self.current_bet:
                return False
        return True

    def _auto_run_if_all_in(self, players_by_seat: dict[int, Player]):
        # If everyone remaining is all-in or matched and nobody needs to act, deal remaining streets.
        if self.action_seat is not None:
            return
        if not self._all_active_all_in_or_matched(players_by_seat):
            return

        while self.stage in ("preflop", "flop", "turn", "river"):
            self._advance_stage(players_by_seat)
            if self.stage == "showdown":
                break
            # If no one can act (all-in), keep dealing.
            if any((not p.folded and not p.all_in) for p in players_by_seat.values()):
                break
            self.action_seat = None
            self._to_act = []

    def apply_action(
        self,
        *,
        sid: str,
        action_type: str,
        amount: int | None,
        players_by_seat: dict[int, Player],
        seat_of_sid: dict[str, int],
    ):
        if not self.started or self.stage == "waiting":
            raise RoomError("牌局未开始")

        self._ensure_turn(sid, players_by_seat, seat_of_sid)
        seat = seat_of_sid[sid]
        p = players_by_seat[seat]

        action_type = (action_type or "").lower().strip()

        if action_type == "fold":
            p.folded = True
            p.last_action = "fold"
            self.last_log = f"{p.name} 弃牌"
            self._remove_from_to_act(seat)
            if self._maybe_finish_early(players_by_seat):
                return

        elif action_type == "check":
            if p.bet != self.current_bet:
                raise RoomError("不能过牌，需要跟注或弃牌")
            p.last_action = "check"
            self.last_log = f"{p.name} 过牌"
            self._remove_from_to_act(seat)

        elif action_type == "call":
            need = max(0, self.current_bet - p.bet)
            pay = min(need, p.chips)
            p.chips -= pay
            p.bet += pay
            p.total_bet += pay
            self.pot += pay
            if p.chips == 0 and need > 0:
                p.all_in = True
            p.last_action = "call" if need > 0 else "check"
            self.last_log = f"{p.name} 跟注 {pay}" if need > 0 else f"{p.name} 过牌"
            self._remove_from_to_act(seat)

        elif action_type == "raise":
            if amount is None:
                raise RoomError("需要填写加注到的金额")
            try:
                raise_to = int(amount)
            except Exception:
                raise RoomError("加注金额无效")

            if raise_to <= self.current_bet:
                # Treat as call
                need = max(0, self.current_bet - p.bet)
                pay = min(need, p.chips)
                p.chips -= pay
                p.bet += pay
                p.total_bet += pay
                self.pot += pay
                if p.chips == 0 and need > 0:
                    p.all_in = True
                p.last_action = "call" if need > 0 else "check"
                self.last_log = f"{p.name} calls {pay}" if need > 0 else f"{p.name} checks"
                self._remove_from_to_act(seat)
            else:
                if raise_to > p.bet + p.chips:
                    raise RoomError("筹码不足")

                raise_amount = raise_to - self.current_bet
                is_all_in = raise_to == p.bet + p.chips

                if raise_amount < self.min_raise and not is_all_in:
                    raise RoomError(f"最小加注增量为 {self.min_raise}")

                delta = raise_to - p.bet
                p.chips -= delta
                p.bet = raise_to
                p.total_bet += delta
                self.pot += delta
                if p.chips == 0:
                    p.all_in = True

                self.min_raise = max(self.min_raise, raise_to - self.current_bet)
                self.current_bet = raise_to

                p.last_action = "raise"
                self.last_log = f"{p.name} 加注到 {raise_to}"

                # Reset to_act: everyone else who can still act
                seats_can_act = [
                    s
                    for s, pl in players_by_seat.items()
                    if s != seat and not pl.folded and not pl.all_in
                ]
                if seats_can_act:
                    order = self._seat_order(seats_can_act, self._next_seat(sorted(players_by_seat.keys()), seat))
                    self._to_act = order
                    self.action_seat = self._to_act[0]
                else:
                    self._to_act = []
                    self.action_seat = None

        else:
            raise RoomError("未知操作")

        # End betting round?
        if not self._to_act and self.started and self.stage in ("preflop", "flop", "turn", "river"):
            self._advance_stage(players_by_seat)

        self._auto_run_if_all_in(players_by_seat)

    def finish_showdown(self, players_by_seat: dict[int, Player]) -> HandResult:
        if self.stage != "showdown":
            raise RoomError("尚未进入摊牌")

        evaluator = Evaluator()

        board = [Card.new(c) for c in self.board]
        contenders = [seat for seat, p in players_by_seat.items() if not p.folded]
        if len(contenders) == 1:
            winner = contenders[0]
            players_by_seat[winner].chips += self.pot
            self.showdown_reveal[winner] = players_by_seat[winner].hand
            return HandResult(winners=[winner], payouts={winner: self.pot}, ranking=[(winner, 0)])

        # Scores
        scores: dict[int, int] = {}
        for seat in contenders:
            hand = [Card.new(c) for c in players_by_seat[seat].hand]
            scores[seat] = evaluator.evaluate(board, hand)

        ranking = sorted(((seat, score) for seat, score in scores.items()), key=lambda x: x[1])

        # Side pots based on total_bet from all players (including folded)
        totals = {seat: p.total_bet for seat, p in players_by_seat.items()}
        levels = sorted({t for t in totals.values() if t > 0})

        payouts: dict[int, int] = {seat: 0 for seat in players_by_seat.keys()}
        prev = 0
        for level in levels:
            contributors = [seat for seat, t in totals.items() if t >= level]
            pot_amount = (level - prev) * len(contributors)
            prev = level

            eligible = [seat for seat in contributors if seat in contenders]
            if not eligible:
                continue

            best_score = min(scores[s] for s in eligible)
            winners = [s for s in eligible if scores[s] == best_score]

            share = pot_amount // len(winners)
            remainder = pot_amount - share * len(winners)

            for s in winners:
                payouts[s] += share

            # Remainder chips: distribute in seat order from left of dealer
            if remainder:
                seats_sorted = sorted(winners)
                for i in range(remainder):
                    payouts[seats_sorted[i % len(seats_sorted)]] += 1

        # Apply payouts
        winners_final: list[int] = []
        for seat, amount in payouts.items():
            if amount > 0:
                players_by_seat[seat].chips += amount
        winners_final = [seat for seat, amt in payouts.items() if amt > 0 and seat in contenders]

        for seat in contenders:
            self.showdown_reveal[seat] = players_by_seat[seat].hand

        return HandResult(winners=winners_final, payouts={k: v for k, v in payouts.items() if v}, ranking=ranking)


class GameRoom:
    def __init__(self, *, room_id: str):
        self.room_id = room_id
        self.players: dict[str, Player] = {}  # sid -> Player
        self.table = Table()
        self.logs: list[dict[str, Any]] = []
        self.chat: list[dict[str, Any]] = []

    def _players_by_seat(self) -> dict[int, Player]:
        return {p.seat: p for p in self.players.values()}

    def _seat_of_sid(self) -> dict[str, int]:
        return {sid: p.seat for sid, p in self.players.items()}

    def _next_free_seat(self) -> int:
        used = {p.seat for p in self.players.values()}
        for seat in range(1, 10):
            if seat not in used:
                return seat
        raise RoomError("座位已满")

    def add_log(self, message: str):
        self.logs.append({"t": int(time.time()), "msg": message})
        self.logs = self.logs[-200:]

    def add_chat(self, sid: str, text: str):
        text = (text or "").strip()
        if not text:
            return
        name = self.players.get(sid).name if sid in self.players else "?"
        self.chat.append({"t": int(time.time()), "name": name, "text": text[:300]})
        self.chat = self.chat[-200:]

    def add_player(self, *, sid: str, name: str | None) -> Player:
        name = (name or "").strip() or f"Player{random.randint(100, 999)}"
        if sid in self.players:
            self.players[sid].name = name
            return self.players[sid]

        seat = self._next_free_seat()
        p = Player(sid=sid, name=name, seat=seat)
        self.players[sid] = p
        return p

    def buyin(self, *, sid: str, amount: int = 1000):
        if sid not in self.players:
            raise RoomError("你还未加入房间")
        if self.table.started:
            raise RoomError("牌局进行中，不能买入")
        if amount <= 0:
            raise RoomError("买入金额无效")

        p = self.players[sid]
        p.chips += amount
        p.buyin_total += amount
        net = p.chips - p.buyin_total
        self.add_log(f"{p.name} 买入 +{amount}，现有积分 {p.chips}，净积分 {net}")

    def remove_player(self, sid: str):
        if sid not in self.players:
            return

        # If a hand is running and it's the leaving player's turn, auto-fold.
        leaving_seat = self.players[sid].seat
        players_by_seat = self._players_by_seat()
        seat_of_sid = self._seat_of_sid()

        if self.table.started and self.table.action_seat == leaving_seat:
            try:
                self.table.apply_action(
                    sid=sid,
                    action_type="fold",
                    amount=None,
                    players_by_seat=players_by_seat,
                    seat_of_sid=seat_of_sid,
                )
                if self.table.last_log:
                    self.add_log(self.table.last_log)
            except Exception:
                pass

        del self.players[sid]

    def toggle_ready(self, sid: str):
        if sid not in self.players:
            raise RoomError("你还未加入房间")
        self.players[sid].ready = not self.players[sid].ready
        self.add_log(f"{self.players[sid].name}{' 已准备' if self.players[sid].ready else ' 取消准备'}")

    def start_hand(self, requester_sid: str):
        if requester_sid not in self.players:
            raise RoomError("你还未加入房间")
        if len(self.players) < 2:
            raise RoomError("至少需要 2 名玩家")
        if not all(p.ready for p in self.players.values()):
            raise RoomError("还有玩家未准备")

        players_by_seat = self._players_by_seat()
        self.table.start_hand(players_by_seat)
        self.add_log(f"第 {self.table.hand_no} 局开始")
        self.add_log(f"阶段：{self.table.stage}")

    def player_action(self, *, sid: str, action_type: str, amount: int | None):
        players_by_seat = self._players_by_seat()
        seat_of_sid = self._seat_of_sid()

        self.table.apply_action(
            sid=sid,
            action_type=action_type,
            amount=amount,
            players_by_seat=players_by_seat,
            seat_of_sid=seat_of_sid,
        )

        if self.table.last_log:
            self.add_log(self.table.last_log)

        if self.table.stage == "showdown":
            result = self.table.finish_showdown(players_by_seat)

            evaluator = Evaluator()
            class_zh = {
                "High Card": "高牌",
                "Pair": "一对",
                "Two Pair": "两对",
                "Three of a Kind": "三条",
                "Straight": "顺子",
                "Flush": "同花",
                "Full House": "葫芦",
                "Four of a Kind": "四条",
                "Straight Flush": "同花顺",
                "Royal Flush": "皇家同花顺",
            }

            def best_five_cards(seat: int) -> list[int]:
                board_ints = [Card.new(c) for c in self.table.board]
                hand_ints = [Card.new(c) for c in players_by_seat[seat].hand]
                seven = board_ints + hand_ints
                best_combo: tuple[int, ...] | None = None
                best_rank = 10**9
                for combo in combinations(seven, 5):
                    r = evaluator._five(list(combo))
                    if r < best_rank:
                        best_rank = r
                        best_combo = combo
                return list(best_combo) if best_combo else []

            self.add_log("摊牌结算")

            # 显示手牌
            for seat, _ in result.ranking:
                p = players_by_seat[seat]
                hand_html = ' '.join(Table._format_card_html(c) for c in p.hand)
                self.add_log(f"{p.name} 手牌：{hand_html}")

            # Also note folded players.
            for p in sorted(players_by_seat.values(), key=lambda x: x.seat):
                if p.folded:
                    self.add_log(f"{p.name} 已弃牌")

            # Payouts + points (积分=当前筹码)
            for seat, amt in result.payouts.items():
                p = players_by_seat.get(seat)
                if not p:
                    continue
                self.add_log(f"{p.name} 赢得 {amt}，当前积分 {p.chips}")

            self.table.stage = "waiting"
            self.table.started = False
            self.table.action_seat = None
            self.table._to_act = []
            # Reset ready to force re-ready for next hand
            for p in self.players.values():
                p.ready = False

    def public_state(self) -> dict[str, Any]:
        players = sorted(self.players.values(), key=lambda p: p.seat)
        state = {
            "room": self.room_id,
            "handNo": self.table.hand_no,
            "started": self.table.started,
            "stage": self.table.stage,
            "dealerSeat": self.table.dealer_seat,
            "sbSeat": self.table.sb_seat,
            "bbSeat": self.table.bb_seat,
            "utgSeat": self.table.utg_seat,
            "actionSeat": self.table.action_seat,
            "pot": self.table.pot,
            "board": self.table.board,
            "currentBet": self.table.current_bet,
            "minRaise": self.table.min_raise,
            "players": [
                {
                    "sid": p.sid,
                    "name": p.name,
                    "seat": p.seat,
                    "chips": p.chips,
                    "buyinTotal": p.buyin_total,
                    "net": p.chips - p.buyin_total,
                    "bet": p.bet,
                    "totalBet": p.total_bet,
                    "folded": p.folded,
                    "allIn": p.all_in,
                    "ready": p.ready,
                    "lastAction": p.last_action,
                }
                for p in players
            ],
            "logs": self.logs[-80:],
            "chat": self.chat[-80:],
            "showdown": self.table.showdown_reveal,
        }
        return state

    def private_state(self, sid: str) -> dict[str, Any]:
        hand = self.players[sid].hand if sid in self.players else []
        return {"sid": sid, "hand": hand}
