import asyncio
import websockets
import json
from datetime import datetime, timezone
from kafka import KafkaProducer


# load API_Key from .env file
from dotenv import load_dotenv
import os
load_dotenv()
API_Key = os.getenv("API_Key")

async def connect_ais_stream():

    async with websockets.connect("wss://stream.aisstream.io/v0/stream") as websocket:
        
        message_counter = 0
        message_counter_stats_interval = 10000
        message_counter_start = 0
        timestamp_start = datetime.now(timezone.utc)

        # Connect to Kafka broker running in Docker
        producer = KafkaProducer(
            bootstrap_servers='host.docker.internal:9093',
            value_serializer=lambda v: json.dumps(v).encode('utf-8')
        )
        
        # subscribe async to the AIS stream with the API key and bounding box
        subscribe_message = {"APIKey": API_Key, "BoundingBoxes": [[[-90, -180], [90, 180]]]}
        subscribe_message_json = json.dumps(subscribe_message)
        await websocket.send(subscribe_message_json)

        async for message_json in websocket:
            message = json.loads(message_json)
            # print(message)
            producer.send(message["MessageType"], message)  # Send the raw JSON message to Kafka
            producer.flush()  # Ensure the message is sent to Kafka

            # some load statistics
            if message_counter == 0:
                timestamp_start = datetime.now(timezone.utc)      
                print(f"[{timestamp_start}] Connected to AIS stream, starting to process messages...")
                          
            message_counter += 1

            if message_counter % message_counter_stats_interval == 0:
                print(f"[{datetime.now(timezone.utc)}] Processed {message_counter} messages")
                # create some statistisc in json format and send to Kafka
                stats = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "interval_seconds": (datetime.now(timezone.utc) - timestamp_start).total_seconds(),
                    "interval_message_count": message_counter - message_counter_start,
                    "interval_message_rate": (message_counter - message_counter_start) / (datetime.now(timezone.utc) - timestamp_start).total_seconds(),
                    "message_count": message_counter
                }
                timestamp_start = datetime.now(timezone.utc)  # reset the timer
                message_counter_start = message_counter  # reset the message counter
                producer.send("stats", stats)
                producer.flush()
                print(f"[{datetime.now(timezone.utc)}] Sent stats to Kafka: {stats}")
                

if __name__ == "__main__":
    asyncio.run(connect_ais_stream())
