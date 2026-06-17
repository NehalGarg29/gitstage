import asyncio
import json
import logging
from typing import Dict, Set

logger = logging.getLogger(__name__)

class InMemoryPubSub:
    """
    Lightweight, in-memory, thread-safe asynchronous PubSub system 
    designed to coordinate WebSocket progress updates from background task threads.
    """
    def __init__(self):
        # Maps a channel name to a set of active subscriber asyncio.Queues
        self.subscribers: Dict[str, Set[asyncio.Queue]] = {}

    def subscribe(self, channel: str) -> asyncio.Queue:
        """Subscribes to a channel and returns an asyncio.Queue to receive messages."""
        queue = asyncio.Queue()
        if channel not in self.subscribers:
            self.subscribers[channel] = set()
        self.subscribers[channel].add(queue)
        logger.debug(f"Subscribed to channel '{channel}'. Active subscribers: {len(self.subscribers[channel])}")
        return queue

    def unsubscribe(self, channel: str, queue: asyncio.Queue):
        """Unsubscribes a queue from a channel."""
        if channel in self.subscribers:
            self.subscribers[channel].discard(queue)
            if not self.subscribers[channel]:
                del self.subscribers[channel]
            logger.debug(f"Unsubscribed from channel '{channel}'. Remaining subscribers: {len(self.subscribers.get(channel, []))}")

    def publish(self, channel: str, message: str):
        """
        Publishes a message to all active subscribers of a channel.
        Must be run on the main event loop thread. If calling from another thread,
        use loop.call_soon_threadsafe(pubsub_manager.publish, channel, message).
        """
        if channel in self.subscribers:
            for queue in list(self.subscribers[channel]):
                try:
                    queue.put_nowait(message)
                except Exception as e:
                    logger.warning(f"Error putting message into subscriber queue: {e}")

# Singleton pubsub manager instance
pubsub_manager = InMemoryPubSub()
