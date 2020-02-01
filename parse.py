#! usr/bin/env python
# Project: akrios-fe
# Filename: parse.py
#
# File Description: Parse messages we receive from the game engine.
#
# By: Jubelo
"""
    Housing the coroutines for parsing JSON messages we receive from the game engine.
"""

# Standard Library
import asyncio
import logging
import json
import subprocess
import time

# Standard Library Typing
from typing import (
    Callable,
    Dict
)

# Third Party
from telnetlib3 import WILL, WONT, ECHO  # type: ignore

# Project
from messages import Message
from messages import messages_to_clients
import clients
from keys import WS_SECRET

log: logging.Logger = logging.getLogger(__name__)


async def softboot_game(wait_time: int) -> None:
    """
        The game has notified that it will shutdown.  We take the wait_time, sleep that time and then
        launch the game.
    """
    await asyncio.sleep(wait_time)
    subprocess.Popen(['python3.8', '/home/bwp/PycharmProjects/akriosmud/src/akrios.py', '&'])


async def msg_heartbeat(msg: Dict[str, Dict[str, str]]) -> None:
    log.debug(f"The full message is: {msg}")
    log.debug(f"Heartbeat received from game at: {time.time()}")


async def msg_players_output(msg: Dict[str, Dict[str, str]]) -> None:
    """
        The msg is output for a player.  We .put that message into the asyncio.queue for that specific
        player.
    """
    session = msg["payload"]["uuid"]
    message = msg["payload"]["message"]
    if session in clients.PlayerConnection.connections:
        asyncio.create_task(messages_to_clients[session].put(Message("IO", message)))


async def msg_players_sign_in(msg: Dict[str, Dict[str, str]]) -> None:
    player = msg["payload"]["name"]
    session = msg["payload"]["uuid"]
    if session in clients.PlayerConnection.connections:
        log.debug(f"players/sign-in received for {player}@{session}")
        clients.PlayerConnection.connections[session].name = player


async def msg_players_sign_out(msg: Dict[str, Dict[str, str]]) -> None:
    player = msg["payload"]["name"]
    message = msg["payload"]["message"]
    session = msg["payload"]["uuid"]
    if session in clients.PlayerConnection.connections:
        log.debug(f'{msg["event"]} received for {player}@{session}')
        clients.PlayerConnection.connections[session].state["connected"] = False
        asyncio.create_task(messages_to_clients[session].put(Message("IO", message)))


async def msg_player_session_command(msg: Dict[str, Dict[str, str]]) -> None:
    session = msg["payload"]["uuid"]
    command = msg["payload"]["command"]
    if session in clients.PlayerConnection.connections:
        if clients.PlayerConnection.connections[session].conn_type == "telnet":
            if command == "do echo":
                message = WONT + ECHO
            elif command == "dont echo":
                message = WILL + ECHO
            else:
                message = WONT + ECHO
            asyncio.create_task(messages_to_clients[session].put(Message("COMMAND-TELNET", "", message)))


async def msg_game_softboot(msg: Dict[str, Dict[str, str]]) -> None:
    await softboot_game(int(msg["payload"]["wait_time"]))


messages: Dict[str, Callable] = {
    "players/output": msg_players_output,
    "players/sign-in": msg_players_sign_in,
    "players/sign-out": msg_players_sign_out,
    "players/login-failed": msg_players_sign_out,
    "player/session command": msg_player_session_command,
    "game/softboot": msg_game_softboot,
    "heartbeat": msg_heartbeat,
}


async def message_parse(inp: str):
    msg: Dict[str, Dict[str, str]] = json.loads(inp)

    if "secret" not in msg.keys() or msg["secret"] != WS_SECRET:
        log.warning("No secret in message header, or wrong key.")
        return

    if msg["event"] in messages:
        asyncio.create_task(messages[str(msg["event"])](msg))