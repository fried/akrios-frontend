#! usr/bin/env python
# Project: akrios-frontend
# Filename: servers.py
#
# File Description: Game Engine connection websocket.
#
# By: Jubelo
"""
    Housing the Class(es) and coroutines for building and maintaining a websocket connection with
    the game engine.
"""

# Standard Library
import asyncio
import logging
import json
from uuid import uuid4

# Third Party

# Project
from messages import messages_to_game
import clients
from keys import WS_SECRET
import parse

log: logging.Logger = logging.getLogger(__name__)

connections = {}


class GameConnection(object):
    """
        Each connection when created in async ws_handler will instance this class.

        Instance variables:
            self.state is the current state of the game connection
            self.uuid is a str(uuid.uuid4()) used for unique game connection session tracking
    """

    def __init__(self):
        self.state = {"connected": True}
        self.uuid = str(uuid4())


def register_client(game_connection):
    """
        Upon a new game connection, we register it to the GameConnection Class.
    """
    log.debug(f"servers.py:register_client - Adding game {game_connection.uuid} to connections")
    connections[game_connection.uuid] = game_connection


def unregister_client(game_connection):
    """
        Upon an existing game disconnecting, we unregister it.
    """
    if game_connection.uuid in connections:
        log.debug(f"servers.py:unregister_client - Deleting game {game_connection.uuid} from connections")
        connections.pop(game_connection.uuid)


async def ws_heartbeat(websocket_, game_connection):
    """
        Create a JSON heartbeat payload, create the send task, then await a 10 second sleep.
        This effectively sends a heartbeat to the game engine every 10 seconds.
    """
    while game_connection.state["connected"]:
        msg = {
            "event": "heartbeat",
            "tasks": len(asyncio.all_tasks()),
            "secret": WS_SECRET,
        }

        log.info(msg)

        asyncio.create_task(websocket_.send(json.dumps(msg, sort_keys=True, indent=4)))
        await asyncio.sleep(10)


async def softboot_connection_list(websocket_):
    """
        When a game connects to this front end, part of the handler's responsibility
        is to verify if there are current connections to this front end.  If so then we may
        assume that the game/FE have performed a "soft boot", or the game was restarted.

        Create a JSON message to the game to indicate the session ID to player name mapping
        so that the player(s) may be logged back in automatically.
    """
    sessions = {}
    for session_id, client in clients.connections.items():
        sessions[session_id] = [client.name.lower(), client.addr, client.port]

    payload = {"players": sessions}
    msg = {
        "event": "game/load_players",
        "secret": WS_SECRET,
        "payload": payload,
    }
    log.debug(f"servers.py:softboot_connection_list - Notifying game engine of connections:\n\r{msg}")
    await websocket_.send(json.dumps(msg, sort_keys=True, indent=4))


async def ws_read(websocket_, game_connection):
    """
        We want this coroutine to run while the game is connected, so we begin with a while loop.
        We first await control back to the main loop until we have received some data from the game.
        Create task to parse / handle the message from the game engine.
    """
    while game_connection.state["connected"]:
        if data := await websocket_.recv():
            log.debug(f"servers.py:ws_read - Received from game: {str(data)}")
            asyncio.create_task(parse.message_parse(data))
        else:
            game_connection.state["connected"] = False  # EOF Disconnect


async def ws_write(websocket_, game_connection):
    """
        We want this coroutine to run while the game is connected, so we begin with a while loop.
        Await for the messages_to_game Queue to have a message for the game.
        Create a task to send that message to the game engine.
    """
    while game_connection.state["connected"]:
        msg_obj = await messages_to_game.get()
        log.debug(f"servers.py:ws_write - Message sent to game: {msg_obj.msg}")

        asyncio.create_task(websocket_.send(msg_obj.msg))


async def ws_handler(websocket_, path):
    """
        This is a generic websocket handler/"shell".  It is called on new connections of websocket
        clients, which would be the game connecting to this front end.

        Start by taking our new connection, instantiate a GameConnection and register it.
        Create our three coroutine tasks associated with _this connection_.

        This coroutine will run while we have active coroutines associated with it.

    """
    game_connection = GameConnection()
    register_client(game_connection)

    log.debug(f"servers.py:ws_handler - Received websocket connection from game at : {websocket_} {path}")

    tasks = [
        asyncio.create_task(ws_heartbeat(websocket_, game_connection), name=f"WS: {game_connection.uuid} hb",),
        asyncio.create_task(ws_read(websocket_, game_connection), name=f"WS: {game_connection.uuid} read",),
        asyncio.create_task(ws_write(websocket_, game_connection), name=f"WS: {game_connection.uuid} write",),
    ]

    asyncio.current_task().set_name(f"WS: {game_connection.uuid} handler")  # type: ignore

    # When a game connection to this front end happens, we make an assumption that if have
    # clients in clients.PlayerConnection.connections that the game has "softboot"ed or has
    # crashed and restarted.  Await a coroutine which informs the game of those client
    # details so that they can be automatically logged back in.
    if clients.connections:
        log.debug("servers.py:ws_handler - Game connected to Front End.  Clients exist, await softboot_connection_list")
        await softboot_connection_list(websocket_)

    _, pending = await asyncio.wait(tasks, return_when="FIRST_COMPLETED")

    # Cancel any tasks associated with the 'current' game connection based on task name
    # containing a uuid for that connection.  This prevents the softboot, client and any other
    # task from cancelling unless they were specific to the connection.
    for task in asyncio.all_tasks():
        if game_connection.uuid in task.get_name():
            task.cancel()

    unregister_client(game_connection)
    log.info(f"servers.py:ws_handler - Closing websocket")
