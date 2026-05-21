import asyncio
import logging
import ssl
import websockets
import json
from datetime import datetime, timezone
from kafka import KafkaProducer
import os

API_Key = os.getenv("API_KEY")
MONITORING_TOPIC = os.getenv("AIS_PRODUCER_MONITORING_TOPIC", "statistics_ais_producer")
KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "host.docker.internal:9093")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger(os.path.splitext(os.path.basename(__file__))[0])

## unsecure ssl approach - TESTING ONLY
ssl_context = ssl.SSLContext(protocol=ssl.PROTOCOL_TLS_CLIENT)
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE

async def connect_ais_stream():

    async with websockets.connect("wss://stream.aisstream.io/v0/stream", ping_timeout=60, ssl=ssl_context) as websocket:
        
        message_counter = 0
        message_counter_stats_interval = 10000
        message_counter_start = 0
        timestamp_start = datetime.now(timezone.utc)

        # Connect to Kafka broker running in Docker
        producer = KafkaProducer(
            bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
            value_serializer=lambda v: json.dumps(v).encode('utf-8')
        )
        
        # subscribe async to the AIS stream with the API key and bounding box
        subscribe_message = {"APIKey": API_Key, "BoundingBoxes": [[[-90, -180], [90, 180]]]}
        subscribe_message_json = json.dumps(subscribe_message)
        log.info(f"Subscribing to AIS stream with message: {subscribe_message_json}")        
        await websocket.send(subscribe_message_json)

        async for message_json in websocket:
            message = json.loads(message_json)            
            producer.send(message["MessageType"], message)  # Send the raw JSON message to Kafka
            producer.flush()  # Ensure the message is sent to Kafka

            # some load statistics
            if message_counter == 0:
                timestamp_start = datetime.now(timezone.utc)
                log.info(f"Connected to AIS stream, starting to process messages...")                      
                          
            message_counter += 1

            if message_counter % message_counter_stats_interval == 0:
                log.info(f"Processed {message_counter} messages")                
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
                producer.send(MONITORING_TOPIC, stats)
                producer.flush()
                log.info(f"Sent stats to Kafka, current message rate: {stats['interval_message_rate']}")
                

if __name__ == "__main__":
    asyncio.run(connect_ais_stream())
