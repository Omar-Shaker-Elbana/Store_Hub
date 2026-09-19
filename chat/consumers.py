import json
import time
from collections import deque

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

from .models import MAX_MESSAGE_LENGTH, DirectConversation, DirectMessage
from .permissions import can_chat_with
from .utils import direct_conversation_group_name, serialize_direct_message

MESSAGE_BURST = 10  # messages allowed ...
MESSAGE_WINDOW = 10.0  # ... within this many seconds
TYPING_INTERVAL = 2.0  # minimum seconds between forwarded typing events


class DirectChatConsumer(AsyncWebsocketConsumer):
    """Handles a single merchant's live connection to one 1-to-1 conversation.

    Text messages are created and broadcast entirely over the socket.
    Attachments are uploaded through the regular `send_direct_attachment`
    view (browsers don't deal well with multi-megabyte files inside a JSON
    websocket frame) - that view then calls `broadcast_direct_message` so
    the message still shows up live for both participants.
    """

    async def connect(self):
        self.user = self.scope["user"]
        self.conversation_id = self.scope["url_route"]["kwargs"]["conversation_id"]
        self.group_name = direct_conversation_group_name(self.conversation_id)
        self._message_times = deque(maxlen=MESSAGE_BURST)
        self._last_typing_sent = 0.0

        if not self.user or not self.user.is_authenticated:
            await self.close(code=4001)
            return

        if not await self._can_access_conversation():
            await self.close(code=4003)
            return

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive(self, text_data=None, bytes_data=None):
        if not text_data or len(text_data) > MAX_MESSAGE_LENGTH * 2:
            return

        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            return
        if not isinstance(data, dict):
            return

        event_type = data.get("type", "message")

        if event_type == "message":
            content = (data.get("content") or "").strip()[:MAX_MESSAGE_LENGTH]
            if not content:
                return
            if not self._allow_message():
                await self.send(
                    text_data=json.dumps(
                        {"type": "error", "error": "You're sending messages too fast."}
                    )
                )
                return
            message = await self._save_message(content)
            payload = await database_sync_to_async(serialize_direct_message)(message)
            await self.channel_layer.group_send(
                self.group_name,
                {"type": "chat_message", "message": payload},
            )

        elif event_type == "typing":
            now = time.monotonic()
            if now - self._last_typing_sent < TYPING_INTERVAL:
                return
            self._last_typing_sent = now
            await self.channel_layer.group_send(
                self.group_name,
                {
                    "type": "chat_typing",
                    "user_id": self.user.id,
                    "is_typing": bool(data.get("is_typing")),
                },
            )

        elif event_type == "read":
            await self._mark_messages_read()
            await self.channel_layer.group_send(
                self.group_name,
                {"type": "chat_read", "user_id": self.user.id},
            )

    # --- throttling ---------------------------------------------------------

    def _allow_message(self):
        now = time.monotonic()
        if (
            len(self._message_times) == MESSAGE_BURST
            and now - self._message_times[0] < MESSAGE_WINDOW
        ):
            return False
        self._message_times.append(now)
        return True

    # --- group event handlers -> pushed down to this socket ---------------

    async def chat_message(self, event):
        await self.send(
            text_data=json.dumps({"type": "message", "message": event["message"]})
        )

    async def chat_typing(self, event):
        if event["user_id"] == self.user.id:
            return  # don't echo our own typing indicator back to ourselves
        await self.send(
            text_data=json.dumps(
                {
                    "type": "typing",
                    "user_id": event["user_id"],
                    "is_typing": event["is_typing"],
                }
            )
        )

    async def chat_read(self, event):
        await self.send(
            text_data=json.dumps({"type": "read", "user_id": event["user_id"]})
        )

    # --- DB helpers ---------------------------------------------------------

    @database_sync_to_async
    def _can_access_conversation(self):
        conversation = DirectConversation.objects.filter(
            pk=self.conversation_id
        ).first()
        if not conversation or not conversation.has_participant(self.user):
            return False
        return can_chat_with(self.user, conversation.other_participant(self.user))

    @database_sync_to_async
    def _save_message(self, content):
        conversation = DirectConversation.objects.get(pk=self.conversation_id)
        message = DirectMessage.objects.create(
            conversation=conversation, sender=self.user, content=content
        )
        conversation.touch()
        return message

    @database_sync_to_async
    def _mark_messages_read(self):
        DirectMessage.objects.filter(
            conversation_id=self.conversation_id
        ).mark_read_for(self.user)
