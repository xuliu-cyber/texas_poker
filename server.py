import argparse
import os
from dataclasses import asdict

import eventlet

eventlet.monkey_patch()

from flask import Flask, render_template, request  # noqa: E402
from flask_socketio import SocketIO, emit, join_room, leave_room  # noqa: E402

from poker.game import GameRoom, RoomError  # noqa: E402


def create_app() -> tuple[Flask, SocketIO]:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev")

    socketio = SocketIO(
        app,
        cors_allowed_origins="*",
        async_mode="eventlet",
        logger=False,
        engineio_logger=False,
    )

    @app.get("/")
    def index():
        return render_template("index.html")

    return app, socketio


app, socketio = create_app()


ROOMS: dict[str, GameRoom] = {}


def get_room(room_id: str) -> GameRoom:
    room_id = (room_id or "").strip()
    if not room_id:
        raise RoomError("room is required")
    room = ROOMS.get(room_id)
    if room is None:
        room = GameRoom(room_id=room_id)
        ROOMS[room_id] = room
    return room


def emit_room_state(room: GameRoom):
    public_state = room.public_state()
    socketio.emit("room_state", public_state, to=room.room_id)

    for player in room.players.values():
        socketio.emit("private_state", room.private_state(player.sid), to=player.sid)


@socketio.on("connect")
def on_connect():
    emit("hello", {"ok": True})


@socketio.on("disconnect")
def on_disconnect():
    sid = request.sid
    # Remove player from any room
    for room in list(ROOMS.values()):
        if sid in room.players:
            name = room.players[sid].name
            room.remove_player(sid)
            leave_room(room.room_id)
            room.add_log(f"{name} 离开房间")
            emit_room_state(room)


@socketio.on("join")
def on_join(data):
    sid = request.sid
    room_id = (data or {}).get("room")
    name = (data or {}).get("name")

    room = get_room(room_id)
    player = room.add_player(sid=sid, name=name)

    join_room(room.room_id)
    room.add_log(f"{player.name} 加入房间（座位 {player.seat}）")

    emit_room_state(room)


@socketio.on("leave")
def on_leave(data):
    sid = request.sid
    room_id = (data or {}).get("room")
    room = get_room(room_id)

    if sid in room.players:
        name = room.players[sid].name
        room.remove_player(sid)
        leave_room(room.room_id)
        room.add_log(f"{name} 离开房间")
        emit_room_state(room)


@socketio.on("ready")
def on_ready(data):
    sid = request.sid
    room_id = (data or {}).get("room")

    room = get_room(room_id)
    room.toggle_ready(sid)

    # Auto-start when everyone is ready.
    if (not room.table.started) and len(room.players) >= 2 and all(p.ready for p in room.players.values()):
        try:
            room.start_hand(requester_sid=sid)
        except Exception:
            # If a race happens (e.g., disconnect), just fall back to waiting.
            pass
    emit_room_state(room)


@socketio.on("start")
def on_start(data):
    sid = request.sid
    room_id = (data or {}).get("room")

    room = get_room(room_id)
    room.start_hand(requester_sid=sid)
    emit_room_state(room)


@socketio.on("action")
def on_action(data):
    sid = request.sid
    room_id = (data or {}).get("room")
    action_type = (data or {}).get("type")
    amount = (data or {}).get("amount")

    room = get_room(room_id)
    room.player_action(sid=sid, action_type=action_type, amount=amount)
    emit_room_state(room)


@socketio.on("chat")
def on_chat(data):
    sid = request.sid
    room_id = (data or {}).get("room")
    text = (data or {}).get("text")

    room = get_room(room_id)
    room.add_chat(sid=sid, text=text)
    emit_room_state(room)


@socketio.on("buyin")
def on_buyin(data):
    sid = request.sid
    room_id = (data or {}).get("room")
    amount = (data or {}).get("amount", 1000)

    room = get_room(room_id)
    try:
        amount_int = int(amount)
    except Exception:
        amount_int = 1000
    room.buyin(sid=sid, amount=amount_int)
    emit_room_state(room)


@socketio.on_error_default
def on_error(e):
    # Best-effort error mapping to client.
    try:
        message = str(e)
    except Exception:
        message = "server error"
    emit("error", {"message": message})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()

    socketio.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
