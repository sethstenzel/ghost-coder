import asyncio
import json
from loguru import logger
from amqtt.broker import Broker
import paho.mqtt.client as Client


def broker_process(available_port, enable_logging=True):

    broker_config = {
        "listeners": {
            "default": {
                "type": "tcp",
                "bind": f"127.0.0.1:{available_port}",
            }
        },
    }

    """Run an embedded MQTT broker in its own process."""
    if enable_logging:
        logger.configure(
            handlers=[
                {
                    "sink": "ghost_coder.log",
                    "format": "[{time:YYYY-MM-DD HH:mm:ss}] {level} {name}:{function}:{line} - {message}",
                    "rotation": "10 MB",
                    "retention": "7 days",
                    "level": "DEBUG"
                },
                {
                    "sink": lambda msg: print(msg, end=""),
                    "format": "[{time:YYYY-MM-DD HH:mm:ss}] {level} {name}:{function}:{line} - {message}",
                    "level": "DEBUG"
                }
            ]
        )
        logger.enable("ghost_coder")
    else:
        logger.disable("ghost_coder")
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    broker = Broker(broker_config, loop=loop)
    shutdown_event = asyncio.Event()
    mqtt_client = None

    def on_message(client, userdata, message):
        """Handle incoming MQTT messages."""
        try:
            payload = json.loads(message.payload.decode())
        except json.JSONDecodeError:
            logger.warning(f"Received non-JSON message on {message.topic}: {message.payload.decode()}")
            return
        
        if message.topic == "BROKER" and payload.get("command") == "SHUTDOWN":
            logger.info("Received SHUTDOWN command")
            # Send CLOSE message to APP topic before shutting down
            close_msg = json.dumps({"command": "CLOSE"})
            client.publish("APP", close_msg, qos=0)
            logger.info("Sent CLOSE message to APP topic")
            # Schedule shutdown after a brief delay to allow message delivery
            async def delayed_shutdown():
                await asyncio.sleep(0.5)
                shutdown_event.set()
            asyncio.run_coroutine_threadsafe(delayed_shutdown(), loop)

        if message.topic == "APP" and payload.get("command") == "CLOSE":
            logger.info("Received SHUTDOWN command")
            loop.call_soon_threadsafe(shutdown_event.set)

    async def start_broker():
        nonlocal mqtt_client
        await broker.start()
        logger.info(f"Broker ready and accepting connections on 127.0.0.1:{available_port}")
        
        # Create a client to subscribe to the BROKER topic
        mqtt_client = Client.Client(Client.CallbackAPIVersion.VERSION1, "broker_listener")
        mqtt_client.on_message = on_message
        mqtt_client.connect("127.0.0.1", available_port)
        mqtt_client.subscribe([
            ("BROKER", 0),
            ("APP", 0),
        ])
        mqtt_client.loop_start()
        
        # Wait for shutdown event
        await shutdown_event.wait()
        mqtt_client.loop_stop()
        mqtt_client.disconnect()

    try:
        loop.run_until_complete(start_broker())
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt, shutting Broker down…")
    finally:
        loop.run_until_complete(broker.shutdown())
        loop.close()
        logger.info("Broker Stopped")

if __name__ == "__main__":
    broker_process(55555)