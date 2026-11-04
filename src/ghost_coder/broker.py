"""Embedded MQTT broker (amqtt) used for communication between Ghost Coder processes."""

import asyncio
import json
import logging

import paho.mqtt.client as mqtt
from amqtt.broker import Broker
from loguru import logger

from ghost_coder.utils import setup_logging, start_parent_watchdog, unique_client_id


def broker_process(broker_host: str, port: int, enable_logging: bool = False) -> None:
    """Run an embedded MQTT broker until a CLOSE message arrives on the APP topic."""
    setup_logging(enable_logging)
    # amqtt logs via stdlib logging; keep it quiet unless debugging (e.g. the startup port probe).
    logging.getLogger("amqtt").setLevel(logging.WARNING if enable_logging else logging.CRITICAL)

    config = {
        "listeners": {"default": {"type": "tcp", "bind": f"{broker_host}:{port}"}},
        # Local-only IPC broker: anonymous auth, no $SYS topics or packet logging.
        "plugins": {"amqtt.plugins.authentication.AnonymousAuthPlugin": {"allow_anonymous": True}},
    }

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    broker = Broker(config, loop=loop)
    shutdown_event = asyncio.Event()

    def request_shutdown():
        loop.call_soon_threadsafe(shutdown_event.set)

    def on_message(client, userdata, message):
        try:
            payload = json.loads(message.payload.decode())
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if isinstance(payload, dict) and "CLOSE" in (payload.get("command"), payload.get("cmd")):
            logger.info("Broker received CLOSE; shutting down shortly")
            # Give the other processes a moment to receive the CLOSE message too.
            loop.call_soon_threadsafe(loop.call_later, 0.5, shutdown_event.set)

    async def run():
        await broker.start()
        logger.info(f"Broker accepting connections on {broker_host}:{port}")
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, unique_client_id("broker"))
        client.on_message = on_message
        client.connect(broker_host, port)
        client.subscribe("APP", qos=0)
        client.loop_start()
        try:
            await shutdown_event.wait()
        finally:
            client.loop_stop()
            client.disconnect()

    start_parent_watchdog(request_shutdown)
    try:
        loop.run_until_complete(run())
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        try:
            loop.run_until_complete(broker.shutdown())
        except (Exception, asyncio.CancelledError) as e:
            # In-flight QoS1 deliveries to already-disconnected clients get cancelled here.
            logger.debug(f"Broker shutdown: {e!r}")
        loop.close()
        logger.info("Broker stopped")
